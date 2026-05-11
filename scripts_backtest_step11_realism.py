"""
Шаг 11: реализм. Slippage/spread + альтернативные exit-стратегии.

База = step5_final + SHORT wick ≤0.2 (наш новый prod).

Параметры реализма:
  SLIPPAGE — изменение цены входа против тебя
  SPREAD   — комиссия + half-spread на вход и выход
  
Конфигурации:
  A_chand_clean   — chandelier act+2R, slippage=0
  B_tp1R_clean    — fixed TP=+1R, slippage=0
  C_chand_real    — chandelier act+2R, slippage=0.15%
  D_tp1R_real     — fixed TP=+1R, slippage=0.15%
  E_tp1R_real_low — fixed TP=+1R, slippage=0.05% (BingX VIP0 норма)
  F_tp08R_real    — fixed TP=+0.8R, slippage=0.15% (легче достижим)
  G_tp12R_real    — fixed TP=+1.2R, slippage=0.15%
  H_tp15R_real    — fixed TP=+1.5R, slippage=0.15%

Также: проверим распределение макс. прибыли по сделкам — сколько вообще доходит до 0.5R/1R/2R.
"""
import statistics, json, time
from assistant.core.exchange import request

SYMBOLS = [
    "BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT","ADA-USDT",
    "BNB-USDT","LINK-USDT","AVAX-USDT","DOT-USDT","LTC-USDT",
    "TRX-USDT","NEAR-USDT","ATOM-USDT","ICP-USDT","APT-USDT","ARB-USDT","OP-USDT",
    "JASMY-USDT","JUP-USDT","UNI-USDT","ENS-USDT","ETHFI-USDT"
]

def klines(sym, interval='15m', limit=1000, end_ms=None):
    params = {'symbol':sym, 'interval':interval, 'limit':limit}
    if end_ms: params['endTime']=end_ms
    return request('GET','/openApi/swap/v3/quote/klines', params)

def find_swings(K, left=2, right=2):
    sw=[]
    for i in range(left, len(K)-right):
        h=K[i]['h']; l=K[i]['l']
        if all(K[i-j-1]['h']<h for j in range(left)) and all(K[i+j+1]['h']<h for j in range(right)):
            sw.append((i,h,'H'))
        if all(K[i-j-1]['l']>l for j in range(left)) and all(K[i+j+1]['l']>l for j in range(right)):
            sw.append((i,l,'L'))
    return sw

def atr_of(K, n=14):
    if len(K)<n+1: return 0
    trs=[max(K[i]['h']-K[i]['l'], abs(K[i]['h']-K[i-1]['c']), abs(K[i]['l']-K[i-1]['c']))
         for i in range(len(K)-n,len(K)) if i>=1]
    return sum(trs)/len(trs) if trs else 0

def detect_setup(K, i):
    if i<50: return None
    win=K[max(0,i-100):i+1]
    sw=find_swings(win,2,2)
    if len(sw)<4: return None
    highs=[s for s in sw if s[2]=='H']; lows=[s for s in sw if s[2]=='L']
    if len(highs)<2 or len(lows)<2: return None
    last_h, last_l = highs[-1], lows[-1]
    a=atr_of(win,14)
    if a==0: return None
    last=win[-1]['c']
    long_b=last>last_h[1]
    short_b=last<last_l[1]
    if long_b and short_b:
        if last_h[0]>=last_l[0]: short_b=False
        else: long_b=False
    if long_b:
        sl_idx=max(0,last_h[0]-3)
        sl=min(K[max(0,i-100)+sl_idx:max(0,i-100)+last_h[0]+1], key=lambda k:k['l'])['l']
        return {'dir':'LONG','entry':last,'sl':sl,'atr':a,'i':i}
    if short_b:
        sl_idx=max(0,last_l[0]-3)
        sl=max(K[max(0,i-100)+sl_idx:max(0,i-100)+last_l[0]+1], key=lambda k:k['h'])['h']
        return {'dir':'SHORT','entry':last,'sl':sl,'atr':a,'i':i}
    return None

def sim(K, s, cfg):
    """
    cfg: {
      'exit': 'chandelier' | 'tp_fixed',
      'tp_R': float (для tp_fixed),
      'ch_act': float (для chandelier),
      'ch_mult': float,
      'be_R': float | None,  # R при котором BE
      'slip_pct': float  # % slippage на вход
    }
    Возвращает (pnl_R, max_favorable_R, bars).
    max_favorable_R - сколько максимум плюсовала сделка (для статистики «как далеко доходят»).
    """
    ep_orig = s['entry']
    d = s['dir']
    # slippage увеличивает entry для LONG, уменьшает для SHORT (хуже)
    slip = ep_orig * cfg.get('slip_pct',0) / 100
    ep = ep_orig + slip if d=='LONG' else ep_orig - slip
    sl0 = s['sl']
    R = abs(ep - sl0)
    if R == 0: return None
    sl = sl0
    be_done = False
    ch_activated = False
    max_fav_R = 0.0
    max_bars = 200
    
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h = K[i]['h']; l = K[i]['l']
        # max favorable excursion
        if d == 'LONG':
            mfe_R = (h - ep) / R
        else:
            mfe_R = (ep - l) / R
        if mfe_R > max_fav_R: max_fav_R = mfe_R
        
        # SL check
        if d=='LONG' and l<=sl:
            # выход с slippage на выходе тоже (худшая цена)
            exit_p = sl - sl*cfg.get('slip_pct',0)/100
            return ((exit_p - ep)/R, max_fav_R, i-s['i'])
        if d=='SHORT' and h>=sl:
            exit_p = sl + sl*cfg.get('slip_pct',0)/100
            return ((ep - exit_p)/R, max_fav_R, i-s['i'])
        
        # TP fixed check
        if cfg['exit']=='tp_fixed':
            tp_R = cfg['tp_R']
            tp_price = ep + R*tp_R if d=='LONG' else ep - R*tp_R
            if d=='LONG' and h>=tp_price:
                exit_p = tp_price - tp_price*cfg.get('slip_pct',0)/100
                return ((exit_p - ep)/R, max_fav_R, i-s['i'])
            if d=='SHORT' and l<=tp_price:
                exit_p = tp_price + tp_price*cfg.get('slip_pct',0)/100
                return ((ep - exit_p)/R, max_fav_R, i-s['i'])
        
        # BE
        cur_R = (h-ep)/R if d=='LONG' else (ep-l)/R
        if cfg.get('be_R') is not None and not be_done and cur_R >= cfg['be_R']:
            if d=='LONG' and ep>sl: sl=ep
            if d=='SHORT' and ep<sl: sl=ep
            be_done=True
        
        # chandelier (только если exit=chandelier)
        if cfg['exit']=='chandelier':
            if cur_R >= cfg.get('ch_act', 2.0): ch_activated=True
            if ch_activated:
                ch_period = cfg.get('ch_period', 22)
                ch_mult = cfg.get('ch_mult', 3.0)
                j0 = max(0, i-ch_period+1)
                window = K[j0:i+1]
                a_now = atr_of(K[max(0,i-30):i+1],14) or s['atr']
                if d=='LONG':
                    hh = max(k['h'] for k in window); new_sl = hh - ch_mult*a_now
                    if new_sl > sl: sl = new_sl
                else:
                    ll = min(k['l'] for k in window); new_sl = ll + ch_mult*a_now
                    if new_sl < sl: sl = new_sl
    
    # достигли макс баров — выход по close
    last = K[min(s['i']+max_bars-1, len(K)-1)]['c']
    exit_p = last
    pnl = (exit_p-ep)/R if d=='LONG' else (ep-exit_p)/R
    return (pnl, max_fav_R, max_bars)

def aggregate(trs, label):
    n = len(trs)
    if n == 0: return {'label':label,'n':0,'WR':0,'avg_R':0,'sum_R':0,'max_DD':0}
    pnls = [t[0] for t in trs]
    wins = [p for p in pnls if p > 0.1]
    eq = [0]
    for p in pnls: eq.append(eq[-1]+p)
    peak=0; dd=0
    for e in eq:
        peak=max(peak,e); dd=min(dd,e-peak)
    return {'label':label,'n':n,'WR':round(len(wins)/n*100,1),
            'avg_R':round(statistics.mean(pnls),3),'sum_R':round(sum(pnls),1),
            'max_DD':round(dd,1)}

# === LOAD ===
print("Загружаю свечи (последние 1000)...")
data = {}
for sym in SYMBOLS:
    try:
        r = klines(sym, '15m', 1000).get('data') or []
        K=[]
        for k in r:
            try: K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'t':int(k['time'])})
            except: pass
        K = sorted(K, key=lambda x: x['t'])
        if len(K) >= 100: data[sym] = K
    except Exception as e:
        print(f" {sym}: err {e}")
print(f"символов: {len(data)}")

print("Ищу сетапы (с фильтрами prod)...")
setups = []
for sym, K in data.items():
    for i in range(100, len(K)-50, 4):
        sig = detect_setup(K, i)
        if not sig: continue
        ch24 = 0; ch4 = 0
        if i>=96: ch24=(K[i]['c']/K[i-96]['c']-1)*100
        if i>=16: ch4=(K[i]['c']/K[i-16]['c']-1)*100
        sig['ch24']=ch24; sig['ch4']=ch4
        sig['atr_pct']=sig['atr']/sig['entry']*100
        sig['symbol']=sym; sig['K']=K
        if sig['atr_pct']<0.4: continue
        if sig['dir']=='LONG'  and sig['ch4']<-1.5: continue
        if sig['dir']=='SHORT' and sig['ch4']>1.5:  continue
        if sig['dir']=='LONG'  and sig['ch24']<-3: continue
        if sig['dir']=='SHORT' and sig['ch24']>3:  continue
        # SHORT wick filter (prod)
        cur=K[i]
        body=abs(cur['c']-cur['o']) or 1e-12
        if sig['dir']=='SHORT':
            lower=min(cur['c'],cur['o'])-cur['l']
            if lower/body > 0.2: continue
        setups.append(sig)
print(f"  сетапов: {len(setups)}\n")

CFGS = [
    ('A_chand_clean',   {'exit':'chandelier','ch_act':2.0,'ch_mult':3.0,'be_R':1.0,'slip_pct':0.0}),
    ('B_tp1R_clean',    {'exit':'tp_fixed','tp_R':1.0,'be_R':None,'slip_pct':0.0}),
    ('C_chand_real',    {'exit':'chandelier','ch_act':2.0,'ch_mult':3.0,'be_R':1.0,'slip_pct':0.15}),
    ('D_tp1R_real',     {'exit':'tp_fixed','tp_R':1.0,'be_R':None,'slip_pct':0.15}),
    ('E_tp1R_lowsl',    {'exit':'tp_fixed','tp_R':1.0,'be_R':None,'slip_pct':0.05}),
    ('F_tp08R_real',    {'exit':'tp_fixed','tp_R':0.8,'be_R':None,'slip_pct':0.15}),
    ('G_tp12R_real',    {'exit':'tp_fixed','tp_R':1.2,'be_R':None,'slip_pct':0.15}),
    ('H_tp15R_real',    {'exit':'tp_fixed','tp_R':1.5,'be_R':None,'slip_pct':0.15}),
    ('I_tp1R_chand_real',{'exit':'chandelier','ch_act':1.0,'ch_mult':2.0,'be_R':0.5,'slip_pct':0.15}),
]

print("=== STEP 11: реализм + альтернативные TP стратегии ===")
print(f"{'config':>22s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD|")
print("-"*92)
all_runs = {}
for label, cfg in CFGS:
    trs = []
    for s in setups:
        out = sim(s['K'], s, cfg)
        if out: trs.append(out)
    r = aggregate(trs, label)
    all_runs[label] = (r, trs)
    sd = r['sum_R']/abs(r['max_DD']) if r['max_DD'] else 0
    print(f"  {label:>20s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f}")

# === КЛЮЧЕВОЙ срез: как далеко доходят сделки ===
# Возьмём бэктест C (chandelier real) и посмотрим распределение max_favorable_R
trs_C = all_runs['C_chand_real'][1]
print("\n=== Распределение max_favorable_R (как далеко в плюс доходила каждая сделка) ===")
print("База: C_chand_real (как мы сейчас торгуем + slippage)")
mfes = sorted([t[1] for t in trs_C])
n = len(mfes)
if n>0:
    levels = [0.3, 0.5, 0.7, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
    for L in levels:
        pct = len([m for m in mfes if m >= L]) / n * 100
        print(f"  ≥ {L}R: {len([m for m in mfes if m >= L]):>3d}/{n} ({pct:.0f}%)")
    print(f"\n  медиана MFE: {mfes[n//2]:.2f}R")
    print(f"  p25/p50/p75: {mfes[n//4]:.2f} / {mfes[n//2]:.2f} / {mfes[3*n//4]:.2f}")

print("\n=== ВЫВОДЫ ===")
print("Если % сделок достигающих 2R << 30% — chandelier бесполезен, нужен fixed TP")
print("Если sum_R падает >50% при slippage 0.15% — система не выдерживает реальных издержек")
