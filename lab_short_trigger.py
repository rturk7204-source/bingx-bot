"""
LAB-USDT SHORT — отложенный авто-вход с подтверждением.

ПЛАН:
  - Ждём funding в 05:00 PDT (12:00 UTC)
  - С 05:05 PDT по 05:15 PDT каждые 30с проверяем закрытую 5m свечу
  - Если close < 5.90 — открываем SHORT
  - Если за окно условие не выполнилось — пропускаем сделку, пишем алерт в TG

ПАРАМЕТРЫ:
  symbol = LAB-USDT, SHORT, leverage 10x ISOLATED
  notional ≈ $30 (qty ≈ 5 LAB)
  SL = 6.32 (hard stop-market)
  TP1 = 5.50 (50% qty, лимитный)
  TP2 = 5.00 (50% qty, лимитный)
"""
import sys, time, datetime
sys.path.insert(0, "/root/bingx-bot")

from assistant.core import exchange as ex
from assistant.core import journal
from assistant.signals.execute import get_contract, round_to
import asyncio, os
from aiogram import Bot

TG_TOKEN = os.environ.get("TELEGRAM_TOKEN") or open("/root/bingx-bot/.env").read().split("TELEGRAM_TOKEN=")[1].split("\n")[0].strip()
CHAT_ID = 729951023

SYM = "LAB-USDT"
DIR = "SHORT"
LEVERAGE = 10
NOTIONAL_USDT = 30.0
SL_PRICE = 6.32
TP1_PRICE = 5.50
TP2_PRICE = 5.00
TRIGGER_CLOSE_BELOW = 5.90

# Окно: 12:05–12:15 UTC = 05:05–05:15 PDT
START_UTC = datetime.datetime(2026, 5, 13, 12, 5, 0)
END_UTC   = datetime.datetime(2026, 5, 13, 12, 15, 0)

def now_utc():
    return datetime.datetime.utcnow()

def log(msg):
    ts = now_utc().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

async def tg(msg):
    try:
        bot = Bot(TG_TOKEN)
        await bot.send_message(CHAT_ID, f"[LAB-trigger] {msg}")
        await bot.session.close()
    except Exception as e:
        log(f"tg err: {e}")

def get_last_closed_5m_close():
    """Берёт предпоследнюю свечу (последняя ещё формируется)."""
    r = ex.request("GET", "/openApi/swap/v3/quote/klines",
                   {"symbol": SYM, "interval": "5m", "limit": 3})
    data = r.get("data") or []
    if len(data) < 2:
        return None
    # Сортировка по времени
    data = sorted(data, key=lambda x: int(x["time"]))
    return float(data[-2]["close"])

def get_mark():
    t = ex.get_ticker(SYM)
    d = (t.get("data") or [{}])
    row = d[0] if isinstance(d, list) else d
    return float(row.get("lastPrice", 0))

def execute_short(entry_px):
    c = get_contract(SYM)
    qp = int(c.get("quantityPrecision", 1))
    pp = int(c.get("pricePrecision", 4))
    qty = round_to(NOTIONAL_USDT / entry_px, qp)
    if qty <= 0:
        return {"ok": False, "msg": f"qty=0 (px={entry_px})"}

    # SL/TP округление
    sl = round_to(SL_PRICE, pp)
    tp1 = round_to(TP1_PRICE, pp)
    tp2 = round_to(TP2_PRICE, pp)
    # qty split
    half = round_to(qty / 2, qp)
    other = round_to(qty - half, qp)
    if half <= 0 or other <= 0:
        # для маленьких qty не делим
        half = qty
        other = 0

    log(f"plan: qty={qty} (half={half}+{other})  entry≈{entry_px}  SL={sl}  TP1={tp1}  TP2={tp2}")

    # 1) изолят + плечо
    ex.set_margin_mode(SYM, "ISOLATED")
    r_lev = ex.set_leverage(SYM, "SHORT", LEVERAGE)
    if r_lev.get("code") != 0:
        return {"ok": False, "msg": f"leverage: {r_lev}"}

    # 2) маркет SHORT
    r_open = ex.place_order(symbol=SYM, side="SELL", positionSide="SHORT",
                            type="MARKET", quantity=qty)
    if r_open.get("code") != 0:
        return {"ok": False, "msg": f"entry: {r_open}"}
    entry_id = r_open.get("data", {}).get("order", {}).get("orderId")

    # 3) SL — обязательный
    r_sl = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                          type="STOP_MARKET", stopPrice=sl, quantity=qty,
                          workingType="MARK_PRICE")
    if r_sl.get("code") != 0:
        # аварийный выход
        ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                       type="MARKET", quantity=qty)
        return {"ok": False, "msg": f"sl FAILED, position closed: {r_sl}"}
    sl_id = r_sl.get("data", {}).get("order", {}).get("orderId")

    # 4) TP1 (лимит buy)
    tp1_id = None
    if half > 0:
        r_tp1 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                               type="LIMIT", price=tp1, quantity=half,
                               timeInForce="GTC")
        tp1_id = r_tp1.get("data", {}).get("order", {}).get("orderId")
        log(f"tp1: {r_tp1.get('code')} {r_tp1.get('msg','ok')}")

    # 5) TP2 (лимит buy)
    tp2_id = None
    if other > 0:
        r_tp2 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                               type="LIMIT", price=tp2, quantity=other,
                               timeInForce="GTC")
        tp2_id = r_tp2.get("data", {}).get("order", {}).get("orderId")
        log(f"tp2: {r_tp2.get('code')} {r_tp2.get('msg','ok')}")

    # 6) Запись в journal (active_positions) — чтобы monitor_positions не дёргался
    # Помечаем как manual (не auto), be_done=False — но BE-логики уже нет.
    try:
        journal.save_active(SYM, {
            "symbol": SYM, "direction": "SHORT", "entry": entry_px,
            "sl": sl, "qty": qty, "tp": tp2, "be": entry_px,
            "leverage": LEVERAGE, "auto": False, "chat_id": CHAT_ID,
            "sl_order_id": sl_id, "be_done": False, "manual_trigger": "lab_5m_close<5.90"
        })
    except Exception as e:
        log(f"journal save err: {e}")

    return {"ok": True, "qty": qty, "entry_id": entry_id,
            "sl_id": sl_id, "tp1_id": tp1_id, "tp2_id": tp2_id}

async def main():
    log(f"start. Окно вход: {START_UTC} UTC ... {END_UTC} UTC")
    await tg(f"скрипт стартовал. Жду 5m close <{TRIGGER_CLOSE_BELOW} в окне 05:05-05:15 PDT")

    # ждём начала окна
    while now_utc() < START_UTC:
        time.sleep(15)

    log("вошли в окно проверки")
    last_close_seen = None
    while now_utc() < END_UTC:
        try:
            close5m = get_last_closed_5m_close()
            mark = get_mark()
            if close5m != last_close_seen:
                log(f"5m close={close5m}  mark={mark}  trigger if close<{TRIGGER_CLOSE_BELOW}")
                last_close_seen = close5m
            if close5m is not None and close5m < TRIGGER_CLOSE_BELOW:
                log("ТРИГГЕР СРАБОТАЛ — открываем шорт")
                res = execute_short(mark)
                log(f"result: {res}")
                if res.get("ok"):
                    await tg(f"ВХОД: SHORT {res['qty']} LAB @≈{mark}\nSL=6.32 TP1=5.50 TP2=5.00")
                else:
                    await tg(f"ВХОД НЕ УДАЛСЯ: {res.get('msg')}")
                return
        except Exception as e:
            log(f"loop err: {e}")
        time.sleep(30)

    log("окно закрыто без триггера — сделки нет")
    await tg(f"окно 05:05-05:15 закрыто, 5m close так и не пробил {TRIGGER_CLOSE_BELOW}. Сделки нет.")

if __name__ == "__main__":
    asyncio.run(main())
