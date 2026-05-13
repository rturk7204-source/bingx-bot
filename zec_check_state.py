"""Показать что реально стоит по ZEC: позиция + все открытые ордера."""
import sys
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex

SYM = "ZEC-USDT"

# Позиция
r = ex.request("GET", "/openApi/swap/v2/user/positions", {"symbol": SYM}, auth=True)
print("=== POSITION ===")
for p in (r.get("data") or []):
    print(f"  side={p.get('positionSide')} qty={p.get('positionAmt')} avg={p.get('avgPrice')} "
          f"unPnL={p.get('unrealizedProfit')} liq={p.get('liquidationPrice')} lev={p.get('leverage')}")

# Все открытые ордера
r2 = ex.request("GET", "/openApi/swap/v2/trade/openOrders", {"symbol": SYM}, auth=True)
orders = (r2.get("data") or {}).get("orders") or []
print(f"\n=== OPEN ORDERS ({len(orders)}) ===")
for o in orders:
    print(f"  id={o.get('orderId')}")
    print(f"    side={o.get('side')} posSide={o.get('positionSide')} type={o.get('type')}")
    print(f"    price={o.get('price')} stopPrice={o.get('stopPrice')} qty={o.get('origQty')}")
