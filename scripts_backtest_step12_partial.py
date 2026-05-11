"""
Шаг 12: партиал-TP. 50% на +1R, остаток ведёт чанделир.
Также сравниваем разные доли партиала и пороги.
Slippage 0.15% применяется к каждой ноге (вход+TP+финальный выход).
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

def sim_partial(K, s, cfg):
    """
    Партиал-TP. Закрываем partial_share на partial_R, остаток ведём чанделиром.
    cfg: {
      partial_share: 0..1,  # доля для tp1
      partial_R: float,     # триггер партиала в R
      ch_act: float,        # активация чанделира на R
      ch_mult: float,
      be_after_partial: bool,  # после партиала перевести остаток в BE
      slip_pct: float
    }
    Возвращает (total_pnl_R, max_fav_R, bars).
    total_pnl = partial_share * pnl_1 + (1-partial_share) * pnl_2
    """
    d = s['dir']
    ep_orig = s['entry']
    slip = ep_orig * cfg.get('slip_pct', 0) / 100
    ep = ep_orig + slip if d=='LONG' else ep_orig - slip
    sl0 = s['sl']
    R = abs(ep - sl0)
    if R == 0: return None
    sl = sl0
    partial_done = False
    pnl_partial = 0.0
    ch_activated = False
    max_fav_R = 0.0
    max_bars = 200
    partial_share = cfg.get('partial_share', 0.5)
    partial_R = cfg.get('partial_R', 1.0)
    
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h = K[i]['h']; l = K[i]['l']
        # MFE
        if d=='LONG': mfe_R = (h-ep)/R
        else: mfe_R = (ep-l)/R
        if mfe_R > max_fav_R: max_fav_R = mfe_R
        
        # SL check (вся оставшаяся доля)
        remaining = 1.0 - (partial_share if partial_done else 0)
        if d=='LONG' and l <= sl:
            exit_p = sl - sl*cfg.get('slip_pct',0)/100
            pnl_2 = (exit_p - ep) / R
            total = pnl_partial * partial_share + pnl_2 * remaining if partial_done else pnl_2
            return (total, max_fav_R, i-s['i'])
        if d=='SHORT' and h >= sl:
            exit_p = sl + sl*cfg.get('slip_pct',0)/100
            pnl_2 = (ep - exit_p) / R
            total = pnl_partial * partial_share + pnl_2 * remaining if partial_done else pnl_2
            return (total, max_fav_R, i-s['i'])
        
        # Partial TP
        if not partial_done:
            tp_price = ep + R*partial_R if d=='LONG' else ep - R*partial_R
            if (d=='LONG' and h >= tp_price) or (d=='SHORT' and l <= tp_price):
                exit_p = tp_price - tp_price*cfg.get('slip_pct',0)/100 if d=='LONG' else tp_price + tp_price*cfg.get('slip_pct',0)/100
                pnl_partial = (exit_p - ep)/R if d=='LONG' else (ep - exit_p)/R
                partial_done = True
                # перевод остатка в BE если cfg говорит
                if cfg.get('be_after_partial', True):
                    if d=='LONG' and ep > sl: sl = ep
                    if d=='SHORT' and ep < sl: sl = ep
        
        # Chandelier на остаток
        cur_R = (h-ep)/R if d=='LONG' else (ep-l)/R
        if cur_R >= cfg.get('ch_act', 2.0): ch_activated = True
        if ch_activated:
            ch_period = cfg.get('ch_period', 22)
            ch_mult = cfg.get('ch_mult', 3.0)
            j0 = max(0, i-ch_period+1)
            window = K[j0:i+1]
            a_now = atr_of(K[max(0,i-30):i+1], 14) or s['atr']
            if d=='LONG':
                hh = max(k['h'] for k in window); new_sl = hh - ch_mult*a_now
                if new_sl > sl: sl = new_sl
            else:
                ll = min(k['l'] for k in window); new_sl = ll + ch_mult*a_now
                if new_sl < sl: sl = new_sl
    
    # дошли до конца окна
    last = K[min(s['i']+max_bars-1, len(K)-1)]['c']
    pnl_2 = (last - ep)/R if d=='LONG' else (ep - last)/R
    if partial_done:
        total = pnl_partial * partial_share + pnl_2 * (1 - partial_share)
    else:
        total = pnl_2
    return (total, max_fav_R, max_bars)

def aggregate(trs, label):
    n = len(trs)
    if n == 0: return {'label':label,'n':0,'WR':0,'avg_R':0,'sum_R':0,'max_DD':0}
    pnls = [t[0] for t in trs]
    wins = [p for p in pnls if p > 0.05]
    eq = [0]
    for p in pnls: eq.append(eq[-1]+p)
    peak=0; dd=0
    for e in eq:
        peak=max(peak,e); dd=min(dd,e-peak)
    return {'label':label,'n':n,'WR':round(len(wins)/n*100,1),
            'avg_R':round(statistics.mean(pnls),3),'sum_R':round(sum(pnls),1),
            'max_DD':round(dd,1)}

print("Загружаю свечи...")
data = {}
for sym in SYMBOLS:
    try:
        r = klines(sym,'15m',1000).get('data') or []
        K=[]
        for k in r:
            try: K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'t':int(k['time'])})
            except: pass
        K = sorted(K, key=lambda x: x['t'])
        if len(K) >= 100: data[sym] = K
    except Exception as e: print(f" {sym}: err {e}")
print(f"символов: {len(data)}")

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
        cur=K[i]; body=abs(cur['c']-cur['o']) or 1e-12
        if sig['dir']=='SHORT':
            lower=min(cur['c'],cur['o'])-cur['l']
            if lower/body > 0.2: continue
        setups.append(sig)
print(f"сетапов: {len(setups)}\n")

CFGS = [
    ('PROD_chand_only',      {'partial_share':0.0,'partial_R':99,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':False,'slip_pct':0.15}),
    ('P30_at_1R',            {'partial_share':0.3,'partial_R':1.0,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':True,'slip_pct':0.15}),
    ('P50_at_1R',            {'partial_share':0.5,'partial_R':1.0,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':True,'slip_pct':0.15}),
    ('P70_at_1R',            {'partial_share':0.7,'partial_R':1.0,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':True,'slip_pct':0.15}),
    ('P50_at_08R',           {'partial_share':0.5,'partial_R':0.8,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':True,'slip_pct':0.15}),
    ('P50_at_12R',           {'partial_share':0.5,'partial_R':1.2,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':True,'slip_pct':0.15}),
    ('P50_at_1R_noBE',       {'partial_share':0.5,'partial_R':1.0,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':False,'slip_pct':0.15}),
    ('P50_at_1R_chand1R',    {'partial_share':0.5,'partial_R':1.0,'ch_act':1.0,'ch_mult':2.5,'be_after_partial':True,'slip_pct':0.15}),
    ('P50_at_1R_zeroslip',   {'partial_share':0.5,'partial_R':1.0,'ch_act':2.0,'ch_mult':3.0,'be_after_partial':True,'slip_pct':0.0}),
]

print("=== STEP 12: partial TP стратегии (slippage 0.15%) ===")
print(f"{'config':>22s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD|  Δ")
print("-"*92)
prod_sum = None
for label, cfg in CFGS:
    trs = []
    for s in setups:
        out = sim_partial(s['K'], s, cfg)
        if out: trs.append(out)
    r = aggregate(trs, label)
    if label=='PROD_chand_only': prod_sum=r['sum_R']
    delta = r['sum_R'] - prod_sum if prod_sum is not None else 0
    sd = r['sum_R']/abs(r['max_DD']) if r['max_DD'] else 0
    print(f"  {label:>20s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f}  Δ={delta:+.1f}R")

print("\n=== Интерпретация ===")
print("WR должен резко вырасти (партиал чаще плюсует)")
print("sum_R хочется >= PROD при значительно меньшем DD")
print("Если P50_at_1R даёт WR 50%+ и sum_R близкий — это психологически намного комфортнее")
