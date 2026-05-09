import asyncio, time
import time
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from ..core import auto_state
from ..core.config import TG_TOKEN, TG_CHAT_ID
from ..core import exchange as ex
from ..collectors.market import get_universe, get_majors
from ..analysis import scoring
from ..analysis.scoring import score_major
from ..signals.trade_calc import calc_trade
from ..signals.execute import open_trade
from ..core import exchange as ex2
from ..core import journal

from aiogram.client.session.aiohttp import AiohttpSession
_tg_session = AiohttpSession(timeout=60)
bot = Bot(session=_tg_session, token=TG_TOKEN)
dp = Dispatcher()
PLANS = {}
ACTIVE = {}  # symbol -> {dir, entry, sl, tp, be, sl_order_id, be_done}

MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сканировать"), KeyboardButton(text="Позиции")],
        [KeyboardButton(text="Активные"), KeyboardButton(text="Журнал")],
        [KeyboardButton(text="Статистика"), KeyboardButton(text="Баланс")],
        [KeyboardButton(text="Импорт позиций"), KeyboardButton(text="Авто"), KeyboardButton(text="Режим")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)




def get_balance_usdt():
    r = ex.get_balance()
    if r.get("code") != 0:
        return 0.0
    for a in r.get("data", []):
        if a.get("asset") == "USDT":
            return float(a.get("availableMargin", 0))
    return 0.0


def fmt_plan(p):
    flag = f" [{p['flag']}]" if p.get('flag') else ""
    smc = p.get('smc_type')
    setup_line = f"setup: {smc}" if smc else "setup: tech only"
    lines = [
        f"[{p['direction']}]{flag} {p['symbol']}",
        f"score: {p['score']}  {setup_line}",
        f"ATR: {p['atr_pct']}%",
        f"вход:    {p['entry']}",
        f"уровень: {p['structure']} (за ним стопы)",
        f"SL:      {p['sl']}  ({p['sl_pct']}%)",
        f"TP:      {p['tp']}  (RR {p['rr']})",
        f"BE:      {p['breakeven']} (после +1R)",
        f"плечо: {p['leverage']}x   qty: {p['qty']}",
        f"маржа: ${p['margin_usd']}   ком.: ${p['commission_usd']}",
        f"",
        f"+ профит: ${p['profit_usd']} ({p['profit_pct_margin']}% маржи)",
        f"- убыток: ${p['loss_usd']} ({p['loss_pct_margin']}% маржи)",
        f"~длительность: {p['duration_min']} мин",
    ]
    return chr(10).join(lines)


@dp.message(Command("ping"))
async def ping(m: Message):
    await m.answer("понг")


@dp.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer("Готов. Используй кнопки внизу.", reply_markup=MAIN_KB)


@dp.message(F.text == "Сканировать")
async def btn_scan(m: Message):
    await cmd_scan(m)


@dp.message(F.text == "Позиции")
async def btn_pos(m: Message):
    await cmd_pos(m)


@dp.message(F.text == "Активные")
async def btn_active(m: Message):
    await cmd_active(m)


@dp.message(F.text == "Журнал")
async def btn_journal(m: Message):
    await cmd_journal(m)


@dp.message(F.text == "Статистика")
async def btn_stats(m: Message):
    await cmd_stats(m)


@dp.message(F.text == "Баланс")
async def btn_bal(m: Message):
    await cmd_bal(m)


@dp.message(F.text == "Импорт позиций")
async def btn_import(m: Message):
    await cmd_import(m)


@dp.message(Command("bal"))
async def cmd_bal(m: Message):
    bal = get_balance_usdt()
    await m.answer(f"USDT (perp): ${bal:.2f}")


@dp.message(Command("scan"))
async def cmd_scan(m: Message):
    await m.answer("Сканирую: топ-100 альтов (15m) + крупняки (1h)...")
    bal = get_balance_usdt()

    # 1) Альты
    uni = get_universe(limit=100)
    sigs = []
    for row in uni:
        try:
            s = scoring.score_candidate(row)
            if s and s["score"] >= 30:
                sigs.append(s)
        except Exception:
            pass
        await asyncio.sleep(0.05)
    sigs.sort(key=lambda x: -x["score"])

    # 2) Мажоры
    majors_rows = get_majors()
    majors_sigs = []
    for row in majors_rows:
        try:
            s = score_major(row)
            if s and s["score"] >= 25:
                majors_sigs.append(s)
        except Exception:
            pass
        await asyncio.sleep(0.05)
    majors_sigs.sort(key=lambda x: -x["score"])

    if not sigs and not majors_sigs:
        await m.answer("Ничего не прошло пороги. Рынок плоский.")
        return

    await m.answer(f"Альты: {len(sigs)} | Крупняки: {len(majors_sigs)} | Баланс: ${bal:.2f}")

    # 3) Альты — топ-10
    sent = 0
    rejected = []
    if sigs:
        await m.answer("=== АЛЬТЫ (15m) ===")
    for s in sigs[:10]:
        p_ = calc_trade(s, bal if bal > 10 else 100)
        if "error" in p_:
            rejected.append(f"{s['symbol']}: {p_['error']}")
            try:
                journal.log_rejection(s['symbol'], None, p_['error'], score=s.get('score'))
            except Exception: pass
            continue
        if not p_.get("quality_ok", True):
            rejected.append(f"{s['symbol']}: {p_.get('quality_reason','quality fail')}")
            try:
                journal.log_rejection(s['symbol'], p_.get('direction'), p_.get('quality_reason','quality fail'),
                                      score=s.get('score'), entry=p_.get('entry'), sl=p_.get('sl'), tp=p_.get('tp'),
                                      info=p_.get('quality_info'))
            except Exception: pass
            continue
            continue
        plan_id = f"{p_['symbol']}_{int(time.time())}"
        PLANS[plan_id] = p_
        sent += 1
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть сделку", callback_data=f"open:{plan_id}"),
            InlineKeyboardButton(text="Пропустить", callback_data=f"skip:{plan_id}"),
        ]])
        await m.answer(fmt_plan(p_), reply_markup=kb)

    # 4) Крупняки — отдельным блоком
    if majors_sigs:
        await m.answer("=== КРУПНЯКИ (1h, длинные сделки) ===")
    for s in majors_sigs:
        p_ = calc_trade(s, bal if bal > 10 else 100)
        if "error" in p_:
            rejected.append(f"{s['symbol']} (major): {p_['error']}")
            try:
                journal.log_rejection(s['symbol'], None, p_['error'], score=s.get('score'))
            except Exception: pass
            continue
        if not p_.get("quality_ok", True):
            rejected.append(f"{s['symbol']} (major): {p_.get('quality_reason','quality fail')}")
            try:
                journal.log_rejection(s['symbol'], p_.get('direction'), p_.get('quality_reason','quality fail'),
                                      score=s.get('score'), entry=p_.get('entry'), sl=p_.get('sl'), tp=p_.get('tp'),
                                      info=p_.get('quality_info'))
            except Exception: pass
            continue
            continue
        plan_id = f"{p_['symbol']}_{int(time.time())}"
        PLANS[plan_id] = p_
        sent += 1
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть сделку", callback_data=f"open:{plan_id}"),
            InlineKeyboardButton(text="Пропустить", callback_data=f"skip:{plan_id}"),
        ]])
        await m.answer(fmt_plan(p_), reply_markup=kb)

    if sent == 0:
        await m.answer("Все кандидаты отбракованы:" + chr(10) + chr(10).join(rejected))
    elif rejected:
        await m.answer(f"Отбраковано {len(rejected)}:" + chr(10) + chr(10).join(rejected))




@dp.message(Command("active"))
async def cmd_active(m: Message):
    from ..core import journal, exchange
    rows = journal.load_all_active()
    if not rows:
        await m.answer("нет активных")
        return
    lines = []
    now = time.time()
    for sym, p in rows.items():
        try:
            t = exchange.get_ticker(sym)
            d = t.get("data") if isinstance(t, dict) else None
            if isinstance(d, list): d = d[0] if d else {}
            d = d or {}
            px = float(d.get("lastPrice") or d.get("last") or d.get("close") or p["entry"])
        except Exception:
            px = p["entry"]
        side = 1 if p["direction"] == "LONG" else -1
        pnl_pct = side * (px - p["entry"]) / p["entry"] * 100
        pnl_usd = side * (px - p["entry"]) * p["qty"]
        rng = abs(p["tp"] - p["entry"])
        done = abs(px - p["entry"]) / rng * 100 if rng > 0 else 0
        done = max(0, min(100, done))
        elapsed = int((now - p["opened_ts"]) / 60) if p.get("opened_ts") else 0
        be = " BE" if p.get("be_done") else ""
        lines.append(
            f"{sym} {p['direction']}{be}\n"
            f"  вход {p['entry']}  сейчас {px}\n"
            f"  PnL {pnl_usd:+.2f}$ ({pnl_pct:+.2f}%)\n"
            f"  до TP {done:.0f}%  | {elapsed} мин в позиции\n"
            f"  SL {p['sl']}  TP {p['tp']}  qty {p['qty']}"
        )
    await m.answer("\n\n".join(lines))


@dp.message(Command("journal"))
async def cmd_journal(m: Message):
    from ..core import journal
    rows = journal.get_recent(10)
    if not rows:
        await m.answer("журнал пуст")
        return
    out = []
    for r in rows:
        sign = "+" if (r.get("pnl") or 0) >= 0 else ""
        out.append(f"{r['symbol']} {r['direction']} {sign}{r.get('pnl',0):.2f}$  {r.get('tag','')}")
    await m.answer("\n".join(out))


@dp.message(Command("stats"))
async def cmd_stats(m: Message):
    from ..core import journal
    s = journal.get_stats()
    if not s or s.get("total", 0) == 0:
        await m.answer("статистики пока нет")
        return
    txt = f"всего: {s['total']}  WR: {s.get('win_rate',0):.0f}%  PnL: {s.get('pnl_total',0):+.2f}$  fees: {s.get('fees_total',0):.2f}$"
    ext = journal.get_stats_extended() or {}
    if ext:
        txt += "\n\nпо сетапам:"
        for tag, d in ext.items():
            txt += (f"\n  {tag}:  n={d['n']}  WR={d['wr']:.0f}%  "
                    f"PnL={d['pnl']:+.2f}$\n"
                    f"     avg {d['avg_duration_min']:.0f} мин  "
                    f"дошли до +1R: {d['reached_1R_pct']:.0f}%")
    await m.answer(txt)


@dp.message(Command("import"))
async def cmd_import(m: Message):
    from ..core import exchange, journal
    try:
        positions = exchange.get_positions()
    except Exception as e:
        await m.answer(f"err: {e}")
        return
    if not positions:
        await m.answer("на бирже нет позиций")
        return
    n = 0
    for pos in positions:
        sym = pos["symbol"]
        if journal.get_active(sym):
            continue
        side = pos["direction"]
        entry = float(pos["entry"])
        qty = float(pos["qty"])
        sl = entry * (0.97 if side == "LONG" else 1.03)
        tp = entry * (1.06 if side == "LONG" else 0.94)
        journal.save_active(sym, {
            "direction": side, "entry": entry, "sl": sl, "tp": tp,
            "be": entry, "qty": qty, "sl_order_id": None, "be_done": 0,
            "chat_id": m.chat.id, "opened_ts": time.time(),
            "trade_id": None, "setup_tag": "manual"
        })
        n += 1
    await m.answer(f"импортировано {n}. SL ±3%, TP ±6% (правь /setsl /settp)")


@dp.message(Command("setsl"))
async def cmd_setsl(m: Message):
    parts = m.text.split()
    if len(parts) < 3:
        await m.answer("/setsl SYMBOL PRICE"); return
    from ..core import journal
    sym = parts[1].upper()
    price = float(parts[2])
    if sym not in journal.load_all_active():
        await m.answer("нет такой активной"); return
    journal.update_active(sym, sl=price)
    await m.answer(f"{sym} SL = {price}")


@dp.message(Command("settp"))
async def cmd_settp(m: Message):
    parts = m.text.split()
    if len(parts) < 3:
        await m.answer("/settp SYMBOL PRICE"); return
    from ..core import journal
    sym = parts[1].upper()
    price = float(parts[2])
    if sym not in journal.load_all_active():
        await m.answer("нет такой активной"); return
    journal.update_active(sym, tp=price)
    await m.answer(f"{sym} TP = {price}")



@dp.message(Command("rejections"))
async def cmd_rejections(m: Message):
    rows = journal.rejection_stats(hours=24)
    if not rows:
        await m.answer("за 24ч отказов нет")
        return
    total = sum(r["count"] for r in rows)
    lines = [f"Отбраковок за 24ч: {total}", ""]
    for r in rows[:15]:
        pct = r["count"] / total * 100
        lines.append(f"  {r['count']:>3}  ({pct:>4.0f}%)  {r['reason']}")
    await m.answer("\n".join(lines))


@dp.message(Command("pos"))
async def cmd_pos(m: Message):
    r = ex.get_positions()
    if r.get("code") != 0:
        await m.answer(f"err: {r.get('msg', r)}")
        return
    rows = [d for d in r.get("data", []) if float(d.get("positionAmt", 0)) != 0]
    if not rows:
        await m.answer("Открытых позиций нет")
        return
    parts = ["Открытые позиции:\n"]
    for d in rows:
        sym = d["symbol"]
        side = d.get("positionSide", "?")
        amt = float(d["positionAmt"])
        avg = float(d.get("avgPrice", 0))
        upl = float(d.get("unrealizedProfit", 0))
        lev = d.get("leverage", "?")
        parts.append(f"{sym} {side} qty={amt} @ {avg}\n  PnL: ${upl:+.2f}  lev: {lev}x")
    await m.answer("\n\n".join(parts))


@dp.callback_query(F.data.startswith("skip:"))
async def cb_skip(c: CallbackQuery):
    plan_id = c.data.split(":", 1)[1]
    PLANS.pop(plan_id, None)
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.answer("Пропущено")


@dp.callback_query(F.data.startswith("open:"))
async def cb_open(c: CallbackQuery):
    plan_id = c.data.split(":", 1)[1]
    p = PLANS.get(plan_id)
    if not p:
        await c.answer("План истёк", show_alert=True)
        return
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    txt = (
        f"ПОДТВЕРЖДЕНИЕ\n\n"
        f"{p['symbol']} {p['direction']} qty={p['qty']} lev={p['leverage']}x\n"
        f"вход {p['entry']}, SL {p['sl']}, TP {p['tp']}\n"
        f"маржа ${p['margin_usd']}, риск ${p['risk_usd']}\n\n"
        f"Открыть сделку?"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="ПОЕХАЛИ", callback_data=f"go:{plan_id}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"skip:{plan_id}"),
    ]])
    await c.message.answer(txt, reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data.startswith("go:"))
async def cb_go(c: CallbackQuery):
    plan_id = c.data.split(":", 1)[1]
    p = PLANS.get(plan_id)
    if not p:
        await c.answer("План истёк", show_alert=True)
        return
    try:
        await c.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await c.message.answer(f"Открываю {p['symbol']} {p['direction']}...")
    res = open_trade(p)
    if not res.get("ok"):
        await c.message.answer(f"ОШИБКА на этапе {res['stage']}:\n{res['msg']}")
        await c.answer("Ошибка", show_alert=True)
        return
    msg = (
        f"СДЕЛКА ОТКРЫТА\n"
        f"{p['symbol']} {p['direction']} qty={res['rounded']['qty']} @ {p['entry']}\n"
        f"SL {res['rounded']['sl']}: {'OK' if res['sl_placed'] else 'НЕ ВЫСТАВЛЕН — выставь руками!'}\n"
        f"TP {res['rounded']['tp']}: {'OK' if res['tp_placed'] else 'НЕ ВЫСТАВЛЕН — выставь руками!'}\n"
        f"order_id: {res.get('entry_order_id')}"
    )
    if not res['sl_placed']:
        msg += f"\nSL err: {res['sl_msg']}"
    if not res['tp_placed']:
        msg += f"\nTP err: {res['tp_msg']}"
    sl_order_id = None
    if res.get('sl_msg', {}).get('data'):
        sl_order_id = res['sl_msg']['data'].get('order', {}).get('orderId')
    setup_tag = p.get('smc_type') or 'tech_only'
    trade_id = journal.journal_open(p['symbol'], p['direction'], p['entry'],
                                     res['rounded']['qty'], setup_tag)
    pos = {
        "direction": p['direction'],
        "entry": p['entry'],
        "sl": res['rounded']['sl'],
        "tp": res['rounded']['tp'],
        "be": p['breakeven'],
        "r_init": abs(p['entry'] - p['sl']),
        "trail_step": 0,
        "qty": res['rounded']['qty'],
        "sl_order_id": sl_order_id,
        "be_done": False,
        "chat_id": c.message.chat.id,
        "trade_id": trade_id,
        "setup_tag": setup_tag,
        "opened_ts": int(time.time()),
    }
    ACTIVE[p['symbol']] = pos
    journal.save_active(p['symbol'], pos)
    await c.message.answer(msg)
    PLANS.pop(plan_id, None)
    await c.answer("Открыто")



async def close_in_journal(sym, pos):
    """Позиция закрыта на бирже — определяем exit_price и пишем в журнал."""
    try:
        # последняя цена
        t = ex2.get_ticker(sym)
        exit_px = 0
        if t.get("code") == 0:
            data = t.get("data", [])
            row = data[0] if isinstance(data, list) and data else (data or {})
            exit_px = float(row.get("lastPrice", 0))

        entry = pos["entry"]
        qty = pos["qty"]
        if pos["direction"] == "LONG":
            pnl = (exit_px - entry) * qty
        else:
            pnl = (entry - exit_px) * qty
        # комиссии: 0.05% × notional × 2
        notional = entry * qty
        fees = notional * 0.0005 * 2

        # определяем причину закрытия
        sl = pos["sl"]
        tp = pos["tp"]
        be_moved = bool(pos.get("be_done"))
        if pos["direction"] == "LONG":
            if exit_px <= sl * 1.001:
                reason = "BE" if be_moved else "SL"
            elif exit_px >= tp * 0.999:
                reason = "TP"
            else:
                reason = "manual"
        else:
            if exit_px >= sl * 0.999:
                reason = "BE" if be_moved else "SL"
            elif exit_px <= tp * 1.001:
                reason = "TP"
            else:
                reason = "manual"

        if pos.get("trade_id"):
            journal.journal_close(pos["trade_id"], exit_px, pnl - fees, fees,
                                   be_moved, notes=reason)

        # отменяем висящие SL/TP ордера если есть
        try:
            ex2.cancel_all_orders(sym)
        except Exception:
            pass

        # AUTO: запись и проверка блока
        if pos.get("auto"):
            try:
                R = pos.get("r_init") or abs(entry - sl)
                pnl_R = (pnl - fees) / (R * pos.get("qty", 1)) if R > 0 and pos.get("qty") else 0
                auto_state.log_close(sym, pos["direction"], pnl_R, reason)
                last4 = auto_state.last_n_results(4)
                if len(last4) >= 4 and all(x < 0 for x in last4):
                    until = auto_state.block_24h("4 минуса подряд в авто")
                    await bot.send_message(pos["chat_id"],
                        f"⛔ AUTO заблокирован на 24ч (4 минуса подряд). /auto unblock — снять")
            except Exception as _e:
                print("auto-close hook err:", _e)

        ACTIVE.pop(sym, None)
        journal.remove_active(sym)

        emoji = "+" if pnl - fees > 0 else "-"
        await bot.send_message(
            pos["chat_id"],
            f"ЗАКРЫТА: {sym} {pos['direction']} ({reason})\n"
            f"вход {entry} → выход {exit_px}\n"
            f"PnL: ${pnl - fees:+.2f}  (комиссии ${fees:.2f})  [{emoji}]"
        )
    except Exception as e:
        print(f"close_in_journal err {sym}: {e}")


async def monitor_positions():
    """Каждые 30 сек проверяет позиции — если +1R, переставляет SL в БУ."""
    while True:
        try:
            # тянем все позиции с биржи одним запросом
            r_pos = ex2.get_positions()
            exch_positions = {}
            if r_pos.get("code") == 0:
                for d in r_pos.get("data", []):
                    if float(d.get("positionAmt", 0)) != 0:
                        exch_positions[d["symbol"]] = d

            for sym, pos in list(ACTIVE.items()):
                # если позиции на бирже нет — её закрыли (TP/SL/руками)
                if sym not in exch_positions:
                    await close_in_journal(sym, pos)
                    continue

                # CHANDELIER trailing: после +2R SL = max(high-22 на 15m) - 3*ATR(14).
                # Подтягиваем только вверх (LONG) / вниз (SHORT). Никогда не ослабляем.
                t = ex2.get_ticker(sym)
                if t.get("code") != 0:
                    continue
                data = t.get("data", [])
                row = data[0] if isinstance(data, list) else data
                px = float((row or {}).get("lastPrice", 0))
                if px == 0:
                    continue
                entry = pos["entry"]
                r_init = pos.get("r_init") or abs(entry - pos["sl"])
                if r_init <= 0:
                    continue
                if pos["direction"] == "LONG":
                    profit_R = (px - entry) / r_init
                else:
                    profit_R = (entry - px) / r_init
                if profit_R < 2.0:
                    continue
                # тянем 25 свечей 15m, считаем chandelier
                try:
                    rk = ex2.get_klines(sym, "15m", 30)
                    raw = rk.get("data") or []
                    K = []
                    for k in raw:
                        try:
                            K.append({"h": float(k["high"]), "l": float(k["low"]), "c": float(k["close"])})
                        except Exception:
                            pass
                    if len(K) < 23:
                        continue
                    # ATR(14)
                    trs = [max(K[i]["h"]-K[i]["l"], abs(K[i]["h"]-K[i-1]["c"]), abs(K[i]["l"]-K[i-1]["c"])) for i in range(1, len(K))]
                    a = sum(trs[-14:]) / 14 if len(trs) >= 14 else 0
                    if a <= 0:
                        continue
                    last22 = K[-22:]
                    if pos["direction"] == "LONG":
                        ch_sl = max(k["h"] for k in last22) - 3.0 * a
                        # тянем только вверх
                        if ch_sl <= pos["sl"]:
                            continue
                        # не выше текущей цены минус 0.2*ATR (sanity)
                        if ch_sl >= px - 0.2 * a:
                            ch_sl = px - 0.2 * a
                            if ch_sl <= pos["sl"]:
                                continue
                    else:
                        ch_sl = min(k["l"] for k in last22) + 3.0 * a
                        if ch_sl >= pos["sl"]:
                            continue
                        if ch_sl <= px + 0.2 * a:
                            ch_sl = px + 0.2 * a
                            if ch_sl >= pos["sl"]:
                                continue
                except Exception as e:
                    print(f"chandelier {sym} err: {e}")
                    continue
                # переставляем SL
                if pos.get("sl_order_id"):
                    ex2.request("DELETE", "/openApi/swap/v2/trade/order",
                                {"symbol": sym, "orderId": pos["sl_order_id"]}, auth=True)
                pos_side = "LONG" if pos["direction"] == "LONG" else "SHORT"
                sl_side = "SELL" if pos["direction"] == "LONG" else "BUY"
                from ..signals.execute import get_contract, round_to
                c_info = get_contract(sym)
                pp = int(c_info.get("pricePrecision", 6))
                new_sl_r = round_to(ch_sl, pp)
                r_new = ex2.place_order(
                    symbol=sym, side=sl_side, positionSide=pos_side,
                    type="STOP_MARKET", stopPrice=new_sl_r, quantity=pos["qty"],
                    workingType="MARK_PRICE"
                )
                if r_new.get("code") == 0:
                    pos["sl"] = new_sl_r
                    new_id = r_new.get("data", {}).get("order", {}).get("orderId")
                    pos["sl_order_id"] = new_id
                    journal.update_active(sym, sl=new_sl_r, sl_order_id=new_id)
                    await bot.send_message(
                        pos["chat_id"],
                        f"CHAND: {sym} +{profit_R:.1f}R. SL → {new_sl_r}"
                    )
                    continue
                # текущая цена
                t = ex2.get_ticker(sym)
                if t.get("code") != 0:
                    continue
                data = t.get("data", [])
                if not data:
                    continue
                row = data[0] if isinstance(data, list) else data
                px = float(row.get("lastPrice", 0))
                if px == 0:
                    continue

                entry = pos["entry"]
                sl = pos["sl"]
                # 1R = расстояние от entry до SL
                r_dist = abs(entry - sl)
                if pos["direction"] == "LONG":
                    profit = px - entry
                else:
                    profit = entry - px

                if profit >= r_dist:  # достигли +1R
                    # отменяем старый SL и ставим новый на BE
                    if pos.get("sl_order_id"):
                        ex2.request("DELETE", "/openApi/swap/v2/trade/order",
                                    {"symbol": sym, "orderId": pos["sl_order_id"]}, auth=True)
                    pos_side = "LONG" if pos["direction"] == "LONG" else "SHORT"
                    sl_side = "SELL" if pos["direction"] == "LONG" else "BUY"
                    # округление BE по precision
                    from ..signals.execute import get_contract, round_to
                    c_info = get_contract(sym)
                    pp = int(c_info.get("pricePrecision", 6))
                    be = round_to(pos["be"], pp)
                    r_new = ex2.place_order(
                        symbol=sym, side=sl_side, positionSide=pos_side,
                        type="STOP_MARKET", stopPrice=be, quantity=pos["qty"],
                        workingType="MARK_PRICE"
                    )
                    if r_new.get("code") == 0:
                        pos["be_done"] = True
                        pos["sl"] = be
                        new_id = r_new.get("data", {}).get("order", {}).get("orderId")
                        pos["sl_order_id"] = new_id
                        journal.update_active(sym, be_done=1, sl=be, sl_order_id=new_id)
                        if pos.get("trade_id"):
                            journal.journal_be_moved(pos["trade_id"])
                        await bot.send_message(
                            pos["chat_id"],
                            f"BE: {sym} {pos['direction']} достиг +1R. SL переставлен в безубыток ({be})."
                        )
                    else:
                        await bot.send_message(
                            pos["chat_id"],
                            f"BE FAIL для {sym}: {r_new.get('msg', r_new)}"
                        )
        except Exception as e:
            print(f"monitor err: {e}")
        await asyncio.sleep(30)



@dp.message(Command("regime"))
async def cmd_regime(m: Message):
    from ..core import config as _cfg
    if str(m.from_user.id) != str(_cfg.TG_CHAT_ID): return
    from ..analysis.scoring import scan as _scan
    from ..collectors.market import get_universe, get_majors
    from ..signals.trade_calc import calc_trade, ATR_MIN
    try:
        bal_resp = exchange.get_balance()
        bd = bal_resp.get("data") if isinstance(bal_resp, dict) else None
        if isinstance(bd, list): bd = bd[0] if bd else {}
        bal = float((bd or {}).get("balance") or (bd or {}).get("availableMargin") or 100)
    except Exception: bal = 100
    cands = (_scan(get_universe(limit=100), top_n=20) or []) + (_scan(get_majors(), top_n=20) or [])
    WL = {"BOS+OB+FVG", "CHoCH+OB+FVG"}
    atrs = []
    in_wl_pass = 0
    in_wl_total = 0
    rejected_atr = 0
    for s_ in cands:
        p_ = calc_trade(s_, bal if bal>10 else 100)
        atr = p_.get("atr_pct")
        if atr is not None: atrs.append(atr)
        smc = s_.get("detail",{}).get("smc","")
        if smc in WL:
            in_wl_total += 1
            if p_.get("quality_ok", True) and not p_.get("error"):
                in_wl_pass += 1
        if "ATR" in str(p_.get("error","")):
            rejected_atr += 1
    atrs_sorted = sorted(atrs)
    median = atrs_sorted[len(atrs_sorted)//2] if atrs_sorted else 0
    if median < 0.5: regime = "🔴 МЁРТВЫЙ"
    elif median < 1.0: regime = "🟡 НОРМАЛЬНЫЙ"
    elif median < 2.0: regime = "🟢 ГОРЯЧИЙ"
    else: regime = "🔥 ЭКСТРЕМАЛЬНЫЙ"
    msg = (f"📊 РЫНОК: {regime}\n\n"
           f"медиана ATR: {median:.2f}%\n"
           f"мин/макс: {min(atrs):.2f}% / {max(atrs):.2f}%\n" if atrs else f"📊 РЫНОК: {regime}\n\n")
    msg += (f"порог фильтра: {ATR_MIN}%\n"
            f"кандидатов всего: {len(cands)}\n"
            f"в WL (BOS/CHoCH+OB+FVG): {in_wl_total}\n"
            f"готовы к входу: {in_wl_pass}\n"
            f"отбраковано по ATR: {rejected_atr}")
    await m.answer(msg)

# ---------- AUTO-SCANNER ----------
_auto_seen = {}  # symbol -> ts

async def auto_scanner_loop():
    import asyncio, time, traceback
    from ..core import exchange
    from ..analysis.scoring import scan
    from ..collectors.market import get_universe, get_majors
    from ..signals.trade_calc import calc_trade
    from ..core import config as _cfg

    INTERVAL = 15 * 60     # 15 мин
    DEDUP_TTL = 60 * 60    # 1 час не повторять одну монету

    await asyncio.sleep(30)  # дать боту стартовать
    while True:
        try:
            uni = get_universe(limit=100)
            maj = get_majors()
            cand_alts = scan(uni, top_n=20) or []
            cand_maj = scan(maj, top_n=10) or []
            try:
                bal_resp = exchange.get_balance()
                bd = bal_resp.get("data") if isinstance(bal_resp, dict) else None
                if isinstance(bd, list): bd = bd[0] if bd else {}
                bal = float((bd or {}).get("balance") or (bd or {}).get("availableMargin") or 100)
            except Exception:
                bal = 100

            now = time.time()
            # очистка старых
            for sym in list(_auto_seen):
                if now - _auto_seen[sym] > DEDUP_TTL:
                    _auto_seen.pop(sym, None)

            hits = []
            print(f"[AUTO] цикл: alts={len(cand_alts)} maj={len(cand_maj)} bal={bal:.0f}", flush=True)
            for s_ in (cand_alts + cand_maj):
                sym = s_["symbol"]
                if sym in _auto_seen:
                    continue
                p_ = calc_trade(s_, bal if bal > 10 else 100)
                if "error" in p_ or not p_.get("quality_ok", True):
                    continue
                _auto_seen[sym] = now
                hits.append((s_, p_))
            print(f"[AUTO] hits: {len(hits)} (in WL: {sum(1 for s_,_ in hits if s_.get('detail',{}).get('smc','') in {'BOS+OB+FVG','CHoCH+OB+FVG'})})", flush=True)

            # ===== AUTO-OPEN =====
            try:
                if auto_state.is_enabled():
                    blk, _ = auto_state.is_blocked()
                    AUTO_DAILY_LIMIT = 4
                    today_count = auto_state.count_today_auto()
                    if blk:
                        pass
                    elif today_count >= AUTO_DAILY_LIMIT:
                        print(f"[AUTO] дневной лимит достигнут: {today_count}/{AUTO_DAILY_LIMIT}", flush=True)
                    else:
                        WHITELIST = {"BOS+OB+FVG", "CHoCH+OB+FVG"}
                        opened_auto = sum(1 for pp in ACTIVE.values() if pp.get("auto"))
                        for s_, p_ in hits:
                            if today_count >= AUTO_DAILY_LIMIT:
                                print(f"[AUTO] лимит дня {AUTO_DAILY_LIMIT} достигнут — стоп", flush=True)
                                break
                            if opened_auto >= 5: break
                            if s_["symbol"] in ACTIVE: continue
                            if s_.get("score", 0) < 60: continue
                            tag = s_.get("detail", {}).get("smc", "")
                            if tag not in WHITELIST: continue
                            # КРИТИЧНО: проверка quality перед открытием
                            if not p_.get("quality_ok", False):
                                print(f"[AUTO] {p_['symbol']} отказ: {p_.get('quality_reason','?')}", flush=True)
                                try:
                                    journal.log_rejection(p_['symbol'], p_['direction'],
                                        p_.get('quality_reason','?'), s_.get('score'),
                                        p_.get('entry'), p_.get('sl'), p_.get('tp'),
                                        p_.get('quality_info'))
                                except: pass
                                continue
                            # ДОПОЛНИТЕЛЬНО: жёсткий финальный RR ≥ 1.5 на фактических entry/sl/tp
                            _R = abs(p_['entry'] - p_['sl'])
                            _D = abs(p_['tp'] - p_['entry'])
                            _final_rr = _D / _R if _R > 0 else 0
                            if _final_rr < 1.7:
                                print(f"[AUTO] {p_['symbol']} отказ: финальный RR={_final_rr:.2f} < 1.7", flush=True)
                                try:
                                    journal.log_rejection(p_['symbol'], p_['direction'],
                                        f"финальный RR={_final_rr:.2f}<1.7", s_.get('score'),
                                        p_.get('entry'), p_.get('sl'), p_.get('tp'), None)
                                except: pass
                                continue
                            # открываем
                            plan = {
                                "symbol": p_["symbol"], "direction": p_["direction"],
                                "entry": p_["entry"], "sl": p_["sl"], "tp": p_["tp"],
                                "breakeven": p_["breakeven"], "qty": p_["qty"],
                                "leverage": p_["leverage"], "smc_type": tag,
                            }
                            res = open_trade(plan)
                            if not res.get("ok"):
                                err_text = f"AUTO: {p_['symbol']} ошибка stage={res.get('stage')} msg={res.get('msg')} full={res}"
                                print(err_text, flush=True)
                                await bot.send_message(int(_cfg.TG_CHAT_ID), err_text[:400])
                                continue
                            # ДОПОЛНИТЕЛЬНАЯ ЗАЩИТА: если SL не выставлен — позиция уже закрыта в execute, пропускаем
                            if not res.get("sl_placed"):
                                await bot.send_message(int(_cfg.TG_CHAT_ID),
                                    f"⚠️ AUTO: {p_['symbol']} SL не выставлен, позиция закрыта аварийно")
                                continue
                            sl_order_id = None
                            if isinstance(res.get('sl_msg'), dict) and res['sl_msg'].get('data'):
                                sl_order_id = res['sl_msg']['data'].get('order', {}).get('orderId')
                            trade_id = journal.journal_open(
                                p_["symbol"], p_["direction"],
                                p_["entry"], res["rounded"]["qty"], tag,
                                sl=res["rounded"]["sl"], tp=res["rounded"]["tp"],
                                score=s_.get("score"),
                                adj_rr=(p_.get("quality_info") or {}).get("adjusted_rr"),
                                ch24=s_.get("change_24h"),
                                atr_pct=p_.get("atr_pct"),
                                quality_info=p_.get("quality_info"))
                            ACTIVE[p_["symbol"]] = {
                                "direction": p_["direction"], "entry": p_["entry"],
                                "sl": res["rounded"]["sl"], "tp": res["rounded"]["tp"],
                                "be": p_["breakeven"], "r_init": abs(p_["entry"] - p_["sl"]),
                                "trail_step": 0, "qty": res["rounded"]["qty"],
                                "sl_order_id": sl_order_id, "be_done": False,
                                "chat_id": int(_cfg.TG_CHAT_ID), "trade_id": trade_id,
                                "setup_tag": tag, "opened_ts": int(time.time()),
                                "auto": True,
                            }
                            journal.save_active(p_["symbol"], ACTIVE[p_["symbol"]])
                            opened_auto += 1
                            try:
                                auto_state.log_open(p_["symbol"], p_["direction"])
                                today_count += 1
                            except Exception as _e:
                                print("log_open err:", _e)
                            await bot.send_message(int(_cfg.TG_CHAT_ID),
                                f"🤖 AUTO ОТКРЫТА ({today_count}/{AUTO_DAILY_LIMIT} за день)\n{p_['symbol']} {p_['direction']} qty={res['rounded']['qty']}\n"
                                f"вход {p_['entry']}  SL {res['rounded']['sl']}  TP {res['rounded']['tp']}\n"
                                f"setup {tag}  score {s_.get('score',0)}")
            except Exception as e:
                import traceback; traceback.print_exc()
                print("auto-open err:", e)
            # ===== /AUTO-OPEN =====

            if hits:
                lines = ["🔔 AUTO: найдены сетапы"]
                for s_, p_ in hits[:5]:
                    rr = p_.get("rr", 0)
                    info = p_.get("quality_info") or {}
                    obs = info.get("nearest_obstacle", "")
                    lines.append(
                        f"\n[{p_['direction']}] {s_['symbol']}  score {s_.get('score',0)}  "
                        f"setup {s_.get('detail',{}).get('smc','')}\n"
                        f"вход {p_['entry']}  SL {p_['sl']}  TP {p_['tp']}  "
                        f"RR {rr:.2f}\n"
                        f"профит ${p_.get('profit_usd',0):.2f} / убыток ${p_.get('loss_usd',0):.2f}\n"
                        f"target: {obs}"
                    )
                try:
                    await bot.send_message(int(_cfg.TG_CHAT_ID), "\n".join(lines))
                except Exception as e:
                    print("auto-scan send err:", e)
        except Exception as e:
            print("auto_scanner err:", e); traceback.print_exc()
        await asyncio.sleep(INTERVAL)



@dp.message(Command("auto"))
async def cmd_auto(m: Message):
    args = (m.text or "").split()
    if len(args) < 2:
        await m.answer(auto_state.status_text() + "\n\nкоманды: /auto on, /auto off, /auto status, /auto unblock"); return
    a = args[1].lower()
    if a == "on":
        blk, until = auto_state.is_blocked()
        if blk:
            left = (until - int(time.time())) // 60
            await m.answer(f"Заблокирован ещё {left}мин. /auto unblock — снять"); return
        auto_state.enable()
        await m.answer("Авто-режим ВКЛ. Лимит 5 сделок одновременно. После 4 минусов подряд — блок 24ч.")
    elif a == "off":
        auto_state.disable()
        await m.answer("Авто-режим ВЫКЛ")
    elif a == "unblock":
        auto_state.set_("blocked_until", "0")
        await m.answer("Блок снят. Используй /auto on")
    elif a == "status":
        await m.answer(auto_state.status_text())
    else:
        await m.answer("неизвестно: " + a)

@dp.message(F.text == "Авто")

@dp.message(F.text == "Режим")
async def btn_regime(m: Message):
    await cmd_regime(m)

@dp.message(F.text == "Авто")
async def btn_auto(m: Message):
    await cmd_auto(m)

async def main():
    import asyncio as _aio
    _aio.create_task(auto_scanner_loop())
    journal.init_active_table()
    journal.init_rejections_table()
    saved = journal.load_all_active()
    ACTIVE.update(saved)
    print(f"Бот запущен. chat_id={TG_CHAT_ID}. Восстановлено позиций: {len(saved)}")
    asyncio.create_task(monitor_positions())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())



# ---------- SYNC JOURNAL <-> EXCHANGE ----------
async def sync_journal_loop():
    """Раз в 90с подтягивает фактические SL/TP с биржи в journal.active_positions."""
    import asyncio, time
    from ..core import journal as _j, exchange as _ex
    await asyncio.sleep(60)
    while True:
        try:
            active = _j.load_all_active() or {}
            for sym, pos in active.items():
                try:
                    r = _ex.request("GET","/openApi/swap/v2/trade/openOrders",{"symbol": sym}, auth=True)
                    d = r.get("data") if isinstance(r, dict) else r
                    orders = d.get("orders") if isinstance(d, dict) else d
                    if not isinstance(orders, list):
                        continue
                    real_sl = None; real_sl_id = None; real_tp = None
                    for o in orders:
                        otype = o.get("type", "")
                        sp = o.get("stopPrice")
                        if not sp: continue
                        try: spf = float(sp)
                        except: continue
                        if "STOP_MARKET" in otype and "TAKE_PROFIT" not in otype:
                            real_sl = spf; real_sl_id = o.get("orderId")
                        elif "TAKE_PROFIT" in otype:
                            real_tp = spf
                    upd = {}
                    if real_sl is not None and abs(real_sl - float(pos.get("sl") or 0)) / max(real_sl,1e-9) > 0.0005:
                        upd["sl"] = real_sl
                        upd["sl_order_id"] = real_sl_id
                    if real_tp is not None and abs(real_tp - float(pos.get("tp") or 0)) / max(real_tp,1e-9) > 0.0005:
                        upd["tp"] = real_tp
                    if upd:
                        _j.update_active(sym, **upd)
                        print(f"sync {sym}: {upd}")
                except Exception as e:
                    print(f"sync err {sym}: {e}")
        except Exception as e:
            print(f"sync_loop err: {e}")
        await asyncio.sleep(90)
