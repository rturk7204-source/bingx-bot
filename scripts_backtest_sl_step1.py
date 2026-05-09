"""
Шаг 1: тестируем ТОЛЬКО SL multiplier (1.0..2.5).
Всё остальное = текущая прод-сборка (RR=1.7, ATR>=0.4%, anti-countertrend ch24<3%).
"""
import statistics, json
from assistant.core.exchange import get_klines

SYMBOLS = [
    "BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT","ADA-USDT",
    "BNB-USDT","LINK-USDT","AVAX-USDT","DOT-USDT","LTC-USDT",
    "TRX-USDT","NEAR-USDT","ATOM-USDT","ICP-USDT","APT-USDT","ARB-USDT","OP-USDT"
]

def find_swings(K, left=2, right=2):
    sw=[]
    for i in range(left, len(K)-right):
        h=K[i]['h']; l=K[i]['l']
        if all(K[i-j-1]['h']<h for j in range(left)) and all(K[i+j+1]['h']<h for j in range(right)):
            sw.append((i,h,'H'))
        if all(K[i-j-1]['l']>l for j in range(left)) and all(K[i+j+1]['l']>l for j in range(right)):
            sw.append((i,l,'L'))
    return sw

def atr(K, n=14):
    if len(K)<n+1: return 0
    trs=[max(K[i]['h']-K[i]['l'], abs(K[i]['h']-K[i-1]['c']), abs(K[i]['l']-K[i-1]['c']))
         for i in range(len(K)-n,len(K)) if i>=1]
    return sum(trs)/len(trs) if trs else 0

def detect_setup(K, i, min_break_atr=0.3):
    if i<50: return None
    win=K[max(0,i-100):i+1]
    sw=find_swings(win,2,2)
    if len(sw)<4: return None
    highs=[s for s in sw if s[2]=='H']; lows=[s for s in sw if s[2]=='L']
    if len(highs)<2 or len(lows)<2: return None
    last_h, last_l = highs[-1], lows[-1]
    a=atr(win,14)
    if a==0: return None
    last=win[-1]['c']
    mb=min_break_atr*a
    long_b=last>last_h[1]+mb
    short_b=last<last_l[1]-mb
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

def simulate(K, sig, rr, sl_atr_mult, max_bars=200):
    """SL = entry ± sl_atr_mult * ATR(14). RR от ЭТОГО нового R."""
    ep=sig['entry']; d=sig['dir']; a=sig['atr']
    R = sl_atr_mult * a
    if R==0: return None
    if d=='LONG':
        sl = ep - R
        tp = ep + rr*R
    else:
        sl = ep + R
        tp = ep - rr*R
    for i in range(sig['i']+1, min(sig['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        if d=='LONG':
            if l<=sl: return -1.0, i-sig['i']
            if h>=tp: return rr, i-sig['i']
        else:
            if h>=sl: return -1.0, i-sig['i']
            if l<=tp: return rr, i-sig['i']
    last=K[min(sig['i']+max_bars-1,len(K)-1)]['c']
    pnl=(last-ep)/R if d=='LONG' else (ep-last)/R
    return pnl, max_bars

def aggregate(trs, label):
    n=len(trs)
    if n==0: return {'label':label,'n':0}
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

print("Загружаю свечи 15m x 1000...")
data={}
for sym in SYMBOLS:
    try:
        r=get_klines(sym,'15m',1000).get('data') or []
        K=[]
        for k in r:
            try: K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'t':int(k['time'])})
            except: pass
        K=sorted(K,key=lambda x:x['t'])
        if len(K)>=100:
            data[sym]=K
    except Exception as e:
        print(f"  {sym}: err {e}")
print(f"символов: {len(data)}")

print("Ищу сетапы...")
all_setups=[]
for sym,K in data.items():
    for i in range(100, len(K)-50, 4):
        sig=detect_setup(K,i,0.3)
        if not sig: continue
        ch24=0
        if i>=96: ch24=(K[i]['c']/K[i-96]['c']-1)*100
        sig['ch24']=ch24
        sig['atr_pct']=sig['atr']/sig['entry']*100
        sig['symbol']=sym
        sig['K']=K
        all_setups.append(sig)
print(f"сетапов: {len(all_setups)}\n")

def filter_baseline(s):
    if s['atr_pct']<0.4: return False
    if s['dir']=='LONG' and s['ch24']<-3: return False
    if s['dir']=='SHORT' and s['ch24']>3: return False
    return True

print("=== STEP 1: SL = N * ATR(14), RR=1.7, baseline фильтры ===")
print(f"{'config':>14s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s}")
print("-"*60)
results={}
for sl_mult in [0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]:
    trs=[]
    for s in all_setups:
        if not filter_baseline(s): continue
        out=simulate(s['K'], s, 1.7, sl_mult)
        if out: trs.append(out)
    r=aggregate(trs, f'SL={sl_mult}xATR')
    results[f'sl_{sl_mult}']=r
    print(f"  SL={sl_mult:>4.2f}xATR | {r['n']:>4d} | {r.get('WR',0):>5.1f} | {r.get('avg_R',0):>+7.3f} | {r.get('sum_R',0):>+8.1f} | {r.get('max_DD',0):>7.1f}")

print("\n=== Контроль: текущая прод (структурный SL, RR=1.7) ===")
trs=[]
for s in all_setups:
    if not filter_baseline(s): continue
    ep=s['entry']; sl0=s['sl']; d=s['dir']; a=s['atr']
    R=abs(ep-sl0)
    if R==0: continue
    tp=ep+1.7*R if d=='LONG' else ep-1.7*R
    K=s['K']
    out=None
    for i in range(s['i']+1, min(s['i']+200, len(K))):
        h=K[i]['h']; l=K[i]['l']
        if d=='LONG':
            if l<=sl0: out=(-1.0,i-s['i']); break
            if h>=tp: out=(1.7,i-s['i']); break
        else:
            if h>=sl0: out=(-1.0,i-s['i']); break
            if l<=tp: out=(1.7,i-s['i']); break
    if not out:
        last=K[min(s['i']+199,len(K)-1)]['c']
        pnl=(last-ep)/R if d=='LONG' else (ep-last)/R
        out=(pnl,200)
    # средний R / ATR
    s['_struct_r_atr']=R/a if a else 0
    trs.append(out)
r=aggregate(trs,'STRUCT_SL')
results['struct']=r
print(f"  STRUCT_SL    | {r['n']:>4d} | {r.get('WR',0):>5.1f} | {r.get('avg_R',0):>+7.3f} | {r.get('sum_R',0):>+8.1f} | {r.get('max_DD',0):>7.1f}")

# средний размер структурного R/ATR
ratios=[s.get('_struct_r_atr',0) for s in all_setups if s.get('_struct_r_atr',0)>0 and filter_baseline(s)]
if ratios:
    print(f"\nСредний структурный R/ATR = {statistics.mean(ratios):.2f} (медиана {statistics.median(ratios):.2f})")

with open('/tmp/sl_step1.json','w') as f:
    json.dump(results,f,indent=2,ensure_ascii=False)
print("\nрезультаты в /tmp/sl_step1.json")
