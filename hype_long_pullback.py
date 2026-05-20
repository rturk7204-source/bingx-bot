"""
HYPE-USDT LONG на откате.

План:
  Isolated, leverage 10x
  LIMIT BUY 0.20 @ 47.50
  LIMIT BUY 0.20 @ 47.00
  SL  TRIGGER_MARKET SELL 46.40 (qty 0.40)
  TP1 TRIGGER_LIMIT  SELL 49.50 (qty 0.20)
  TP2 TRIGGER_LIMIT  SELL 52.00 (qty 0.20)

SL/TP — TRIGGER_*, активируются после набора позиции.
"""
import sys
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex
from assistant.core.config import TG_TOKEN, TG_CHAT_ID
from assistant.signals.execute import get_contract, round_to
import asyncio
from aiogram import Bot

SYM = "HYPE-USDT"
E1, E2 = 47.50, 47.00
Q1, Q2 = 0.20, 0.20
QT = Q1 + Q2
SL = 46.40
TP1, TP2 = 49.50, 52.00
LEV = 10
CHAT_ID = int(TG_CHAT_ID) if TG_CHAT_ID else 729951023

def main():
    c = get_contract(SYM)
    qp = int(c.get("quantityPrecision", 2))
    pp = int(c.get("pricePrecision", 4))

    q1 = round_to(Q1, qp); q2 = round_to(Q2, qp); qt = round_to(QT, qp)
    e1 = round_to(E1, pp); e2 = round_to(E2, pp)
    sl = round_to(SL, pp); t1 = round_to(TP1, pp); t2 = round_to(TP2, pp)

    print(f"[plan] LONG {q1}@{e1} + {q2}@{e2} = {qt}")
    print(f"       SL={sl}({qt})  TP1={t1}({q1})  TP2={t2}({q2})  lev={LEV}x ISO")

    rm = ex.set_margin_mode(SYM, mode="ISOLATED")
    print(f"[margin ISOLATED] code={rm.get('code')} msg={rm.get('msg')}")
    rl = ex.set_leverage(SYM, "LONG", LEV)
    print(f"[lev LONG {LEV}x] code={rl.get('code')} msg={rl.get('msg')}")

    # Entries
    r1 = ex.place_order(symbol=SYM, side="BUY", positionSide="LONG",
                        type="LIMIT", price=e1, quantity=q1, timeInForce="GTC")
    print(f"[E1 BUY {q1}@{e1}] code={r1.get('code')} msg={r1.get('msg')} "
          f"id={r1.get('data',{}).get('order',{}).get('orderId')}")

    r2 = ex.place_order(symbol=SYM, side="BUY", positionSide="LONG",
                        type="LIMIT", price=e2, quantity=q2, timeInForce="GTC")
    print(f"[E2 BUY {q2}@{e2}] code={r2.get('code')} msg={r2.get('msg')} "
          f"id={r2.get('data',{}).get('order',{}).get('orderId')}")

    # SL trigger market
    r_sl = ex.place_order(symbol=SYM, side="SELL", positionSide="LONG",
                          type="TRIGGER_MARKET", stopPrice=sl, quantity=qt,
                          workingType="MARK_PRICE")
    print(f"[SL TRIGGER_MARKET @{sl}] code={r_sl.get('code')} msg={r_sl.get('msg')} "
          f"id={r_sl.get('data',{}).get('order',{}).get('orderId')}")

    # TP1
    r_tp1 = ex.place_order(symbol=SYM, side="SELL", positionSide="LONG",
                           type="TRIGGER_LIMIT", stopPrice=t1, price=t1, quantity=q1,
                           workingType="MARK_PRICE", timeInForce="GTC")
    print(f"[TP1 TRIGGER_LIMIT @{t1}] code={r_tp1.get('code')} msg={r_tp1.get('msg')} "
          f"id={r_tp1.get('data',{}).get('order',{}).get('orderId')}")

    # TP2
    r_tp2 = ex.place_order(symbol=SYM, side="SELL", positionSide="LONG",
                           type="TRIGGER_LIMIT", stopPrice=t2, price=t2, quantity=q2,
                           workingType="MARK_PRICE", timeInForce="GTC")
    print(f"[TP2 TRIGGER_LIMIT @{t2}] code={r_tp2.get('code')} msg={r_tp2.get('msg')} "
          f"id={r_tp2.get('data',{}).get('order',{}).get('orderId')}")

    # Список открытых ордеров
    r = ex.request("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": SYM}, auth=True)
    orders = (r.get("data") or {}).get("orders") or []
    print(f"\n[open orders HYPE] {len(orders)}")
    for o in orders:
        print(f"  {o.get('orderId')} {o.get('side')} {o.get('positionSide')} "
              f"{o.get('type')} stop={o.get('stopPrice')} px={o.get('price')} qty={o.get('origQty')}")

    async def tg():
        try:
            b = Bot(TG_TOKEN)
            avg = (e1*q1 + e2*q2)/(q1+q2)
            await b.send_message(CHAT_ID,
                f"[HYPE LONG pullback]\n"
                f"LIMIT BUY: {q1}@{e1} + {q2}@{e2} (avg ~{avg:.3f})\n"
                f"SL {sl} | TP1 {t1} ({q1}) | TP2 {t2} ({q2})\n"
                f"ISOLATED {LEV}x")
            await b.session.close()
        except Exception as e:
            print(f"tg err: {e}")
    asyncio.run(tg())

if __name__ == "__main__":
    main()
