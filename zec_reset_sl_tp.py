"""
ZEC-USDT SHORT — перестановка SL/TP.
Текущая позиция: 0.938 ZEC, средняя 529.53.

Цель:
  SL  529.53 -> 562.00
  TP1 0.469 ZEC @ 515.00
  TP2 0.469 ZEC @ 500.00

Алгоритм:
  1. Получить открытые ордера по ZEC
  2. Отменить все условные / лимитные (старый SL=546.27 и TP=505.64)
  3. Поставить новый STOP_MARKET на 562 (BUY, SHORT)
  4. Поставить два LIMIT BUY: 515 и 500
"""
import sys
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex
from assistant.core.config import TG_TOKEN, TG_CHAT_ID
from assistant.signals.execute import get_contract, round_to
import asyncio
from aiogram import Bot

SYM = "ZEC-USDT"
DIR = "SHORT"
NEW_SL = 562.00
TP1 = 515.00
TP2 = 500.00
QTY_TOTAL = 0.938  # из скрина
CHAT_ID = int(TG_CHAT_ID) if TG_CHAT_ID else 729951023

def main():
    c = get_contract(SYM)
    qp = int(c.get("quantityPrecision", 2))
    pp = int(c.get("pricePrecision", 2))
    half = round_to(QTY_TOTAL / 2, qp)
    other = round_to(QTY_TOTAL - half, qp)

    sl_px = round_to(NEW_SL, pp)
    tp1_px = round_to(TP1, pp)
    tp2_px = round_to(TP2, pp)

    print(f"[plan] qty_total={QTY_TOTAL}  half={half}+{other}  pp={pp} qp={qp}")
    print(f"       new_SL={sl_px}  TP1={tp1_px}  TP2={tp2_px}")

    # 1. Получаем открытые ордера
    r = ex.request("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": SYM}, auth=True)
    orders = (r.get("data") or {}).get("orders") or []
    print(f"[orders] open count: {len(orders)}")
    for o in orders:
        print(f"  id={o.get('orderId')} side={o.get('side')} posSide={o.get('positionSide')} "
              f"type={o.get('type')} stopPrice={o.get('stopPrice')} price={o.get('price')} qty={o.get('origQty')}")

    # 2. Отменяем те что SHORT
    for o in orders:
        if o.get("positionSide") != "SHORT":
            continue
        oid = o.get("orderId")
        rc = ex.request("DELETE", "/openApi/swap/v2/trade/order",
                        {"symbol": SYM, "orderId": oid}, auth=True)
        print(f"[cancel] {oid}: code={rc.get('code')} msg={rc.get('msg')}")

    # 3. Новый SL — stop-market BUY на закрытие шорта
    r_sl = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                          type="STOP_MARKET", stopPrice=sl_px, quantity=QTY_TOTAL,
                          workingType="MARK_PRICE")
    print(f"[new SL] code={r_sl.get('code')} msg={r_sl.get('msg')} id={r_sl.get('data',{}).get('order',{}).get('orderId')}")

    # 4. TP1 limit
    if half > 0:
        r_tp1 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                               type="LIMIT", price=tp1_px, quantity=half,
                               timeInForce="GTC")
        print(f"[TP1 @ {tp1_px}] code={r_tp1.get('code')} msg={r_tp1.get('msg')} id={r_tp1.get('data',{}).get('order',{}).get('orderId')}")

    # 5. TP2 limit
    if other > 0:
        r_tp2 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                               type="LIMIT", price=tp2_px, quantity=other,
                               timeInForce="GTC")
        print(f"[TP2 @ {tp2_px}] code={r_tp2.get('code')} msg={r_tp2.get('msg')} id={r_tp2.get('data',{}).get('order',{}).get('orderId')}")

    # 6. TG
    async def tg():
        try:
            b = Bot(TG_TOKEN)
            await b.send_message(CHAT_ID,
                f"[ZEC manual] SL→{sl_px}, TP1={tp1_px} ({half}), TP2={tp2_px} ({other}). Старые ордера отменены.")
            await b.session.close()
        except Exception as e:
            print(f"tg err: {e}")
    asyncio.run(tg())

if __name__ == "__main__":
    main()
