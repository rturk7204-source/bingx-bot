#!/bin/bash
cd /root/bingx-bot
source venv/bin/activate
python3 << 'PY'
import os,time,hmac,hashlib,requests
from urllib.parse import urlencode
K=os.getenv("BINGX_API_KEY"); S=os.getenv("BINGX_SECRET_KEY")
B="https://open-api.bingx.com"
def sig(p): return hmac.new(S.encode(),urlencode(p).encode(),hashlib.sha256).hexdigest()
def req(m,path,p):
    p["timestamp"]=int(time.time()*1000); p["signature"]=sig(p)
    r=requests.request(m,B+path,params=p,headers={"X-BX-APIKEY":K})
    return r.json()

# 1. Отменить старый SL @ 2.30
print("Отмена старого SL...")
r=req("GET","/openApi/swap/v2/trade/openOrders",{"symbol":"LAB-USDT"})
for o in r.get("data",{}).get("orders",[]):
    if o["type"]=="STOP_MARKET" and float(o["stopPrice"])==2.30:
        print(req("DELETE","/openApi/swap/v2/trade/order",{"symbol":"LAB-USDT","orderId":o["orderId"]}))

# 2. Новый SL @ 2.36 close_all
print("Новый SL @ 2.36...")
print(req("POST","/openApi/swap/v2/trade/order",{
    "symbol":"LAB-USDT","side":"BUY","positionSide":"SHORT",
    "type":"STOP_MARKET","stopPrice":2.36,"quantity":40,"closePosition":"true"
}))

# 3. Лимитный шорт-усреднение @ 2.25
print("Лимит SHORT 40 LAB @ 2.25...")
print(req("POST","/openApi/swap/v2/trade/order",{
    "symbol":"LAB-USDT","side":"SELL","positionSide":"SHORT",
    "type":"LIMIT","price":2.25,"quantity":40,"timeInForce":"GTC"
}))
PY
