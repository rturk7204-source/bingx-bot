#!/bin/bash
set -e
cd /root/bingx-bot

echo "=== 1. ОСТАНАВЛИВАЕМ CRON ROTATION (он мешает ручной работе) ==="
crontab -l > /tmp/crontab.bak.$(date +%s)
crontab -l | grep -v "rotate-smart\|arb_tools.*rebalance" | crontab -
echo "Cron rotation отключён. Backup: /tmp/crontab.bak.*"
crontab -l | grep -E "rotate|rebalance" || echo "  (нет rotate/rebalance в cron — OK)"

echo ""
echo "=== 2. Что реально на бирже сейчас (ВСЕ позиции) ==="
python3 -c "
import sys; sys.path.insert(0, '.')
import arb_bot as b
import requests, hmac, hashlib, time
from urllib.parse import urlencode

# Все perp позиции
ts = int(time.time()*1000)
params = {'timestamp': ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.get(f'https://open-api.bingx.com/openApi/swap/v2/user/positions?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10).json()
print('=== PERP позиции на бирже ===')
for p in r.get('data', []):
    amt = float(p.get('positionAmt', 0))
    if abs(amt) > 0.0001:
        print(f\"  {p['symbol']:<16} {p['positionSide']:<6} qty={amt} pnl=\${p.get('unrealizedProfit','?')} avg=\${p.get('avgPrice','?')}\")

# Все спот балансы > $1
print('')
print('=== SPOT балансы (>$1) ===')
ts = int(time.time()*1000)
params = {'timestamp': ts}
qs = urlencode(params)
sig = hmac.new(b.SECRET_KEY.encode(), qs.encode(), hashlib.sha256).hexdigest()
r = requests.get(f'https://open-api.bingx.com/openApi/spot/v1/account/balance?{qs}&signature={sig}',
    headers={'X-BX-APIKEY': b.API_KEY}, timeout=10).json()
for bl in r.get('data', {}).get('balances', []):
    free = float(bl.get('free', 0))
    if free > 0.0001 and bl.get('asset') != 'USDT':
        # Получаем цену чтобы оценить notional
        try:
            sym = f\"{bl['asset']}-USDT\"
            pr = requests.get(f'https://open-api.bingx.com/openApi/spot/v1/ticker/24hr?symbol={sym}', timeout=5).json()
            price = float(pr.get('data', [{}])[0].get('lastPrice', 0))
            notional = free * price
            if notional > 1:
                print(f\"  {bl['asset']:<10} qty={free:.4f} \${notional:.2f}\")
        except: pass
print('')
print('=== USDT балансы ===')
print(f'  Spot USDT: {b.get_spot_usdt():.2f}')
print(f'  Perp USDT: {b.get_futures_usdt():.2f}')
"

echo ""
echo "=== 3. SYMBOL в коде каждого бота (что бот ДУМАЕТ что торгует) ==="
for i in "" 2 3 4 5 6 7; do
    FILE="arb_bot${i}.py"
    SYM=$(grep "^SYMBOL" "$FILE" | head -1)
    printf "  bot%-2s %s: %s\n" "${i:-1}" "$FILE" "$SYM"
done

echo ""
echo "=== 4. basis всех топ-пар СЕЙЧАС (для решения куда заходить) ==="
python3 -c "
import requests
def basis(sym):
    try:
        spot = float(requests.get(f'https://open-api.bingx.com/openApi/spot/v1/ticker/24hr?symbol={sym}', timeout=5).json()['data'][0]['lastPrice'])
        d = requests.get(f'https://open-api.bingx.com/openApi/swap/v2/quote/premiumIndex?symbol={sym}', timeout=5).json()['data']
        perp = float(d['markPrice'])
        rate = float(d['lastFundingRate'])
        ih = int(d.get('fundingIntervalHours', 8))
        apr = rate * (24/ih) * 365 * 100
        return spot, perp, (perp-spot)/spot*100, apr
    except Exception as e: return None
for s in ['FIGHTID-USDT','TRADOOR-USDT','EVAA-USDT','IRYS-USDT','HANA-USDT','GUA-USDT','CYS-USDT','POWER-USDT','RIVER-USDT']:
    r = basis(s)
    if r:
        sp, pp, b, apr = r
        flag = '🟢' if abs(b)<0.3 else '🟡' if abs(b)<1.0 else '🔴'
        print(f'{flag} {s:<14} basis={b:+.2f}% APR={apr:.0f}%')
"

echo ""
echo "=== ДАЛЬШЕ ЖДЁМ РЕШЕНИЯ ==="
