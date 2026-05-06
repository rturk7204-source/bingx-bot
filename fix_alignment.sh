#!/bin/bash
set -e
cd /root/bingx-bot

echo "=== 1. Останавливаем auto_rotate.sh cron (тоже переписывает SYMBOL) ==="
crontab -l > /tmp/crontab.bak.$(date +%s)
crontab -l | grep -v "auto_rotate.sh" | crontab -
echo "Текущий cron:"
crontab -l | grep -v "^#" | grep -v "^$"

echo ""
echo "=== 2. Останавливаем все bot processes (если запущены) ==="
pkill -f "arb_bot.*--monitor" 2>/dev/null || echo "  нет активных monitor процессов"
sleep 2

echo ""
echo "=== 3. Выравниваем код с реальностью ==="
# bot1 → FIGHTID-USDT (там $170)
sed -i 's/^SYMBOL\s*=.*/SYMBOL      = "FIGHTID-USDT"/' arb_bot.py
echo "  bot1: $(grep '^SYMBOL' arb_bot.py | head -1)"

# bot4 → IRYS-USDT (там $160 сирота)
sed -i 's/^SYMBOL\s*=.*/SYMBOL      = "IRYS-USDT"/' arb_bot4.py
echo "  bot4: $(grep '^SYMBOL' arb_bot4.py | head -1)"

# Остальные не трогаем — они и так совпадают
for i in 2 3 5 6 7; do
    echo "  bot$i: $(grep '^SYMBOL' arb_bot$i.py | head -1)"
done

echo ""
echo "=== 4. Проверка что каждый бот видит свою позицию ==="
for i in "" 2 3 4 5 6 7; do
    FILE="arb_bot${i}"
    BOT_NUM="${i:-1}"
    python3 -c "
import sys; sys.path.insert(0, '.')
mod = __import__('${FILE}')
spot = mod.get_spot_token() or 0
pos = mod.get_perp_position()
perp = abs(float(pos.get('positionAmt', 0))) if pos else 0
mark = mod.get_mark_price() or 0
notional = spot * mark
status = '✅ OPEN' if notional > 5 else '⚪ EMPTY'
print(f'  bot${BOT_NUM} {mod.SYMBOL:<14}: {status} spot=\${notional:.0f} perp_qty={perp:.2f}')
" 2>&1 | tail -1
done

echo ""
echo "=== 5. Восстанавливаем cron (только monitor + auto_rebalance, БЕЗ rotate) ==="
crontab -l | grep -E "monitor|rebalance|auto_rebalance" || echo "  monitor/rebalance в cron"

echo ""
echo "=== 6. bot7 пустой — basis HANA -0.06%, APR 153% (4h). Заходим? ==="
echo "  HANA 🟢 -0.06% basis, 153% APR — отлично"
echo "  Но spot USDT=330, perp USDT=72. Для $80 enter нужно 80 на perp при leverage 2x = $40 margin."
echo "  Сейчас perp 72 USDT — хватит для одной позиции. Но IRYS на bot4 уже использует ~$53 margin."
echo "  Свободно perp: 72 - 53 = 19 USDT — мало для нового входа $80."
echo "  Перевод spot→perp $30:"
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
import requests, hmac, hashlib, time
from urllib.parse import urlencode
ts = int(time.time()*1000)
params = {'asset':'USDT','amount':'30','type':'FUND_PFUTURES','timestamp':ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.get(f'https://open-api.bingx.com/openApi/api/v3/asset/transfer?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10)
print('  Transfer 30 USDT spot→perp:', r.json())
"
sleep 2
echo "  Новый perp баланс:"
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
print(f'  Spot USDT: {b.get_spot_usdt():.2f}, Perp USDT: {b.get_futures_usdt():.2f}')
"

echo ""
echo "=== 7. Заходим bot7 в HANA-USDT ==="
sed -i 's/^SYMBOL\s*=.*/SYMBOL      = "HANA-USDT"/' arb_bot7.py
python3 arb_bot7.py --enter 2>&1 | tail -15

echo ""
echo "=== 8. ФИНАЛ — статус всех 7 ботов ==="
sleep 3
for i in "" 2 3 4 5 6 7; do
    FILE="arb_bot${i}"
    BOT_NUM="${i:-1}"
    python3 -c "
import sys; sys.path.insert(0, '.')
mod = __import__('${FILE}')
spot = mod.get_spot_token() or 0
pos = mod.get_perp_position()
perp = abs(float(pos.get('positionAmt', 0))) if pos else 0
mark = mod.get_mark_price() or 0
notional = spot * mark
status = '✅ OPEN' if notional > 5 else '⚪ EMPTY'
print(f'  bot${BOT_NUM} {mod.SYMBOL:<14}: {status} \${notional:.0f}')
" 2>&1 | tail -1
done

echo ""
echo "=== ГОТОВО ==="
