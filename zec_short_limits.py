"""
ZEC-USDT SHORT — двухтировые лимитные входы.

План:
  Изоляция, leverage 10×
  LIMIT SHORT 0.4 @ 595.00
  LIMIT SHORT 0.4 @ 608.00
  SL  stop-market 632 на 0.8 (общий)
  TP1 limit 0.4 @ 560
  TP2 limit 0.4 @ 540

Логика:
  1. Установить isolated + leverage 10× для SHORT
  2. Поставить два LIMIT SELL (positionSide=SHORT) на 595 и 608
  3. SL stop-market BUY на 632 (qty 0.8)
  4. Два TP LIMIT BUY на 560 и 540 (по 0.4)
  5. Уведомить в TG
"""
import sys
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex
from assistant.core.config import TG_TOKEN, TG_CHAT_ID
from assistant.signals.execute import get_contract, round_to
import asyncio
from aiogram import Bot

SYM = "ZEC-USDT"
ENTRY1 = 595.00
ENTRY2 = 608.00
QTY1   = 0.4
QTY2   = 0.4
QTY_TOTAL = QTY1 + QTY2  # 0.8
SL     = 632.00
TP1    = 560.00
TP2    = 540.00
LEV    = 10
CHAT_ID = int(TG_CHAT_ID) if TG_CHAT_ID else 729951023

def main():
    c = get_contract(SYM)
    qp = int(c.get("quantityPrecision", 2))
    pp = int(c.get("pricePrecision", 2))

    q1 = round_to(QTY1, qp)
    q2 = round_to(QTY2, qp)
    qt = round_to(QTY_TOTAL, qp)
    e1 = round_to(ENTRY1, pp)
    e2 = round_to(ENTRY2, pp)
    sl = round_to(SL, pp)
    t1 = round_to(TP1, pp)
    t2 = round_to(TP2, pp)

    print(f"[plan] entries: {q1}@{e1} + {q2}@{e2} = {qt}")
    print(f"       SL={sl}  TP1={t1}({q1})  TP2={t2}({q2})  lev={LEV}x ISOLATED")

    # 1. Margin + leverage
    rm = ex.set_margin_mode(SYM, mode="ISOLATED")
    print(f"[margin ISOLATED] code={rm.get('code')} msg={rm.get('msg')}")
    rl = ex.set_leverage(SYM, "SHORT", LEV)
    print(f"[lev SHORT {LEV}x] code={rl.get('code')} msg={rl.get('msg')}")

    # 2. LIMIT SHORT entries (SELL, positionSide=SHORT)
    r1 = ex.place_order(symbol=SYM, side="SELL", positionSide="SHORT",
                        type="LIMIT", price=e1, quantity=q1, timeInForce="GTC")
    print(f"[entry1 SELL {q1}@{e1}] code={r1.get('code')} msg={r1.get('msg')} "
          f"id={r1.get('data',{}).get('order',{}).get('orderId')}")

    r2 = ex.place_order(symbol=SYM, side="SELL", positionSide="SHORT",
                        type="LIMIT", price=e2, quantity=q2, timeInForce="GTC")
    print(f"[entry2 SELL {q2}@{e2}] code={r2.get('code')} msg={r2.get('msg')} "
          f"id={r2.get('data',{}).get('order',{}).get('orderId')}")

    # 3. SL — stop-market BUY на 632 общим объёмом
    r_sl = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                          type="STOP_MARKET", stopPrice=sl, quantity=qt,
                          workingType="MARK_PRICE")
    print(f"[SL @{sl}] code={r_sl.get('code')} msg={r_sl.get('msg')} "
          f"id={r_sl.get('data',{}).get('order',{}).get('orderId')}")

    # 4. TP1 limit
    r_tp1 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                           type="LIMIT", price=t1, quantity=q1, timeInForce="GTC")
    print(f"[TP1 @{t1}] code={r_tp1.get('code')} msg={r_tp1.get('msg')} "
          f"id={r_tp1.get('data',{}).get('order',{}).get('orderId')}")

    # 5. TP2 limit
    r_tp2 = ex.place_order(symbol=SYM, side="BUY", positionSide="SHORT",
                           type="LIMIT", price=t2, quantity=q2, timeInForce="GTC")
    print(f"[TP2 @{t2}] code={r_tp2.get('code')} msg={r_tp2.get('msg')} "
          f"id={r_tp2.get('data',{}).get('order',{}).get('orderId')}")

    # 6. TG
    async def tg():
        try:
            b = Bot(TG_TOKEN)
            avg = (e1*q1 + e2*q2) / (q1+q2)
            await b.send_message(CHAT_ID,
                f"[ZEC manual setup]\n"
                f"SHORT лимиты: {q1}@{e1} + {q2}@{e2} (avg ~{avg:.2f})\n"
                f"SL {sl} | TP1 {t1} ({q1}) | TP2 {t2} ({q2})\n"
                f"ISOLATED {LEV}x")
            await b.session.close()
        except Exception as e:
            print(f"tg err: {e}")
    asyncio.run(tg())

if __name__ == "__main__":
    main()
