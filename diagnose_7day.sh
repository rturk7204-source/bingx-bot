#!/bin/bash
# diagnose_7day.sh — полная диагностика для 7-day evaluation Block 8.5.
# Запускать 8 мая 2026 ~18:00 UTC (или раньше если интересно).
# Output: всё в одном дампе, готовом к копированию агенту.

cd /root/bingx-bot

echo "═══════════════════════════════════════════════════════════════"
echo "  BingX ARB Bot — 7-day Evaluation ($(date -u +'%Y-%m-%d %H:%M UTC'))"
echo "═══════════════════════════════════════════════════════════════"

echo ""
echo "── 1. REAL EQUITY ──"
python3 -c "
from bingx_transfer import get_wallet_balances
b = get_wallet_balances('USDT')
total = b['spot'] + b['fund'] + b['perp_equity']
print(f'  spot:          \${b[\"spot\"]:.2f}')
print(f'  fund:          \${b[\"fund\"]:.2f}')
print(f'  perp_equity:   \${b[\"perp_equity\"]:.2f}')
print(f'  perp_avail:    \${b[\"perp_avail\"]:.2f}')
print(f'  ─────────────────────')
print(f'  TOTAL EQUITY:  \${total:.2f}')
"

echo ""
echo "── 2. PER-BOT EARNINGS ──"
python3 -c "
import json
from datetime import datetime, timezone
now = datetime.now(timezone.utc)
total_earned = 0
total_capital = 0
total_pays = 0
print(f'  {\"Bot\":<6} {\"Symbol\":<14} {\"Age(h)\":>8} {\"Pays\":>5} {\"Earned\":>10} {\"APR\":>8}')
print('  ' + '─' * 60)
for i in ['', 2, 3, 4, 5, 6, 7]:
    try:
        s = json.load(open(f'arb_state{i}.json'))
    except FileNotFoundError:
        continue
    et_str = s.get('entry_time', '')
    earned = s.get('total_earned_usdt', 0)
    budget = s.get('spot_budget', 0)
    pays   = s.get('payments_received', 0)
    sym    = s.get('symbol', '?')
    open_  = s.get('position_open', False)
    try:
        et = datetime.strptime(et_str, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
        age_h = (now - et).total_seconds() / 3600
    except Exception:
        age_h = 0
    apr = (earned / budget) * (8760 / age_h) * 100 if age_h > 0 and budget > 0 else 0
    bot_id = i if i else 1
    flag = '' if open_ else ' [CLOSED]'
    print(f'  bot{bot_id:<3} {sym:<14} {age_h:>8.1f} {pays:>5} \${earned:>8.4f} {apr:>6.1f}%{flag}')
    if open_:
        total_earned  += earned
        total_capital += budget
        total_pays    += pays
print('  ' + '─' * 60)
print(f'  TOTAL: earned=\${total_earned:.4f} capital=\${total_capital:.0f} pays={total_pays}')
"

echo ""
echo "── 3. TOP-UP ACTIVITY (Block 8) ──"
if [ -f top_up_log.json ]; then
    python3 -c "
import json
from datetime import datetime, timezone, timedelta
data = json.load(open('top_up_log.json'))
if not data:
    print('  log пустой')
else:
    print(f'  total topups in log: {len(data)}')
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    recent = []
    for e in data:
        try:
            ts = datetime.fromisoformat(e.get('ts','').replace('Z','+00:00'))
            if ts >= week_ago:
                recent.append(e)
        except Exception:
            pass
    print(f'  topups last 7d:      {len(recent)}')
    total_added = sum(e.get('amount_usd', 0) for e in recent)
    print(f'  USD added last 7d:   \${total_added:.2f}')
    if recent:
        print(f'  most recent:         {recent[-1].get(\"ts\",\"?\")} {recent[-1].get(\"symbol\",\"?\")} +\${recent[-1].get(\"amount_usd\",0):.2f}')
"
else
    echo "  top_up_log.json не существует — Block 8 ещё не доливал ни разу"
fi

echo ""
echo "── 4. AUTO-REBALANCE ACTIVITY (Block 8.5) ──"
if [ -f auto_rebalance_log.json ]; then
    python3 -c "
import json
from datetime import datetime, timezone, timedelta
data = json.load(open('auto_rebalance_log.json'))
if not data:
    print('  log пустой')
else:
    print(f'  total entries:       {len(data)}')
    successful = [e for e in data if e.get('success') is True]
    noops      = [e for e in data if e.get('decision', {}).get('action') == 'noop']
    failed     = [e for e in data if e.get('success') is False]
    print(f'  successful transfers: {len(successful)}')
    print(f'  noops:                {len(noops)}')
    print(f'  failed:               {len(failed)}')
    if successful:
        total_moved = sum(e.get('decision',{}).get('amount',0) for e in successful)
        print(f'  total USD moved:     \${total_moved:.2f}')
        last = successful[-1]
        print(f'  last successful:     {last.get(\"ts\",\"?\")} {last.get(\"decision\",{}).get(\"action\")} \${last.get(\"decision\",{}).get(\"amount\",0):.2f}')
    if failed:
        print(f'  last failure error:  {failed[-1].get(\"error\",\"?\")[:80]}')
"
else
    echo "  auto_rebalance_log.json не существует — cron ещё не сработал?"
fi

echo ""
echo "── 5. CRON STATUS ──"
crontab -l 2>/dev/null | grep -E "auto_rebalance|rotation|state-backup|arb_bot7|top_up" | head -10

echo ""
echo "── 6. RUN-RATE МЕСЯЧНЫЙ ──"
python3 -c "
import json, os
from datetime import datetime, timezone

# Прогрессивная оценка: суммарно earned / суммарный age * 720 (часов в месяце)
now = datetime.now(timezone.utc)
total_earned = 0
total_bot_hours = 0
for i in ['', 2, 3, 4, 5, 6, 7]:
    try:
        s = json.load(open(f'arb_state{i}.json'))
    except FileNotFoundError:
        continue
    earned = s.get('total_earned_usdt', 0)
    et_str = s.get('entry_time', '')
    try:
        et = datetime.strptime(et_str, '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc)
        age_h = (now - et).total_seconds() / 3600
    except Exception:
        age_h = 0
    if s.get('position_open') and age_h > 0:
        total_earned    += earned
        total_bot_hours += age_h

if total_bot_hours > 0:
    # Средний \$/час по всем активным ботам
    avg_per_hour_per_bot = total_earned / total_bot_hours
    # 6-7 ботов одновременно → \$/час всей системы:
    n_bots = sum(1 for i in ['',2,3,4,5,6,7] if os.path.exists(f'arb_state{i}.json'))
    system_per_hour = avg_per_hour_per_bot * n_bots
    monthly = system_per_hour * 720
    print(f'  активных ботов:      {n_bots}')
    print(f'  Σ earned active:     \${total_earned:.4f}')
    print(f'  Σ bot-hours:         {total_bot_hours:.1f}')
    print(f'  avg \$/час/бот:       \${avg_per_hour_per_bot:.4f}')
    print(f'  система \$/час:       \${system_per_hour:.4f}')
    print(f'  PROJECTED \$/мес:    \${monthly:.2f}')
    print()
    if monthly > 100:
        print(f'  ✓ ВЕРДИКТ: > \$100/мес → ПРОДОЛЖАЕМ, roadmap к \$250/мес')
    elif monthly < 80:
        print(f'  ✗ ВЕРДИКТ: < \$80/мес → рекомендую остановить, ручная торговля')
    else:
        print(f'  ⚠ ВЕРДИКТ: \$80-100/мес → серая зона, нужен анализ')
else:
    print('  нет открытых позиций для расчёта')
"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Скопируй этот вывод и пришли агенту для анализа."
echo "═══════════════════════════════════════════════════════════════"
