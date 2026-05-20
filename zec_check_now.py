"""ZEC: текущая цена, позиция, открытые ордера, последние свечи 5m/15m."""
import sys, time
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex

SYM = "ZEC-USDT"

t = ex.request("GET","/openApi/swap/v2/quote/ticker",{"symbol":SYM},auth=False).get("data") or {}
print(f"[ticker] last={t.get('lastPrice')}  24hChg={t.get('priceChangePercent')}%  "
      f"high={t.get('highPrice')}  low={t.get('lowPrice')}")

# Позиция
p = ex.request("GET","/openApi/swap/v2/user/positions",{"symbol":SYM},auth=True).get("data") or []
print(f"\n[positions] {len(p)}")
for x in p:
    print(f"  {x.get('positionSide')} qty={x.get('positionAmt')} entry={x.get('avgPrice')} "
          f"unrlz={x.get('unrealizedProfit')} liq={x.get('liquidationPrice')} margin={x.get('initialMargin')}")

# Открытые ордера
r = ex.request("GET","/openApi/swap/v2/trade/openOrders",{"symbol":SYM},auth=True)
orders = (r.get("data") or {}).get("orders") or []
print(f"\n[open orders] {len(orders)}")
for o in orders:
    print(f"  {o.get('orderId')} {o.get('side')} {o.get('positionSide')} "
          f"{o.get('type')} stop={o.get('stopPrice')} px={o.get('price')} qty={o.get('origQty')} status={o.get('status')}")

# История исполнений за сутки
since = int(time.time()*1000) - 24*3600*1000
r = ex.request("GET","/openApi/swap/v2/trade/allFillOrders",
               {"symbol":SYM,"startTs":since,"endTs":int(time.time()*1000)},auth=True)
fills = (r.get("data") or {}).get("fill_orders") or (r.get("data") or {}).get("fillOrders") or []
print(f"\n[fills 24h] {len(fills)}")
for f in fills[-10:]:
    print(f"  {f.get('filledTime') or f.get('time')}  {f.get('side')} {f.get('positionSide')} "
          f"qty={f.get('volume') or f.get('qty')} px={f.get('price')} pnl={f.get('profit') or f.get('realizedPnl')}")

# Свежие 5m
r = ex.request("GET","/openApi/swap/v3/quote/klines",
               {"symbol":SYM,"interval":"5m","limit":12},auth=False)
ks = r.get("data") or []
print(f"\n[last 12 candles 5m]")
for k in ks:
    if isinstance(k, dict):
        ts = int(k.get("time",0)); o=float(k["open"]); h=float(k["high"]); l=float(k["low"]); c=float(k["close"]); v=float(k.get("volume",0))
    else:
        ts=int(k[0]); o=float(k[1]); h=float(k[2]); l=float(k[3]); c=float(k[4]); v=float(k[5])
    print(f"  {time.strftime('%H:%M',time.gmtime(ts/1000))} o={o:.2f} h={h:.2f} l={l:.2f} c={c:.2f} v={v:.0f}")
