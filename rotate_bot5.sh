#!/bin/bash
set -e
cd /root/bingx-bot

echo "=== 1. Откат SYMBOL на IDOL для корректного exit ==="
sed -i 's/^SYMBOL\s*=.*/SYMBOL      = "IDOL-USDT"/' arb_bot5.py
grep "^SYMBOL" arb_bot5.py

echo "=== 2. Закрытие IDOL позиции ==="
python3 arb_bot5.py --exit 2>&1 | tail -20

echo "=== 3. Проверка что позиции закрыты ==="
sleep 3
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot5 as b
spot_qty = b.get_spot_token() or 0
pos = b.get_perp_position()
perp_qty = abs(float(pos.get('positionAmt', 0))) if pos else 0
print(f'SPOT IDOL: {spot_qty}, PERP IDOL: {perp_qty}')
if spot_qty > 1 or perp_qty > 1:
    print('ОШИБКА: позиции не закрыты, прерываю ротацию')
    sys.exit(1)
print('OK: bot5 чист')
"

echo "=== 4. Смена SYMBOL на TRADOOR ==="
sed -i 's/^SYMBOL\s*=.*/SYMBOL      = "TRADOOR-USDT"/' arb_bot5.py
rm -f pause_bot5 state/pause_bot5
grep "^SYMBOL" arb_bot5.py

echo "=== 5. Вход в TRADOOR ==="
python3 arb_bot5.py --enter 2>&1 | tail -20

echo "=== 6. Статус после входа ==="
sleep 3
python3 arb_bot5.py --status 2>&1 | tail -15

echo "=== ГОТОВО ==="
