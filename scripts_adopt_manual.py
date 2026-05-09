"""
Adopt manual positions: ставит SL, регистрирует в active_positions для monitor+chandelier.
DRY RUN по умолчанию — пишет план. С аргументом "exec" — реально ставит.
"""
import sys, time, sqlite3
from assistant.core import exchange as ex
from assistant.core import journal
from assistant.signals.execute import get_contract, round_to
from assistant.analysis.smc import find_swings

DRY = not (len(sys.argv) > 1 and sys.argv[1] == "exec")
CHAT_ID = 729951023

def fetch_klines(sym, interval='15m', limit=200):
    r = ex.get_klines(sym, interval, limit).get('data') or []
    K = []
    for k in r:
        try:
            K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'t':int(k['time'])})
        except: pass
    return sorted(K, key=lambda x: x['t'])

def atr14(K):
    if len(K) < 15: return 0
    trs = [max(K[i]['h']-K[i]['l'], abs(K[i]['h']-K[i-1]['c']), abs(K[i]['l']-K[i-1]['c']))
           for i in range(len(K)-14, len(K))]
    return sum(trs)/14

def calc_struct_sl(sym, direction, entry):
    """SL = последний swing low (LONG) / swing high (SHORT) на 15m, минус 0.3 ATR safety."""
    K = fetch_klines(sym, '15m', 200)
    if len(K) < 50:
        return None, None
    sw = find_swings(K, left=2, right=2)
    a = atr14(K)
    if direction == 'LONG':
        lows = [s for s in sw if s[2] == 'L' and s[1] < entry]
        if not lows: return None, a
        last_low = lows[-1][1]
        sl = last_low - 0.3 * a
        return sl, a
    else:
        highs = [s for s in sw if s[2] == 'H' and s[1] > entry]
        if not highs: return None, a
        last_high = highs[-1][1]
        sl = last_high + 0.3 * a
        return sl, a

# 1) забираем позиции с биржи
r = ex.get_positions()
positions = []
if r.get('code') == 0:
    for d in r.get('data', []):
        amt = float(d.get('positionAmt', 0))
        if amt == 0: continue
        positions.append({
            'symbol': d['symbol'],
            'direction': d.get('positionSide'),
            'qty': abs(amt),
            'entry': float(d.get('avgPrice', 0)),
            'mark': float(d.get('markPrice', 0)),
        })

if not positions:
    print("нет открытых позиций"); sys.exit(0)

# 2) уже зарегистрированы?
already = journal.load_all_active()

# 3) проверим какие ордера уже стоят
r_orders = ex.request("GET", "/openApi/swap/v2/trade/openOrders", {}, auth=True)
existing_sl = {}  # symbol -> orderId
if r_orders.get('code') == 0:
    for o in r_orders.get('data', {}).get('orders', []):
        if 'STOP_MARKET' in o.get('type','') and 'TAKE_PROFIT' not in o.get('type',''):
            existing_sl[o['symbol']] = o.get('orderId')

print(f"=== ADOPT PLAN (DRY={DRY}) ===")
plans = []
for p in positions:
    sym = p['symbol']
    if sym in already:
        print(f"  {sym}: УЖЕ В ACTIVE — пропуск"); continue
    sl, atr = calc_struct_sl(sym, p['direction'], p['entry'])
    if not sl:
        print(f"  {sym}: не смог определить структурный SL — пропуск"); continue
    R = abs(p['entry'] - sl)
    R_pct = R / p['entry'] * 100
    risk_R = (p['mark'] - sl) / R if p['direction']=='LONG' else (sl - p['mark']) / R
    pnl_R = (p['mark'] - p['entry']) / R if p['direction']=='LONG' else (p['entry'] - p['mark']) / R
    c_info = get_contract(sym)
    pp = int(c_info.get("pricePrecision", 6))
    sl_r = round_to(sl, pp)
    plan = {
        'sym': sym, 'dir': p['direction'], 'qty': p['qty'],
        'entry': p['entry'], 'mark': p['mark'],
        'sl': sl_r, 'R_abs': R, 'R_pct': R_pct,
        'cur_R': pnl_R, 'atr': atr,
        'has_sl_order': sym in existing_sl,
    }
    plans.append(plan)
    print(f"  {sym} {p['direction']}:")
    print(f"    entry={p['entry']:.4f} mark={p['mark']:.4f}")
    print(f"    SL={sl_r:.4f}  R={R:.4f} ({R_pct:.2f}%)")
    print(f"    текущий PnL = {pnl_R:+.2f}R")
    print(f"    SL-ордер на бирже: {'ЕСТЬ '+str(existing_sl[sym]) if plan['has_sl_order'] else 'НЕТ'}")

if DRY:
    print("\n--- DRY RUN. Запусти с 'exec' для постановки SL и регистрации ---")
    sys.exit(0)

# 4) EXECUTE
print("\n=== EXECUTE ===")
for plan in plans:
    sym = plan['sym']; d = plan['dir']
    pos_side = "LONG" if d == "LONG" else "SHORT"
    sl_side = "SELL" if d == "LONG" else "BUY"

    # 4a) если SL уже есть — отменяем (возможно от ручной постановки на другой уровень)
    if plan['has_sl_order']:
        print(f"  {sym}: отменяю старый SL {existing_sl[sym]}")
        ex.request("DELETE", "/openApi/swap/v2/trade/order",
                   {"symbol": sym, "orderId": existing_sl[sym]}, auth=True)

    # 4b) ставим новый SL
    rr = ex.place_order(
        symbol=sym, side=sl_side, positionSide=pos_side,
        type="STOP_MARKET", stopPrice=plan['sl'], quantity=plan['qty'],
        workingType="MARK_PRICE"
    )
    if rr.get('code') != 0:
        print(f"  {sym}: SL ошибка {rr}")
        continue
    sl_oid = rr.get('data', {}).get('order', {}).get('orderId')
    print(f"  {sym}: SL поставлен @ {plan['sl']} (orderId={sl_oid})")

    # 4c) регистрируем в active_positions
    pos = {
        'direction': d,
        'entry': plan['entry'],
        'sl': plan['sl'],
        'tp': 0,  # без TP — chandelier ведёт
        'be': plan['entry'],  # BE = entry
        'qty': plan['qty'],
        'sl_order_id': sl_oid,
        'be_done': False,
        'chat_id': CHAT_ID,
        'opened_ts': int(time.time()),
        'trade_id': None,
        'setup_tag': 'adopted_manual',
        'auto': False,
    }
    journal.save_active(sym, pos)
    # сохраним r_init вручную через update_active (для chandelier)
    # r_init = расстояние от entry до первоначального SL
    try:
        # добавим колонку если нет
        with sqlite3.connect(journal.DB_PATH if hasattr(journal,'DB_PATH') else '/root/bingx-bot/journal.db') as c:
            try: c.execute("ALTER TABLE active_positions ADD COLUMN r_init REAL")
            except: pass
            c.execute("UPDATE active_positions SET r_init=? WHERE symbol=?", (plan['R_abs'], sym))
    except Exception as e:
        print(f"  {sym}: r_init save warn: {e}")
    print(f"  {sym}: зарегистрирован в active_positions")

print("\n=== ГОТОВО. Перезапусти бота чтобы ACTIVE подхватился из БД ===")
print("tmux kill-session -t bot; tmux new -d -s bot 'cd /root/bingx-bot && while true; do PYTHONUNBUFFERED=1 python3 -m assistant.interface.telegram_bot >> /tmp/assistant.log 2>&1; sleep 30; done'")
