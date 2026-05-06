#!/bin/bash
cd /root/bingx-bot

echo "=== 1. ВРЕМЯ С МОМЕНТА РОТАЦИЙ ==="
python3 -c "
from datetime import datetime, timezone
start = datetime(2026, 5, 1, 19, 42, tzinfo=timezone.utc)
now = datetime.now(timezone.utc)
hours = (now - start).total_seconds() / 3600
print(f'Прошло: {hours:.1f}ч с момента ротаций bot4/bot5/bot7')
"

echo ""
echo "=== 2. СОСТОЯНИЕ ВСЕХ 7 БОТОВ ==="
python3 << 'PY'
import sys; sys.path.insert(0, '.')
import importlib

total_funding = 0
total_unrealized = 0
total_notional = 0
print(f"{'Бот':<6} {'Пара':<14} {'Spot$':>7} {'Funding':>9} {'PnL':>8} {'Netto':>8} {'Bad':>4}")
print("-" * 65)

for i in ['', '2', '3', '4', '5', '6', '7']:
    mod = importlib.import_module(f'arb_bot{i}')
    bot_num = i if i else '1'
    spot_qty = mod.get_spot_token() or 0
    pos = mod.get_perp_position()
    mark = mod.get_mark_price() or 0
    notional = spot_qty * mark
    
    # Загружаем state
    try:
        state = mod.load_state()
        funding = float(state.get('total_funding_earned', 0) or 0)
        bad = state.get('bad_periods', 0)
    except:
        funding = 0
        bad = 0
    
    perp_pnl = float(pos.get('unrealizedProfit', 0) or 0) if pos else 0
    spot_basis_pnl = 0
    if pos and notional > 5:
        avg = float(pos.get('avgPrice', 0) or 0)
        # spot эквивалент: текущая_цена - цена_входа (приближение)
        # Для дельта-нейтральной: netto ≈ funding - basis_drift
    netto = funding + perp_pnl  # упрощённо
    
    total_funding += funding
    total_unrealized += perp_pnl
    total_notional += notional
    
    print(f"bot{bot_num:<3} {mod.SYMBOL:<14} ${notional:>6.0f} ${funding:>7.3f} ${perp_pnl:>+6.2f} ${netto:>+6.2f} {bad}/5")

print("-" * 65)
print(f"{'ИТОГО':<6} {'':<14} ${total_notional:>6.0f} ${total_funding:>7.3f} ${total_unrealized:>+6.2f}")
PY

echo ""
echo "=== 3. ПОСЛЕДНИЕ ВЫПЛАТЫ ФАНДИНГА (24ч, через API) ==="
python3 << 'PY'
import sys; sys.path.insert(0, '.')
import arb_bot as b
import requests, hmac, hashlib, time
from urllib.parse import urlencode

# Funding income за 24 часа
since = int((time.time() - 86400) * 1000)
ts = int(time.time()*1000)
params = {'incomeType':'FUNDING_FEE','startTime':since,'limit':1000,'timestamp':ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.get(f'https://open-api.bingx.com/openApi/swap/v2/user/income?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10).json()

by_symbol = {}
total = 0
count = 0
for inc in r.get('data', []):
    sym = inc.get('symbol', '?')
    amt = float(inc.get('income', 0))
    by_symbol[sym] = by_symbol.get(sym, 0) + amt
    total += amt
    count += 1

print(f"Всего выплат за 24ч: {count}")
print(f"{'Пара':<16} {'Funding':>10}")
for s, a in sorted(by_symbol.items(), key=lambda x: -x[1]):
    print(f"  {s:<14} ${a:>+8.4f}")
print(f"  {'ИТОГО':<14} ${total:>+8.4f}")
print(f"")
print(f"Прогноз/мес: ${total * 30:.2f}  (если ставки сохранятся)")
PY

echo ""
echo "=== 4. БАЛАНСЫ И КАПИТАЛ ==="
python3 << 'PY'
import sys; sys.path.insert(0, '.')
import arb_bot as b
import requests, hmac, hashlib, time
from urllib.parse import urlencode

spot_u = b.get_spot_usdt()
perp_u = b.get_futures_usdt()

# Полный perp equity (margin + unrealized)
ts = int(time.time()*1000)
params = {'timestamp': ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.get(f'https://open-api.bingx.com/openApi/swap/v2/user/balance?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10).json()
perp_eq = float(r.get('data', {}).get('balance', {}).get('equity', 0))

# Spot tokens value
ts = int(time.time()*1000)
qs = urlencode({'timestamp': ts})
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.get(f'https://open-api.bingx.com/openApi/spot/v1/account/balance?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10).json()
spot_tokens_value = 0
for bl in r.get('data', {}).get('balances', []):
    free = float(bl.get('free', 0))
    if free > 0.0001 and bl.get('asset') != 'USDT':
        try:
            sym = f"{bl['asset']}-USDT"
            pr = requests.get(f'https://open-api.bingx.com/openApi/spot/v1/ticker/24hr?symbol={sym}', timeout=5).json()
            price = float(pr.get('data', [{}])[0].get('lastPrice', 0))
            spot_tokens_value += free * price
        except: pass

total_equity = spot_u + spot_tokens_value + perp_eq
print(f"Spot USDT:        ${spot_u:.2f}")
print(f"Spot tokens:      ${spot_tokens_value:.2f}  (захеджированы на perp short)")
print(f"Perp equity:      ${perp_eq:.2f}  (включая ваши ~$100 ручных)")
print(f"")
print(f"TOTAL EQUITY:     ${total_equity:.2f}")
print(f"Вчера было:       $1398.02 (+ ваши $100 пришли = $1498)")
print(f"Дельта:           ${total_equity - 1498:+.2f}")
PY

echo ""
echo "=== 5. AUTO_REBALANCE АКТИВНОСТЬ ==="
tail -10 auto_rebalance.log 2>/dev/null || echo "(нет лога)"

echo ""
echo "=== 6. WATCHDOG/PAUSE АКТИВНОСТЬ ==="
ls -la pause_bot* state/pause_bot* 2>/dev/null || echo "Нет паузных файлов ✅"
echo ""
echo "Последние watchdog алерты:"
tail -5 state/watchdog_alerts.json 2>/dev/null

echo ""
echo "=== 7. ROTATION ЛОГИ (должны быть пустые — отключили) ==="
ls -la rotation.log 2>/dev/null && tail -5 rotation.log 2>/dev/null || echo "rotation.log пустой ✅"

echo ""
echo "=== ГОТОВО ==="
