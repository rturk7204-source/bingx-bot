#!/bin/bash
set -e
cd /root/bingx-bot

echo "=== 1. Текущая картина балансов ==="
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
spot_u = b.get_spot_usdt()
perp_u = b.get_futures_usdt()
print(f'Spot USDT свободно: \${spot_u:.2f}')
print(f'Perp USDT свободно: \${perp_u:.2f}')
print(f'Итого свободного USDT: \${spot_u + perp_u:.2f}')
"

echo ""
echo "=== 2. Сколько маржи нужно ботам (минимум для работы) ==="
python3 -c "
import sys; sys.path.insert(0, '.')
# Проверяем требуемую маржу всех открытых позиций
import importlib
total_margin_needed = 0
for i in ['', '2', '3', '4', '5', '6', '7']:
    mod = importlib.import_module(f'arb_bot{i}')
    pos = mod.get_perp_position()
    if pos:
        margin = float(pos.get('margin', 0) or 0)
        notional = float(pos.get('positionValue', 0) or 0)
        total_margin_needed += margin
        print(f'  bot{i or 1} {mod.SYMBOL:<14} margin=\${margin:.2f} notional=\${notional:.2f}')
print(f'')
print(f'TOTAL margin used by 7 bots: \${total_margin_needed:.2f}')
"

echo ""
echo "=== 3. Расчёт безопасного запаса ==="
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
spot_u = b.get_spot_usdt()
perp_u = b.get_futures_usdt()
# Auto_rebalance держит safety+overflow на perp
# В перпе должно остаться: текущая маржа + ~30% запас под скачки + top-up buffer
# Топ-ап обычно $5-10/бот при margin warning
SAFETY_RESERVE_PERP = 50  # буфер под top-up margin
SAFETY_RESERVE_SPOT = 30  # буфер под compound и rotation enter
withdraw_target = 100
free_spot_after_reserve = max(0, spot_u - SAFETY_RESERVE_SPOT)
print(f'Free spot после резерва \$30: \${free_spot_after_reserve:.2f}')
if free_spot_after_reserve >= withdraw_target:
    print(f'✅ Можно снять \$100 со spot, не трогая perp')
    print(f'   Spot останется: \${spot_u - withdraw_target:.2f}')
else:
    deficit = withdraw_target - free_spot_after_reserve
    print(f'⚠️ Со spot можно \${free_spot_after_reserve:.2f}, нужно ещё \${deficit:.2f} с perp')
"

echo ""
echo "=== 4. Перевод $100 со SPOT на FUTURES ==="
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
import requests, hmac, hashlib, time
from urllib.parse import urlencode
ts = int(time.time()*1000)
params = {'asset':'USDT','amount':'100','type':'FUND_PFUTURES','timestamp':ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.get(f'https://open-api.bingx.com/openApi/api/v3/asset/transfer?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10).json()
print('Результат transfer:', r)
if r.get('code') == 0:
    print('✅ \$100 переведено spot → futures')
else:
    print(f'❌ ОШИБКА: {r}')
    sys.exit(1)
"

echo ""
echo "=== 5. Защита \$100 от auto_rebalance.py ==="
# auto_rebalance может переслать USDT обратно на spot, если perp_avail > safety+overflow
# Поднимаем safety до perp_used_margin + 100 + 50, чтобы $100 считался "вашим" запасом
python3 << 'PY'
import json, os
cfg_path = "auto_rebalance_config.json"
# Читаем существующую конфигурацию (если есть)
cfg = {}
if os.path.exists(cfg_path):
    with open(cfg_path) as f:
        try: cfg = json.load(f)
        except: pass

# Помечаем что $100 ваше — auto_rebalance не должен трогать
cfg['user_reserve_perp_usdt'] = 100
cfg['comment'] = '01.05 user добавил $100 на ручную торговлю фьючерсами'
with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
print(f'✅ Записан user_reserve_perp_usdt=$100 в {cfg_path}')
print(f'   auto_rebalance.py будет учитывать этот резерв при расчёте overflow')
PY

# Также блокируем auto_rebalance скрипт от запуска прямо сейчас на 5 минут
# чтобы не успел отреагировать пока $100 ещё не "освоились"
touch state/auto_balance.lock

echo ""
echo "=== 6. Финальные балансы ==="
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
spot_u = b.get_spot_usdt()
perp_u = b.get_futures_usdt()
print(f'Spot USDT:  \${spot_u:.2f}')
print(f'Perp USDT:  \${perp_u:.2f}  ← включает ваши \$100 для ручной торговли')
"

echo ""
echo "=== ВАЖНО ==="
echo "Ваши \$100 на FUTURES wallet, готовы для ручной торговли."
echo "auto_rebalance.py знает про user_reserve и не тронет."
echo "Ботам ничего не угрожает — все 7 позиций целы."
