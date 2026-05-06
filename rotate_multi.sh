#!/bin/bash
set -e
cd /root/bingx-bot

rotate() {
    local BOT=$1 OLD=$2 NEW=$3 FILE="arb_bot${BOT}.py"
    [ "$BOT" = "1" ] && FILE="arb_bot.py"
    echo ""
    echo "########## ROTATE bot${BOT}: ${OLD} → ${NEW} ##########"

    echo "--- 1. SYMBOL=${OLD} для exit ---"
    sed -i "s/^SYMBOL\s*=.*/SYMBOL      = \"${OLD}\"/" "$FILE"
    grep "^SYMBOL" "$FILE"

    echo "--- 2. exit ${OLD} ---"
    python3 "$FILE" --exit 2>&1 | tail -15 || echo "(exit вернул ошибку, продолжаем проверку)"

    echo "--- 3. Проверка чистоты ---"
    sleep 3
    python3 -c "
import sys, importlib
sys.path.insert(0, '.')
mod = importlib.import_module('arb_bot${BOT}' if '${BOT}' != '1' else 'arb_bot')
spot_qty = mod.get_spot_token() or 0
pos = mod.get_perp_position()
perp_qty = abs(float(pos.get('positionAmt', 0))) if pos else 0
print(f'SPOT: {spot_qty}, PERP: {perp_qty}')
if (spot_qty or 0) > 1 or perp_qty > 1:
    print('ОШИБКА: позиция не закрыта')
    sys.exit(1)
print('OK')
"

    echo "--- 4. SYMBOL=${NEW} ---"
    sed -i "s/^SYMBOL\s*=.*/SYMBOL      = \"${NEW}\"/" "$FILE"
    rm -f "pause_bot${BOT}" "state/pause_bot${BOT}"
    grep "^SYMBOL" "$FILE"

    echo "--- 5. enter ${NEW} ---"
    python3 "$FILE" --enter 2>&1 | tail -15

    echo "--- 6. status ---"
    sleep 3
    python3 "$FILE" --status 2>&1 | tail -12
}

# bot4: APR-USDT → IRYS-USDT
rotate 4 "APR-USDT" "IRYS-USDT"

# bot7: BTC-USDT → HANA-USDT
rotate 7 "BTC-USDT" "HANA-USDT"

echo ""
echo "########## СНИМАЕМ ВСЕ ПАУЗЫ ##########"
rm -f pause_bot* state/pause_bot*
echo "Удалены все pause-файлы"
ls pause_bot* state/pause_bot* 2>&1 | tail -5

echo ""
echo "########## SYSTEMCTL СТАТУС ##########"
for i in "" 2 3 4 5 6 7; do
    UNIT="arb-bot${i:-1}"
    systemctl is-active "$UNIT" 2>/dev/null && echo "  $UNIT: active" || echo "  $UNIT: $(systemctl is-active $UNIT 2>&1)"
done

echo ""
echo "########## ГОТОВО ##########"
