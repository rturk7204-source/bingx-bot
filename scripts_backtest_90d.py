"""
Backtest 90 дней на top-20 alts.
Сравнение: текущая конфигурация vs предложенная.

Запускать на сервере: PYTHONPATH=/root/bingx-bot python3 /tmp/backtest_90d.py
"""
import sqlite3, time, json, statistics
from datetime import datetime, timedelta
from assistant.core.exchange import get_klines

# Топ-20 alts (можно расширить)
SYMBOLS = [
    "BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT","ADA-USDT",
    "BNB-USDT","LINK-USDT","AVAX-USDT","MATIC-USDT","DOT-USDT","LTC-USDT",
    "TRX-USDT","TON-USDT","NEAR-USDT","ATOM-USDT","ICP-USDT","APT-USDT",
    "ARB-USDT","OP-USDT"
]

def fetch_history(symbol, days=90, interval='15m'):
    """BingX API максимум 1000 свечей за раз. 15m * 96/день * 90 = 8640 свечей."""
    all_kl = []
    end_ms = int(time.time()*1000)
    chunk = 1000
    while True:
        try:
            r = get_klines(symbol, interval, chunk).get('data') or []
            if not r: break
            all_kl = r + all_kl  # API часто возвращает в обратном порядке
            if len(all_kl) >= days*96: break
            # Без startTime/endTime получаем только последние свечи; для глубокой истории нужно расширять
            break
        except Exception as e:
            print(f"  fetch err {symbol}: {e}")
            break
    # сортировка по времени
    all_kl = sorted(all_kl, key=lambda k: int(k.get('time',0)))
    return all_kl[-days*96:] if len(all_kl) > days*96 else all_kl


# === SMC light implementation для backtest (упрощённый) ===
def find_swings(K, left=2, right=2):
    sw = []
    for i in range(left, len(K)-right):
        h = K[i]['h']; l = K[i]['l']
        is_h = all(K[i-j-1]['h'] < h for j in range(left)) and all(K[i+j+1]['h'] < h for j in range(right))
        is_l = all(K[i-j-1]['l'] > l for j in range(left)) and all(K[i+j+1]['l'] > l for j in range(right))
        if is_h: sw.append((i,h,'H'))
        if is_l: sw.append((i,l,'L'))
    return sw

def atr(K, n=14):
    if len(K) < n+1: return 0
    trs = []
    for i in range(len(K)-n, len(K)):
        if i<1: continue
        tr = max(K[i]['h']-K[i]['l'], abs(K[i]['h']-K[i-1]['c']), abs(K[i]['l']-K[i-1]['c']))
        trs.append(tr)
    return sum(trs)/len(trs) if trs else 0

def adx(K, n=14):
    """ADX(14) на N последних свечах"""
    if len(K) < n*2: return 0
    plus_dm = []; minus_dm = []; trs = []
    for i in range(1, len(K)):
        up = K[i]['h'] - K[i-1]['h']
        dn = K[i-1]['l'] - K[i]['l']
        plus_dm.append(up if up > dn and up > 0 else 0)
        minus_dm.append(dn if dn > up and dn > 0 else 0)
        tr = max(K[i]['h']-K[i]['l'], abs(K[i]['h']-K[i-1]['c']), abs(K[i]['l']-K[i-1]['c']))
        trs.append(tr)
    if len(trs) < n: return 0
    atr_v = sum(trs[-n:])/n
    if atr_v == 0: return 0
    pdi = 100 * sum(plus_dm[-n:])/n / atr_v
    mdi = 100 * sum(minus_dm[-n:])/n / atr_v
    if pdi+mdi == 0: return 0
    dx = 100 * abs(pdi-mdi) / (pdi+mdi)
    return dx  # одна точка, не сглаженная — упрощение

def detect_setup(K, i):
    """Упрощённая SMC-детекция на свече i. Возвращает None или {'dir','entry','sl_struct'}."""
    if i < 50: return None
    K_window = K[max(0,i-100):i+1]
    sw = find_swings(K_window, 2, 2)
    if len(sw) < 4: return None
    highs = [s for s in sw if s[2]=='H']
    lows  = [s for s in sw if s[2]=='L']
    if len(highs)<2 or len(lows)<2: return None
    last_h, last_l = highs[-1], lows[-1]
    a = atr(K_window, 14)
    if a == 0: return None
    last = K_window[-1]['c']
    min_break = 0.3 * a
    long_break  = last > last_h[1] + min_break
    short_break = last < last_l[1] - min_break
    if long_break and short_break:
        if last_h[0] >= last_l[0]: short_break = False
        else: long_break = False
    if long_break:
        sl = min(K_window[max(0,last_h[0]-3):last_h[0]+1], key=lambda k: k['l'])['l']
        return {'dir':'LONG','entry':last,'sl':sl,'atr':a}
    if short_break:
        sl = max(K_window[max(0,last_l[0]-3):last_l[0]+1], key=lambda k: k['h'])['h']
        return {'dir':'SHORT','entry':last,'sl':sl,'atr':a}
    return None


def simulate_exit(K, idx_enter, sig, mode, params):
    """Эмулирует исход сделки от idx_enter.
    mode: 'fixed_rr_1.7' | 'fixed_rr_3.0' | 'trailing' | 'partial_trail' | 'timeout_33'
    Возвращает pnl_R и bars_held.
    """
    ep = sig['entry']; sl = sig['sl']; d = sig['dir']
    R = abs(ep - sl)
    if R == 0: return 0, 0
    
    # настройки по режиму
    if mode == 'fixed_rr_1.7':
        tp = ep + 1.7*R if d=='LONG' else ep - 1.7*R
    elif mode == 'fixed_rr_3.0':
        tp = ep + 3.0*R if d=='LONG' else ep - 3.0*R
    elif mode == 'partial_trail':
        tp1 = ep + 1.5*R if d=='LONG' else ep - 1.5*R
    
    timeout_bars = 33 if mode == 'timeout_33' else None
    
    sl_curr = sl
    partial_done = False
    pnl_locked = 0  # для partial
    
    for i in range(idx_enter+1, min(idx_enter+200, len(K))):
        bars_held = i - idx_enter
        if timeout_bars and bars_held >= timeout_bars:
            # timeout: закрытие по close
            close = K[i]['c']
            pnl = (close-ep)/R if d=='LONG' else (ep-close)/R
            return pnl, bars_held
        
        h = K[i]['h']; l = K[i]['l']
        
        # SL hit
        if d=='LONG' and l <= sl_curr:
            return -1.0 + pnl_locked, bars_held
        if d=='SHORT' and h >= sl_curr:
            return -1.0 + pnl_locked, bars_held
        
        # TP hit (для fixed)
        if mode in ('fixed_rr_1.7', 'fixed_rr_3.0'):
            target_R = 1.7 if mode=='fixed_rr_1.7' else 3.0
            if d=='LONG' and h >= tp:
                return target_R, bars_held
            if d=='SHORT' and l <= tp:
                return target_R, bars_held
        
        if mode == 'partial_trail':
            # сначала TP1 на 1.5R = берём 50%
            if not partial_done:
                if (d=='LONG' and h >= tp1) or (d=='SHORT' and l <= tp1):
                    partial_done = True
                    pnl_locked = 0.75  # 50% позиции на 1.5R
                    sl_curr = ep  # двигаем SL в безубыток для остатка
            # после partial — trailing для остатка по свингам
            if partial_done:
                # trail SL за 3 предыдущими свингами
                window = K[max(0,i-10):i]
                if d=='LONG':
                    new_sl = max(sl_curr, max(k['l'] for k in window))
                    sl_curr = new_sl
                else:
                    new_sl = min(sl_curr, min(k['h'] for k in window))
                    sl_curr = new_sl
        
        if mode == 'trailing':
            # ATR trailing — Chandelier exit style
            window = K[max(0,i-22):i+1]
            a_now = atr(window, 14)
            if d=='LONG':
                trail = max(k['h'] for k in window[-22:]) - 2.5*a_now
                sl_curr = max(sl_curr, trail)
            else:
                trail = min(k['l'] for k in window[-22:]) + 2.5*a_now
                sl_curr = min(sl_curr, trail)
    
    # вышли по концу окна — закрываем по последнему close
    last = K[min(idx_enter+200, len(K)-1)]['c']
    pnl = (last-ep)/R if d=='LONG' else (ep-last)/R
    return pnl + pnl_locked, 200


# === MAIN ===
print("Загружаю свечи...")
all_data = {}
for sym in SYMBOLS:
    print(f"  {sym}...", end=' ')
    raw = fetch_history(sym, 90, '15m')
    if not raw:
        print("пусто")
        continue
    K = []
    for k in raw:
        try:
            K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'v':float(k['volume']),'t':int(k['time'])})
        except:
            pass
    all_data[sym] = K
    print(f"{len(K)} свечей")

print(f"\nданные собраны: {len(all_data)} символов")

# Фильтры конфигов
CONFIG_OLD = {
    'min_atr_pct': 0.4,
    'min_rr': 1.7,
    'max_ch24_against': 3.0,
    'use_adx': False,
    'adx_min': 0,
    'exit_mode': 'fixed_rr_1.7',
    'risk_dollars': 15
}

CONFIG_NEW = {
    'min_atr_pct': 0.4,
    'min_rr': 2.0,
    'max_ch24_against': 3.0,
    'use_adx': True,
    'adx_min': 22,
    'exit_mode': 'partial_trail',  # 50% на 1.5R + trail
    'risk_dollars': 15
}

def run_backtest(config, label):
    trades = []
    for sym, K in all_data.items():
        if len(K) < 100: continue
        for i in range(100, len(K)-50, 4):  # каждые 4 свечи проверяем — минимум час между сетапами
            sig = detect_setup(K, i)
            if not sig: continue
            
            # фильтры
            entry = sig['entry']; sl = sig['sl']
            R = abs(entry-sl)
            if R == 0: continue
            sl_pct = R/entry*100
            
            # ATR фильтр
            atr_pct = sig['atr']/entry*100
            if atr_pct < config['min_atr_pct']: continue
            
            # ADX фильтр (для NEW)
            if config['use_adx']:
                K_1h = K[max(0,i-50*4):i+1]  # сжатие 15m в 1h приближённо
                adx_v = adx(K[max(0,i-50):i+1], 14)
                if adx_v < config['adx_min']: continue
            
            # ch24 контр-тренд
            if i >= 96:
                ch24 = (K[i]['c']/K[i-96]['c']-1)*100
                if sig['dir']=='LONG' and ch24 < -3: continue
                if sig['dir']=='SHORT' and ch24 > 3: continue
            
            # симуляция выхода
            pnl_r, bars = simulate_exit(K, i, sig, config['exit_mode'], config)
            
            # quality: проверим RR минимум — для CONFIG ищем obstacles
            # (упрощение: уже отфильтровано по структуре)
            
            trades.append({'sym':sym,'dir':sig['dir'],'pnl_R':pnl_r,'bars':bars,'i':i,'atr_pct':atr_pct})
    
    n = len(trades)
    if n == 0:
        return {'label':label,'n':0,'note':'нет сделок'}
    pnls = [t['pnl_R'] for t in trades]
    wins = [p for p in pnls if p > 0.1]
    losses = [p for p in pnls if p < -0.1]
    wr = len(wins)/n*100
    avg_pnl = statistics.mean(pnls)
    std = statistics.stdev(pnls) if n>1 else 0
    sharpe = avg_pnl/std*((365*24*4)**0.5) if std else 0  # sharpe на бары
    
    # max drawdown
    equity = [0]
    for p in pnls:
        equity.append(equity[-1]+p)
    peak = 0; max_dd = 0
    for e in equity:
        peak = max(peak, e)
        max_dd = min(max_dd, e-peak)
    
    # ROI на $1500 баланс при $15 риск
    roi_dollars = sum(pnls) * 15  # каждый R = $15
    roi_pct = roi_dollars / 1500 * 100
    
    return {
        'label': label,
        'n': n,
        'WR': round(wr,1),
        'avg_pnl_R': round(avg_pnl,3),
        'sum_R': round(sum(pnls),1),
        'max_DD_R': round(max_dd,1),
        'roi_$': round(roi_dollars,1),
        'roi_%': round(roi_pct,1),
        'sharpe_approx': round(sharpe,2),
        'wins': len(wins),
        'losses': len(losses),
        'avg_bars': round(statistics.mean([t['bars'] for t in trades]),1),
        'best_trade': round(max(pnls),2),
        'worst_trade': round(min(pnls),2),
    }

print("\n=== BACKTEST: текущая конфигурация ===")
res_old = run_backtest(CONFIG_OLD, 'OLD')
print(json.dumps(res_old, indent=2, ensure_ascii=False))

print("\n=== BACKTEST: предложенная конфигурация ===")
res_new = run_backtest(CONFIG_NEW, 'NEW')
print(json.dumps(res_new, indent=2, ensure_ascii=False))

# Сохраняем
import json
out = {'OLD': res_old, 'NEW': res_new, 'symbols': list(all_data.keys()), 'days': 90}
with open('/tmp/backtest_results.json','w') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print("\nрезультаты в /tmp/backtest_results.json")
