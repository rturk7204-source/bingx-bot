"""
Шаг 10b: out-of-sample через прямой API с endTime.
"""
import statistics, json, time
from assistant.core.exchange import request

SYMBOLS = [
    "BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT","ADA-USDT",
    "BNB-USDT","LINK-USDT","AVAX-USDT","DOT-USDT","LTC-USDT",
    "TRX-USDT","NEAR-USDT","ATOM-USDT","ICP-USDT","APT-USDT","ARB-USDT","OP-USDT",
    "JASMY-USDT","JUP-USDT","UNI-USDT","ENS-USDT"
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

def sim_chandelier(K, s, max_bars=200, act_R=2.0, ch_period=22, ch_mult=3.0):
    ep=s['entry']; sl0=s['sl']; d=s['dir']; a0=s['atr']
    R=abs(ep-sl0)
    if R==0: return None
    sl=sl0; be_done=False; activated=False
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        if d=='LONG' and l<=sl: return ((sl-ep)/R, i-s['i'])
        if d=='SHORT' and h>=sl: return ((ep-sl)/R, i-s['i'])
        cur_R=(h-ep)/R if d=='LONG' else (ep-l)/R
        if not be_done and cur_R>=1.0:
            if d=='LONG' and ep>sl: sl=ep
            if d=='SHORT' and ep<sl: sl=ep
            be_done=True
        if cur_R>=act_R: activated=True
        if activated:
            j0=max(0,i-ch_period+1); window=K[j0:i+1]
            a_now=atr_of(K[max(0,i-30):i+1],14) or a0
            if d=='LONG':
                hh=max(k['h'] for k in window); new_sl=hh-ch_mult*a_now
                if new_sl>sl: sl=new_sl
            else:
                ll=min(k['l'] for k in window); new_sl=ll+ch_mult*a_now
                if new_sl<sl: sl=new_sl
    last=K[min(s['i']+max_bars-1,len(K)-1)]['c']
    pnl=(last-ep)/R if d=='LONG' else (ep-last)/R
    return (pnl, max_bars)

def aggregate(trs, label):
    n=len(trs)
    if n==0: return {'label':label,'n':0,'WR':0,'avg_R':0,'sum_R':0,'max_DD':0}
    pnls=[t[0] for t in trs]
    wins=[p for p in pnls if p>0.1]
    eq=[0]
    for p in pnls: eq.append(eq[-1]+p)
    peak=0; dd=0
    for e in eq:
        peak=max(peak,e); dd=min(dd,e-peak)
    return {'label':label,'n':n,'WR':round(len(wins)/n*100,1),
            'avg_R':round(statistics.mean(pnls),3),'sum_R':round(sum(pnls),1),
            'max_DD':round(dd,1)}

def fetch_period(end_ms=None):
    data={}
    for sym in SYMBOLS:
        try:
            r=klines(sym, '15m', 1000, end_ms).get('data') or []
            K=[]
            for k in r:
                try: K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'t':int(k['time'])})
                except: pass
            K=sorted(K,key=lambda x:x['t'])
            if len(K)>=100: data[sym]=K
        except Exception as e:
            print(f"  {sym}: err {e}")
    return data

def find_setups(data):
    setups=[]
    for sym,K in data.items():
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
            if sig['dir']=='LONG'  and sig['ch4']<-1.5: continue
            if sig['dir']=='SHORT' and sig['ch4']>1.5:  continue
            if sig['dir']=='LONG'  and sig['ch24']<-3: continue
            if sig['dir']=='SHORT' and sig['ch24']>3:  continue
            cur=K[i]
            body=abs(cur['c']-cur['o']) or 1e-12
            if sig['dir']=='LONG':
                upper=cur['h']-max(cur['c'],cur['o'])
                sig['wick']=upper/body
            else:
                lower=min(cur['c'],cur['o'])-cur['l']
                sig['wick']=lower/body
            setups.append(sig)
    return setups

def run(setups, side_filter, wick_max, label):
    trs=[]
    for s in setups:
        if side_filter=='short_only' and s['dir']=='SHORT' and s['wick']>wick_max: continue
        out=sim_chandelier(s['K'], s)
        if out: trs.append(out)
    return aggregate(trs, label)

# === IN-SAMPLE ===
print("=== IN-SAMPLE (последние 1000 свечей) ===")
data_is = fetch_period(None)
ts_min_is = min(K[0]['t'] for K in data_is.values()) if data_is else 0
ts_max_is = max(K[-1]['t'] for K in data_is.values()) if data_is else 0
print(f"символов: {len(data_is)}  диапазон: {time.strftime('%Y-%m-%d', time.localtime(ts_min_is/1000))} → {time.strftime('%Y-%m-%d', time.localtime(ts_max_is/1000))}")
setups_is = find_setups(data_is)
print(f"сетапов: {len(setups_is)}\n")

# === OOS период 1: 1000 свечей раньше ===
oos1_end_ms = ts_min_is - 1
print(f"=== OOS-1 (1000 свечей до {time.strftime('%Y-%m-%d', time.localtime(oos1_end_ms/1000))}) ===")
data_oos1 = fetch_period(oos1_end_ms)
print(f"символов: {len(data_oos1)}")
if data_oos1:
    ts_min_o = min(K[0]['t'] for K in data_oos1.values()); ts_max_o = max(K[-1]['t'] for K in data_oos1.values())
    print(f"диапазон: {time.strftime('%Y-%m-%d', time.localtime(ts_min_o/1000))} → {time.strftime('%Y-%m-%d', time.localtime(ts_max_o/1000))}")
setups_oos1 = find_setups(data_oos1)
print(f"сетапов: {len(setups_oos1)}\n")

# === OOS период 2: ещё раньше ===
oos2_end_ms = None
if data_oos1:
    oos2_end_ms = min(K[0]['t'] for K in data_oos1.values()) - 1
    print(f"=== OOS-2 (1000 свечей до {time.strftime('%Y-%m-%d', time.localtime(oos2_end_ms/1000))}) ===")
    data_oos2 = fetch_period(oos2_end_ms)
    print(f"символов: {len(data_oos2)}")
    if data_oos2:
        ts_min_o2 = min(K[0]['t'] for K in data_oos2.values()); ts_max_o2 = max(K[-1]['t'] for K in data_oos2.values())
        print(f"диапазон: {time.strftime('%Y-%m-%d', time.localtime(ts_min_o2/1000))} → {time.strftime('%Y-%m-%d', time.localtime(ts_max_o2/1000))}")
    setups_oos2 = find_setups(data_oos2)
    print(f"сетапов: {len(setups_oos2)}\n")
else:
    setups_oos2 = []

THRESHOLDS=[0.2,0.3,0.4,0.5]

for name, setups in [('IN-SAMPLE', setups_is), ('OOS-1', setups_oos1), ('OOS-2', setups_oos2)]:
    if not setups:
        print(f"\n{name}: пусто, скип")
        continue
    print(f"\n=== {name}: SHORT wick фильтр ===")
    print(f"{'threshold':>10s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD|")
    print("-"*88)
    prod = run(setups, 'none', 99, 'PROD')
    print(f"  {'PROD':>10s} | {prod['n']:>4d} | {prod['WR']:>5.1f} | {prod['avg_R']:>+7.3f} | {prod['sum_R']:>+8.1f} | {prod['max_DD']:>7.1f} | {prod['sum_R']/abs(prod['max_DD']) if prod['max_DD'] else 0:>7.2f}")
    for t in THRESHOLDS:
        r=run(setups, 'short_only', t, f'S≤{t}')
        sd=r['sum_R']/abs(r['max_DD']) if r['max_DD'] else 0
        delta=r['sum_R']-prod['sum_R']
        cnt_s = len([s for s in setups if s['dir']=='SHORT'])
        cnt_s_kept = len([s for s in setups if s['dir']=='SHORT' and s['wick']<=t])
        print(f"  {f'S≤{t}':>10s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f}  Δ={delta:+.1f}R  shorts {cnt_s_kept}/{cnt_s}")
