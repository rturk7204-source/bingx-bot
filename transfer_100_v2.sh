#!/bin/bash
set -e
cd /root/bingx-bot

echo "=== 1. Перевод $100 spot→futures (правильный endpoint) ==="
python3 << 'PY'
import sys; sys.path.insert(0, '.')
import arb_bot as b
import requests, hmac, hashlib, time
from urllib.parse import urlencode

# BingX universal transfer: POST /openApi/api/v3/post/asset/transfer
# type: FUND_PFUTURES (Spot → Perpetual Futures)
ts = int(time.time()*1000)
params = {'type':'FUND_PFUTURES', 'asset':'USDT', 'amount':'100', 'timestamp':ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()

# Пробуем POST с правильным URL
r = requests.post(
    f'https://open-api.bingx.com/openApi/api/v3/post/asset/transfer?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY},
    timeout=10
).json()
print('POST endpoint:', r)

if r.get('tranId') or r.get('code') == 0:
    print('✅ Перевод выполнен')
    sys.exit(0)
else:
    # Альтернатива: внутренний клиент бота
    print('Пробуем через внутреннего клиента бота...')
    import importlib.util
    # Использую _post helper из arb_bot.py
    result = b._post('/openApi/api/v3/post/asset/transfer', {
        'type': 'FUND_PFUTURES',
        'asset': 'USDT',
        'amount': '100'
    })
    print('Через _post:', result)
PY

echo ""
echo "=== 2. Балансы после перевода ==="
sleep 2
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
print(f'Spot USDT: \${b.get_spot_usdt():.2f}')
print(f'Perp USDT: \${b.get_futures_usdt():.2f}  ← должно вырасти на ~\$100')
"

echo ""
echo "=== 3. Записываем user_reserve чтобы auto_rebalance не вернул ==="
python3 << 'PY'
import json, os
cfg_path = "auto_rebalance_config.json"
cfg = {}
if os.path.exists(cfg_path):
    with open(cfg_path) as f:
        try: cfg = json.load(f)
        except: pass
cfg['user_reserve_perp_usdt'] = 100
cfg['comment'] = '01.05 user $100 manual futures trading'
with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
print(f'Записан user_reserve в {cfg_path}: $100')
PY

echo ""
echo "ГОТОВО"
