"""
Шаг 13: вклад BE в общую прибыль.
Тестируем чанделир с разными порогами BE (и без него).
Slippage 0.15%.
"""
import statistics
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
    cfg: be_R (None или float), ch_act, ch_mult, ch_period, slip_pct
    """
    d=s['dir']
    ep_orig=s['entry']
    slip = ep_orig * cfg.get('slip_pct',0)/100
    ep = ep_orig + slip if d=='LONG' else ep_orig - slip
    sl0=s['sl']; R=abs(ep-sl0)
    if R==0: return None
    sl=sl0; be_done=False; ch_activated=False
    max_bars=200
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        if d=='LONG' and l<=sl:
            exit_p = sl - sl*cfg.get('slip_pct',0)/100
            return ((exit_p-ep)/R, i-s['i'])
        if d=='SHORT' and h>=sl:
            exit_p = sl + sl*cfg.get('slip_pct',0)/100
            return ((ep-exit_p)/R, i-s['i'])
        cur_R = (h-ep)/R if d=='LONG' else (ep-l)/R
        # BE
        if cfg.get('be_R') is not None and not be_done and cur_R >= cfg['be_R']:
            if d=='LONG' and ep>sl: sl=ep
            if d=='SHORT' and ep<sl: sl=ep
            be_done=True
        # Chandelier
        if cur_R >= cfg.get('ch_act', 2.0): ch_activated=True
        if ch_activated:
            ch_period = cfg.get('ch_period', 22)
            ch_mult = cfg.get('ch_mult', 3.0)
            j0=max(0, i-ch_period+1)
            window=K[j0:i+1]
            a_now=atr_of(K[max(0,i-30):i+1],14) or s['atr']
            if d=='LONG':
                hh=max(k['h'] for k in window); new_sl=hh-ch_mult*a_now
                if new_sl>sl: sl=new_sl
            else:
                ll=min(k['l'] for k in window); new_sl=ll+ch_mult*a_now
                if new_sl<sl: sl=new_sl
    last=K[min(s['i']+max_bars-1, len(K)-1)]['c']
    pnl=(last-ep)/R if d=='LONG' else (ep-last)/R
    return (pnl, max_bars)

def aggregate(trs, label):
    n=len(trs)
    if n==0: return {'label':label,'n':0,'WR':0,'avg_R':0,'sum_R':0,'max_DD':0,'be_zone':0,'sl_count':0,'big_count':0}
    pnls=[t[0] for t in trs]
    wins=[p for p in pnls if p>0.1]
    eq=[0]
    for p in pnls: eq.append(eq[-1]+p)
    peak=0; dd=0
    for e in eq:
        peak=max(peak,e); dd=min(dd,e-peak)
    be_zone = len([p for p in pnls if -0.15<=p<=0.15])
    sl_count = len([p for p in pnls if p<-0.5])
    big_count = len([p for p in pnls if p>2.0])
    return {'label':label,'n':n,'WR':round(len(wins)/n*100,1),
            'avg_R':round(statistics.mean(pnls),3),'sum_R':round(sum(pnls),1),
            'max_DD':round(dd,1),'be_zone':be_zone,'sl_count':sl_count,'big_count':big_count}

print("Загружаю свечи...")
data={}
for sym in SYMBOLS:
    try:
        r=klines(sym,'15m',1000).get('data') or []
        K=[]
        for k in r:
            try: K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'t':int(k['time'])})
            except: pass
        K=sorted(K, key=lambda x:x['t'])
        if len(K)>=100: data[sym]=K
    except Exception as e: print(f" {sym}: err {e}")
print(f"символов: {len(data)}")

setups=[]
for sym, K in data.items():
    for i in range(100, len(K)-50, 4):
        sig=detect_setup(K,i)
        if not sig: continue
        ch24=0; ch4=0
        if i>=96: ch24=(K[i]['c']/K[i-96]['c']-1)*100
        if i>=16: ch4=(K[i]['c']/K[i-16]['c']-1)*100
        sig['ch24']=ch24; sig['ch4']=ch4
        sig['atr_pct']=sig['atr']/sig['entry']*100
        sig['symbol']=sym; sig['K']=K
        if sig['atr_pct']<0.4: continue
        if sig['dir']=='LONG' and sig['ch4']<-1.5: continue
        if sig['dir']=='SHORT' and sig['ch4']>1.5: continue
        if sig['dir']=='LONG' and sig['ch24']<-3: continue
        if sig['dir']=='SHORT' and sig['ch24']>3: continue
        cur=K[i]; body=abs(cur['c']-cur['o']) or 1e-12
        if sig['dir']=='SHORT':
            lower=min(cur['c'],cur['o'])-cur['l']
            if lower/body>0.2: continue
        setups.append(sig)
print(f"сетапов: {len(setups)}\n")

CFGS = [
    ('no_BE',           {'be_R':None, 'ch_act':2.0, 'ch_mult':3.0, 'slip_pct':0.15}),
    ('BE_05R',          {'be_R':0.5,  'ch_act':2.0, 'ch_mult':3.0, 'slip_pct':0.15}),
    ('BE_08R',          {'be_R':0.8,  'ch_act':2.0, 'ch_mult':3.0, 'slip_pct':0.15}),
    ('BE_1R_prod',      {'be_R':1.0,  'ch_act':2.0, 'ch_mult':3.0, 'slip_pct':0.15}),
    ('BE_12R',          {'be_R':1.2,  'ch_act':2.0, 'ch_mult':3.0, 'slip_pct':0.15}),
    ('BE_15R',          {'be_R':1.5,  'ch_act':2.0, 'ch_mult':3.0, 'slip_pct':0.15}),
    # с другими параметрами чанделира
    ('no_BE_chand1R',   {'be_R':None, 'ch_act':1.0, 'ch_mult':3.0, 'slip_pct':0.15}),
    ('no_BE_chand15R',  {'be_R':None, 'ch_act':1.5, 'ch_mult':3.0, 'slip_pct':0.15}),
    ('no_BE_chand_m25', {'be_R':None, 'ch_act':2.0, 'ch_mult':2.5, 'slip_pct':0.15}),
    ('no_BE_chand_m35', {'be_R':None, 'ch_act':2.0, 'ch_mult':3.5, 'slip_pct':0.15}),
]

print("=== STEP 13: BE влияние + параметры чанделира ===")
print(f"{'config':>20s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD| | BE-zone | -SL | >2R")
print("-"*120)
prod_sum=None
for label, cfg in CFGS:
    trs=[]
    for s in setups:
        out=sim(s['K'], s, cfg)
        if out: trs.append(out)
    r=aggregate(trs, label)
    if label=='BE_1R_prod': prod_sum=r['sum_R']
    delta=r['sum_R']-prod_sum if prod_sum is not None else 0
    sd=r['sum_R']/abs(r['max_DD']) if r['max_DD'] else 0
    print(f"  {label:>18s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f} | {r['be_zone']:>4d}    | {r['sl_count']:>3d} | {r['big_count']:>3d}   Δ={delta:+.1f}R")

print("\n=== Интерпретация ===")
print("BE-zone: сколько сделок умерло около 0 (BE-stop срабатывает раньше runners)")
print("-SL: количество настоящих стоп-аутов (худшие сделки)")
print(">2R: количество runners (главный источник прибыли)")
print("Выбираем конфиг с балансом sum_R и DD")
