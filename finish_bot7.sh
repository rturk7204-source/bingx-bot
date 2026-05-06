#!/bin/bash
set -e
cd /root/bingx-bot

echo "=== 1. Перевод USDT перп→спот→перп через auto_rebalance? Нет, прямой transfer ==="
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot7 as b
# Spot → Futures transfer 15 USDT (запас)
import requests, hmac, hashlib, time
from urllib.parse import urlencode
ts = int(time.time()*1000)
params = {'asset':'USDT','amount':'15','type':'FUND_PFUTURES','timestamp':ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.post(f'https://open-api.bingx.com/openApi/api/v3/asset/transfer?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10)
print('Transfer:', r.json())
"

echo ""
echo "=== 2. Баланс после трансфера ==="
sleep 2
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot7 as b
print(f'Спот USDT: {b.get_spot_usdt():.2f}')
print(f'Фьюч USDT: {b.get_futures_usdt():.2f}')
"

echo ""
echo "=== 3. Вход в HANA на bot7 ==="
python3 arb_bot7.py --enter 2>&1 | tail -15

echo ""
echo "=== 4. Финальный статус всех 7 ботов ==="
sleep 3
for i in "" 2 3 4 5 6 7; do
    FILE="arb_bot${i:-}"
    [ -z "$i" ] && FILE="arb_bot"
    SYM=$(grep "^SYMBOL" "${FILE}.py" | head -1 | grep -oP '"[^"]+"' | tr -d '"')
    BOT_NUM="${i:-1}"
    POS=$(python3 -c "
import sys; sys.path.insert(0, '.')
mod = __import__('${FILE}')
spot = mod.get_spot_token() or 0
pos = mod.get_perp_position()
perp = abs(float(pos.get('positionAmt', 0))) if pos else 0
mark = mod.get_mark_price() or 0
notional = spot * mark
status = 'OPEN' if notional > 5 else 'EMPTY'
print(f'{status} \${notional:.0f}')
" 2>/dev/null || echo "ERROR")
    printf "  bot%s: %-16s %s\n" "$BOT_NUM" "$SYM" "$POS"
done

echo ""
echo "=== 5. Проверка cron (мониторинг идёт через cron, не systemd) ==="
crontab -l 2>/dev/null | grep -E "arb_bot|monitor|rotation|rebalance" | head -10

echo ""
echo "=== ГОТОВО ==="
