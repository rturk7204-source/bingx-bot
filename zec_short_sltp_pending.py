"""
ZEC SHORT — добиваем SL/TP к уже выставленным лимитам входа.

Лимиты 595/608 (0.4+0.4) стоят. STOP_MARKET и LIMIT-закрытие отбила биржа
с position not exist. Используем TRIGGER_MARKET / TRIGGER_LIMIT —
они активируются при достижении цены независимо от наличия позиции.

SL  TRIGGER_MARKET BUY 632 qty 0.8
TP1 TRIGGER_LIMIT  BUY 560 qty 0.4 (price=560)
TP2 TRIGGER_LIMIT  BUY 540 qty 0.4 (price=540)
"""
import sys
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex
from assistant.core.config import TG_TOKEN, TG_CHAT_ID
from assistant.signals.execute import get_contract, round_to
import asyncio
from aiogram import Bot

SYM = "ZEC-USDT"
SL  = 632.00
TP1 = 560.00
TP2 = 540.00
QTY1 = 0.4
QTY2 = 0.4
QT   = 0.8
CHAT_ID = int(TG_CHAT_ID) if TG_CHAT_ID else 729951023

def main():
    c = get_contract(SYM)
    qp = int(c.get("quantityPrecision", 2))
    pp = int(c.get("pricePrecision", 2))
    sl = round_to(SL, pp); t1 = round_to(TP1, pp); t2 = round_to(TP2, pp)
    q1 = round_to(QTY1, qp); q2 = round_to(QTY2, qp); qt = round_to(QT, qp)

    print(f"[plan] SL={sl}({qt})  TP1={t1}({q1})  TP2={t2}({q2})")

    # SL — TRIGGER_MARKET (pending, активируется когда mark >= 632)
    r_sl = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                          type="TRIGGER_MARKET", stopPrice=sl, quantity=qt,
                          workingType="MARK_PRICE")
    print(f"[SL TRIGGER_MARKET @{sl}] code={r_sl.get('code')} msg={r_sl.get('msg')} "
          f"id={r_sl.get('data',{}).get('order',{}).get('orderId')}")

    # TP1 — TRIGGER_LIMIT (активируется при mark <= 560, исполнится лимитом 560)
    r_tp1 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                           type="TRIGGER_LIMIT", stopPrice=t1, price=t1, quantity=q1,
                           workingType="MARK_PRICE", timeInForce="GTC")
    print(f"[TP1 TRIGGER_LIMIT @{t1}] code={r_tp1.get('code')} msg={r_tp1.get('msg')} "
          f"id={r_tp1.get('data',{}).get('order',{}).get('orderId')}")

    # TP2 — TRIGGER_LIMIT
    r_tp2 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                           type="TRIGGER_LIMIT", stopPrice=t2, price=t2, quantity=q2,
                           workingType="MARK_PRICE", timeInForce="GTC")
    print(f"[TP2 TRIGGER_LIMIT @{t2}] code={r_tp2.get('code')} msg={r_tp2.get('msg')} "
          f"id={r_tp2.get('data',{}).get('order',{}).get('orderId')}")

    # Список текущих открытых ордеров
    r = ex.request("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": SYM}, auth=True)
    orders = (r.get("data") or {}).get("orders") or []
    print(f"\n[open orders ZEC] {len(orders)}")
    for o in orders:
        print(f"  {o.get('orderId')} {o.get('side')} {o.get('positionSide')} "
              f"{o.get('type')} stop={o.get('stopPrice')} px={o.get('price')} qty={o.get('origQty')}")

    async def tg():
        try:
            b = Bot(TG_TOKEN)
            await b.send_message(CHAT_ID,
                f"[ZEC pending SL/TP]\nSL {sl} ({qt}) | TP1 {t1} ({q1}) | TP2 {t2} ({q2})\n"
                f"Активируются после набора позиции 595/608.")
            await b.session.close()
        except Exception as e:
            print(f"tg err: {e}")
    asyncio.run(tg())

if __name__ == "__main__":
    main()
