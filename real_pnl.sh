#!/bin/bash
cd /root/bingx-bot
python3 << 'PY'
import sys; sys.path.insert(0, '.')
import importlib, requests, hmac, hashlib, time, json
from urllib.parse import urlencode
import arb_bot as base

# Получим historical purchase price для каждого spot token из state
print(f"{'Бот':<6} {'Пара':<14} {'Spot$':>7} {'SpotPnL':>9} {'PerpPnL':>9} {'Funding':>9} {'TRUE NET':>10}")
print("-" * 75)

total_true_net = 0
for i in ['', '2', '3', '4', '5', '6', '7']:
    mod = importlib.import_module(f'arb_bot{i}')
    bot_num = i if i else '1'
    
    # Текущая позиция
    spot_qty = mod.get_spot_token() or 0
    pos = mod.get_perp_position()
    mark = mod.get_mark_price() or 0
    notional = spot_qty * mark
    
    if notional < 5:
        print(f"bot{bot_num:<3} {mod.SYMBOL:<14} EMPTY")
        continue
    
    # Perp PnL — открытый PnL шорта
    perp_pnl = float(pos.get('unrealizedProfit', 0) or 0) if pos else 0
    perp_avg = float(pos.get('avgPrice', 0) or 0) if pos else 0
    perp_qty = abs(float(pos.get('positionAmt', 0) or 0)) if pos else 0
    
    # Spot PnL — нужна цена покупки. Из state.entry_spot_price
    try:
        state = mod.load_state()
        spot_entry = float(state.get('spot_entry_price', 0) or state.get('entry_spot_price', 0) or 0)
        funding_total = float(state.get('total_funding_earned', 0) or state.get('funding_total', 0) or 0)
    except Exception as e:
        spot_entry = 0
        funding_total = 0
    
    if spot_entry > 0:
        spot_pnl = (mark - spot_entry) * spot_qty
    else:
        # fallback: примем что цена входа = avgPrice perp (входили одновременно)
        spot_pnl = (mark - perp_avg) * spot_qty if perp_avg > 0 else 0
    
    true_net = spot_pnl + perp_pnl + funding_total
    total_true_net += true_net
    
    print(f"bot{bot_num:<3} {mod.SYMBOL:<14} ${notional:>6.0f} ${spot_pnl:>+7.2f} ${perp_pnl:>+7.2f} ${funding_total:>+7.2f} ${true_net:>+8.2f}")

print("-" * 75)
print(f"{'TOTAL TRUE NET':<55} ${total_true_net:>+8.2f}")
print()
print("Smysl: SpotPnL и PerpPnL должны почти компенсировать друг друга.")
print("Если SpotPnL + PerpPnL близко к 0 → delta-neutral работает")
print("Если сумма отрицательная → реальные потери (перехедж/комиссии/проскальзывание)")
PY

echo ""
echo "=== Что в state по входным ценам? ==="
for i in "" 2 3 4 5 6 7; do
    F="state/arb_bot${i}.json"
    [ -f "$F" ] && echo "--- bot${i:-1} ---" && cat "$F" | python3 -c "
import json, sys
d = json.load(sys.stdin)
keys = ['spot_entry_price', 'entry_spot_price', 'spot_buy_price', 'perp_entry_price', 'total_funding_earned', 'funding_total', 'opened_at', 'spot_qty', 'perp_qty']
for k in keys:
    if k in d:
        print(f'  {k}: {d[k]}')
"
done
