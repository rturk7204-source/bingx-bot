#!/bin/bash
set -e
cd /root/bingx-bot

# 1. Кладбище: arb_bot7.py каждую минуту переписывает SYMBOL на placeholder
crontab -l > /tmp/crontab.bak.$(date +%s)
crontab -l | grep -v "arb_bot7.py >> arb_bot7.log" | grep -v "auto_enter.sh" | grep -v "auto_rotate.sh" | grep -v "rotate-smart" | grep -v "arb_tools.*rebalance" | crontab -

# 2. Откатить ANCHOR_BOTS (RIVER нестабилен, якорь висит впустую)
sed -i 's|^ANCHOR_BOTS = .*|ANCHOR_BOTS = set()  # 01.05 откат: RIVER нестабилен (1/6 периодов)|' rotation.py

# 3. Зафиксировать SYMBOL во всех ботах от перезаписи (read-only)
chattr +i arb_bot.py arb_bot2.py arb_bot3.py arb_bot4.py arb_bot5.py arb_bot6.py arb_bot7.py 2>/dev/null || \
  for f in arb_bot.py arb_bot2.py arb_bot3.py arb_bot4.py arb_bot5.py arb_bot6.py arb_bot7.py; do chmod 444 "$f"; done

# 4. Удалить все pause-файлы (старые блокировки)
rm -f pause_bot* state/pause_bot*

# 5. Финальный отчёт
echo "=== CRON (только нужное) ==="
crontab -l | grep -v "^#" | grep -v "^$" | wc -l
echo "строк cron активно"
echo ""
echo "=== БОТЫ (заблокированы от перезаписи) ==="
for i in "" 2 3 4 5 6 7; do
  python3 -c "
import sys; sys.path.insert(0, '.')
mod = __import__('arb_bot${i}')
spot = mod.get_spot_token() or 0
mark = mod.get_mark_price() or 0
print(f'  bot${i:-1} {mod.SYMBOL:<14} \${spot*mark:.0f}')
" 2>&1 | tail -1
done
echo ""
echo "=== ЯКОРЬ ==="
grep "^ANCHOR_BOTS" rotation.py
echo ""
echo "ГОТОВО. Боты защищены от автоматических переписываний."
echo "Чтобы изменить SYMBOL вручную: chattr -i arb_botN.py (или chmod 644)"
