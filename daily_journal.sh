#!/bin/bash
cd /root/bingx-bot && set -a && source .env && set +a
python3 << 'PY'
import os,time,hmac,hashlib,requests,json
from urllib.parse import urlencode
K,S=os.getenv('BINGX_API_KEY'),os.getenv('BINGX_SECRET_KEY')
def sig(p): return hmac.new(S.encode(),urlencode(p).encode(),hashlib.sha256).hexdigest()
def req(path,p={}):
    p['timestamp']=int(time.time()*1000); p['signature']=sig(p)
    return requests.get('https://open-api.bingx.com'+path,params=p,headers={'X-BX-APIKEY':K}).json()

end=int(time.time()*1000)
# Funding 24h
r=req('/openApi/swap/v2/user/income',{'incomeType':'FUNDING_FEE','startTime':end-86400000,'endTime':end,'limit':1000})
fund_24h=sum(float(x['income']) for x in r.get('data',[]))
# Realized PnL 24h (закрытые сделки)
r=req('/openApi/swap/v2/user/income',{'incomeType':'REALIZED_PNL','startTime':end-86400000,'endTime':end,'limit':1000})
pnl_24h=sum(float(x['income']) for x in r.get('data',[]))
# Открытые позиции (unrealized)
r=req('/openApi/swap/v2/user/positions',{})
unrealized=sum(float(x['unrealizedProfit']) for x in r.get('data',[]) if float(x['positionAmt'])!=0)

from datetime import datetime
print(f"=== {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
print(f"Funding 24h:  ${fund_24h:+.2f}")
print(f"Realized 24h: ${pnl_24h:+.2f}")
print(f"Unrealized:   ${unrealized:+.2f}")
print(f"24h TOTAL:    ${fund_24h+pnl_24h:+.2f}  (без unrealized)")
print(f"Projected mo: ${(fund_24h+pnl_24h)*30:+.2f}")
PY
