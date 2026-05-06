#!/bin/bash
cd /root/bingx-bot
SYMBOL="LAB-USDT"
NOTIONAL=87       # $ позиции (4% риск)
LEVERAGE=3
SL=2.30
TP1=1.80
TP2=1.50

python3 << PY
import sys; sys.path.insert(0, '.')
import arb_bot as b
import requests, hmac, hashlib, time, json
from urllib.parse import urlencode

SYMBOL = "$SYMBOL"
NOTIONAL = $NOTIONAL
LEVERAGE = $LEVERAGE
SL = $SL
TP1 = $TP1
TP2 = $TP2

def signed_request(method, path, params):
    params['timestamp'] = int(time.time()*1000)
    qs = urlencode(params)
    sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f'https://open-api.bingx.com{path}?{qs}&signature={sig}'
    if method == 'POST':
        r = requests.post(url, headers={'X-BX-APIKEY': b.API_KEY}, timeout=10)
    else:
        r = requests.get(url, headers={'X-BX-APIKEY': b.API_KEY}, timeout=10)
    return r.json()

# 1. Mark price
r = requests.get(f'https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex?symbol={SYMBOL}', timeout=5).json()
mark = float(r['data']['markPrice'])
print(f'Mark price: ${mark:.4f}')
print(f'Funding: {float(r["data"]["lastFundingRate"])*100:+.4f}% / 4h')

# 2. Установить плечо
r = signed_request('POST', '/openApi/swap/v2/trade/leverage',
    {'symbol': SYMBOL, 'side': 'SHORT', 'leverage': LEVERAGE})
print(f'Leverage set: {r}')

# 3. Открыть SHORT (market order)
qty = round(NOTIONAL / mark, 2)
print(f'Открываем SHORT {qty} LAB (~\${NOTIONAL}) @ ~\${mark:.4f}')
r = signed_request('POST', '/openApi/swap/v2/trade/order',
    {'symbol': SYMBOL, 'side': 'SELL', 'positionSide': 'SHORT',
     'type': 'MARKET', 'quantity': qty})
print(f'Open SHORT result: {r}')

if r.get('code') != 0:
    print('❌ ОШИБКА открытия. Прерываю.')
    sys.exit(1)

# 4. Stop Loss (BUY to close SHORT at SL price)
print(f'\\nУстанавливаем SL @ \${SL}')
r = signed_request('POST', '/openApi/swap/v2/trade/order',
    {'symbol': SYMBOL, 'side': 'BUY', 'positionSide': 'SHORT',
     'type': 'STOP_MARKET', 'stopPrice': SL,
     'quantity': qty, 'workingType': 'MARK_PRICE'})
print(f'SL: {r}')

# 5. Take Profit 1 (50% позиции)
qty_tp1 = round(qty * 0.5, 2)
print(f'\\nTP1 \${TP1} на 50% (\${qty_tp1} LAB)')
r = signed_request('POST', '/openApi/swap/v2/trade/order',
    {'symbol': SYMBOL, 'side': 'BUY', 'positionSide': 'SHORT',
     'type': 'TAKE_PROFIT_MARKET', 'stopPrice': TP1,
     'quantity': qty_tp1, 'workingType': 'MARK_PRICE'})
print(f'TP1: {r}')

# 6. Take Profit 2 (оставшиеся 50%)
qty_tp2 = round(qty - qty_tp1, 2)
print(f'\\nTP2 \${TP2} на 50% (\${qty_tp2} LAB)')
r = signed_request('POST', '/openApi/swap/v2/trade/order',
    {'symbol': SYMBOL, 'side': 'BUY', 'positionSide': 'SHORT',
     'type': 'TAKE_PROFIT_MARKET', 'stopPrice': TP2,
     'quantity': qty_tp2, 'workingType': 'MARK_PRICE'})
print(f'TP2: {r}')

# 7. Финальный статус
print(f'\\n=== ИТОГ ===')
r = signed_request('GET', '/openApi/swap/v2/user/positions', {'symbol': SYMBOL})
for p in r.get('data', []):
    if abs(float(p.get('positionAmt', 0))) > 0.01:
        print(f'  Позиция: {p["positionSide"]} qty={p["positionAmt"]} avg=\${p["avgPrice"]} mark=\${p["markPrice"]} pnl=\${p["unrealizedProfit"]} liq=\${p["liquidationPrice"]}')
print(f'  SL @ \${SL}, TP1 @ \${TP1}, TP2 @ \${TP2}')
print(f'  Risk при SL: ~\${(SL-mark)/mark*NOTIONAL:.2f}')
print(f'  Reward TP1 50%: ~\${(mark-TP1)/mark*NOTIONAL*0.5:.2f}')
print(f'  Reward TP2 50%: ~\${(mark-TP2)/mark*NOTIONAL*0.5:.2f}')
PY
