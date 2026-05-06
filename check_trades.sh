#!/bin/bash
cd /root/bingx-bot && set -a && source .env && set +a
python3 -c "
import os,time,hmac,hashlib,requests
from urllib.parse import urlencode
K,S=os.getenv('BINGX_API_KEY'),os.getenv('BINGX_SECRET_KEY')
def r(path,p={}):
    p['timestamp']=int(time.time()*1000)
    p['signature']=hmac.new(S.encode(),urlencode(p).encode(),hashlib.sha256).hexdigest()
    return requests.get('https://open-api.bingx.com'+path,params=p,headers={'X-BX-APIKEY':K}).json()
manual=['ZEC-USDT','LAB-USDT','ENSO-USDT']  # последние сделки
total=0
for sym in manual:
    d=r('/openApi/swap/v2/user/positions',{'symbol':sym})['data']
    for x in d:
        if float(x['positionAmt'])!=0:
            pnl=float(x['unrealizedProfit']); total+=pnl
            entry=float(x['avgPrice']); mark=float(x['markPrice'])
            sign='+' if pnl>=0 else ''
            print(f\"{sym:<10} {x['positionSide']:<5} {x['positionAmt']:<8} entry={entry:<8.4f} mark={mark:<8.4f} PnL={sign}{pnl:.2f}\")
print(f'─────────────────────────────')
print(f'Total unrealized: {total:+.2f}')
"
