#!/bin/bash
set -e
cd /root/bingx-bot

echo "=== 1. Текущий perp баланс ==="
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
print(f'Perp USDT свободно: \${b.get_futures_usdt():.2f}')
"

echo ""
echo "=== 2. Обновляем user_reserve в auto_rebalance_config ==="
python3 << 'PY'
import json, os
cfg_path = "auto_rebalance_config.json"
cfg = {}
if os.path.exists(cfg_path):
    with open(cfg_path) as f:
        try: cfg = json.load(f)
        except: pass
old = cfg.get('user_reserve_perp_usdt', 0)
cfg['user_reserve_perp_usdt'] = old + 77
cfg['comment'] = '02.05 user долил +$77 (всего user_reserve $' + str(old + 77) + ')'
with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
print(f'user_reserve было: \${old} → стало: \${old + 77}')
print(f'auto_rebalance не тронет эти средства')
PY

echo ""
echo "=== ГОТОВО — \$77 защищены, торгуй ==="
