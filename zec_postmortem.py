"""ZEC: фиксация результата после выноса по SL."""
import sys, time
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex

SYM = "ZEC-USDT"

# Текущая цена
t = ex.request("GET","/openApi/swap/v2/quote/ticker",{"symbol":SYM},auth=False).get("data") or {}
print(f"[ticker] last={t.get('lastPrice')}  24hChg={t.get('priceChangePercent')}%  high={t.get('highPrice')}")

# Позиция
p = ex.request("GET","/openApi/swap/v2/user/positions",{"symbol":SYM},auth=True).get("data") or []
print(f"\n[positions] {len(p)}")
for x in p:
    print(f"  {x.get('positionSide')} qty={x.get('positionAmt')} entry={x.get('avgPrice')} unrlz={x.get('unrealizedProfit')}")

# Открытые ордера
r = ex.request("GET","/openApi/swap/v2/trade/openOrders",{"symbol":SYM},auth=True)
orders = (r.get("data") or {}).get("orders") or []
print(f"\n[open ZEC orders] {len(orders)}")
for o in orders:
    print(f"  {o.get('orderId')} {o.get('side')} {o.get('positionSide')} {o.get('type')} stop={o.get('stopPrice')} qty={o.get('origQty')} status={o.get('status')}")

# Все ZEC ордера за сутки (фиксируем закрытие)
since = int(time.time()*1000) - 24*3600*1000
r = ex.request("GET","/openApi/swap/v2/trade/allFillOrders",
               {"symbol":SYM,"startTs":since,"endTs":int(time.time()*1000)},auth=True)
fills = (r.get("data") or {}).get("fill_orders") or (r.get("data") or {}).get("fillOrders") or []
print(f"\n[fills 24h ZEC] {len(fills)}")
total_pnl = 0.0
for f in fills:
    pnl = float(f.get('profit') or f.get('realizedPnl') or 0)
    fee = float(f.get('commission') or f.get('fee') or 0)
    total_pnl += pnl + fee
    print(f"  {f.get('filledTime') or f.get('time')}  {f.get('side')} {f.get('positionSide')} qty={f.get('volume') or f.get('qty')} px={f.get('price')} pnl={pnl} fee={fee}")
print(f"\n[ZEC total pnl 24h] {total_pnl:+.4f}")

# Баланс
b = ex.request("GET","/openApi/swap/v3/user/balance",{},auth=True).get("data") or {}
print(f"\n[balance] {b}")
