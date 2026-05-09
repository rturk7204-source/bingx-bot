"""
Шаг 2: ищем где утекает EV при текущем структурном SL.
EXP A: разные TP (1.3..3.0 + no_TP=timeout-only)
EXP B: разные timeout (16..500 баров)
EXP C: chandelier-trail активируется ПОСЛЕ +1R (high-22 - 3*ATR)
EXP D: distribution закрытий (SL / TP / timeout) для текущей прод
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

def atr_of(K, n=14):
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
    a=atr_of(win,14)
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

def filter_baseline(s):
    if s['atr_pct']<0.4: return False
    if s['dir']=='LONG' and s['ch24']<-3: return False
    if s['dir']=='SHORT' and s['ch24']>3: return False
    return True

def sim_fixed(K, s, rr, max_bars=200):
    """RR-фикс TP, структурный SL, timeout по close."""
    ep=s['entry']; sl=s['sl']; d=s['dir']
    R=abs(ep-sl)
    if R==0: return None
    tp = ep + rr*R if d=='LONG' else ep - rr*R
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        if d=='LONG':
            if l<=sl: return (-1.0, i-s['i'], 'SL')
            if h>=tp: return (rr, i-s['i'], 'TP')
        else:
            if h>=sl: return (-1.0, i-s['i'], 'SL')
            if l<=tp: return (rr, i-s['i'], 'TP')
    last=K[min(s['i']+max_bars-1,len(K)-1)]['c']
    pnl=(last-ep)/R if d=='LONG' else (ep-last)/R
    return (pnl, max_bars, 'TO')

def sim_no_tp(K, s, max_bars):
    """Только структурный SL + timeout (без TP)."""
    ep=s['entry']; sl=s['sl']; d=s['dir']
    R=abs(ep-sl)
    if R==0: return None
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        if d=='LONG' and l<=sl: return (-1.0,i-s['i'],'SL')
        if d=='SHORT' and h>=sl: return (-1.0,i-s['i'],'SL')
    last=K[min(s['i']+max_bars-1,len(K)-1)]['c']
    pnl=(last-ep)/R if d=='LONG' else (ep-last)/R
    return (pnl, max_bars, 'TO')

def sim_chandelier(K, s, activate_at_R, ch_period=22, ch_mult=3.0, max_bars=200):
    """
    SL структурный до достижения +activate_at_R.
    После — trailing chandelier: max(high22) - ch_mult*ATR(14) (LONG)
                                  min(low22) + ch_mult*ATR(14) (SHORT)
    SL только подтягивается (никогда не ослабляется).
    """
    ep=s['entry']; sl0=s['sl']; d=s['dir']; a0=s['atr']
    R=abs(ep-sl0)
    if R==0: return None
    sl=sl0
    activated=False
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        # проверка SL
        if d=='LONG' and l<=sl:
            pnl=(sl-ep)/R; return (pnl, i-s['i'], 'SL' if not activated else 'TR')
        if d=='SHORT' and h>=sl:
            pnl=(ep-sl)/R; return (pnl, i-s['i'], 'SL' if not activated else 'TR')
        # активация trail при +activate_at_R MFE
        cur_R = (h-ep)/R if d=='LONG' else (ep-l)/R
        if cur_R >= activate_at_R: activated=True
        if activated:
            j0=max(0, i-ch_period+1)
            window=K[j0:i+1]
            a_now=atr_of(K[max(0,i-30):i+1],14) or a0
            if d=='LONG':
                hh=max(k['h'] for k in window)
                new_sl = hh - ch_mult*a_now
                if new_sl>sl: sl=new_sl
            else:
                ll=min(k['l'] for k in window)
                new_sl = ll + ch_mult*a_now
                if new_sl<sl: sl=new_sl
    last=K[min(s['i']+max_bars-1,len(K)-1)]['c']
    pnl=(last-ep)/R if d=='LONG' else (ep-last)/R
    return (pnl, max_bars, 'TO')

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

print("Загружаю свечи...")
data={}
for sym in SYMBOLS:
    try:
        r=get_klines(sym,'15m',1000).get('data') or []
        K=[]
        for k in r:
            try: K.append({'h':float(k['high']),'l':float(k['low']),'c':float(k['close']),'o':float(k['open']),'t':int(k['time'])})
            except: pass
        K=sorted(K,key=lambda x:x['t'])
        if len(K)>=100: data[sym]=K
    except Exception as e: print(f" {sym}: err {e}")
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

results={}

print("=== EXP A: разные TP (структурный SL, timeout=200) ===")
print(f"{'TP':>10s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s}")
print("-"*60)
for rr in [1.3, 1.5, 1.7, 2.0, 2.5, 3.0]:
    trs=[]
    for s in all_setups:
        if not filter_baseline(s): continue
        out=sim_fixed(s['K'], s, rr, 200)
        if out: trs.append(out)
    r=aggregate(trs, f'TP={rr}R')
    results[f'tp_{rr}']=r
    print(f"  TP={rr:>4.1f}R | {r['n']:>4d} | {r.get('WR',0):>5.1f} | {r.get('avg_R',0):>+7.3f} | {r.get('sum_R',0):>+8.1f} | {r.get('max_DD',0):>7.1f}")
# no TP
trs=[]
for s in all_setups:
    if not filter_baseline(s): continue
    out=sim_no_tp(s['K'], s, 200)
    if out: trs.append(out)
r=aggregate(trs,'noTP_to=200')
results['no_tp_200']=r
print(f"  no TP/to200 | {r['n']:>4d} | {r.get('WR',0):>5.1f} | {r.get('avg_R',0):>+7.3f} | {r.get('sum_R',0):>+8.1f} | {r.get('max_DD',0):>7.1f}")

print("\n=== EXP B: разные timeout (RR=1.7, структурный SL) ===")
print(f"{'timeout':>10s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s}")
print("-"*60)
for tb in [16, 33, 50, 100, 200, 500]:
    trs=[]
    for s in all_setups:
        if not filter_baseline(s): continue
        out=sim_fixed(s['K'], s, 1.7, tb)
        if out: trs.append(out)
    r=aggregate(trs, f'to={tb}')
    results[f'to_{tb}']=r
    print(f"   to={tb:>4d}b | {r['n']:>4d} | {r.get('WR',0):>5.1f} | {r.get('avg_R',0):>+7.3f} | {r.get('sum_R',0):>+8.1f} | {r.get('max_DD',0):>7.1f}")

print("\n=== EXP C: Chandelier trail после +X R (структурный SL до активации) ===")
print(f"{'config':>20s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s}")
print("-"*70)
for act_R in [0.5, 1.0, 1.5, 2.0]:
    for ch_mult in [2.0, 3.0]:
        trs=[]
        for s in all_setups:
            if not filter_baseline(s): continue
            out=sim_chandelier(s['K'], s, act_R, 22, ch_mult, 200)
            if out: trs.append(out)
        r=aggregate(trs, f'act{act_R}_m{ch_mult}')
        results[f'ch_a{act_R}_m{ch_mult}']=r
        print(f"  act+{act_R}R / {ch_mult}xATR | {r['n']:>4d} | {r.get('WR',0):>5.1f} | {r.get('avg_R',0):>+7.3f} | {r.get('sum_R',0):>+8.1f} | {r.get('max_DD',0):>7.1f}")

print("\n=== EXP D: distribution закрытий (текущая прод RR=1.7, to=200) ===")
trs=[]
for s in all_setups:
    if not filter_baseline(s): continue
    out=sim_fixed(s['K'], s, 1.7, 200)
    if out: trs.append(out)
exits={'SL':[],'TP':[],'TO':[]}
for pnl, bars, why in trs: exits[why].append(pnl)
print(f"  всего сделок: {len(trs)}")
for k in ['SL','TP','TO']:
    arr=exits[k]; pct=len(arr)/len(trs)*100 if trs else 0
    avg=statistics.mean(arr) if arr else 0
    s_=sum(arr)
    print(f"  {k}: {len(arr):>4d} ({pct:>5.1f}%) | avg={avg:+.3f}R | sum={s_:+.1f}R")
results['exit_dist']={k:{'n':len(v),'avg':round(statistics.mean(v),3) if v else 0,'sum':round(sum(v),1)} for k,v in exits.items()}

with open('/tmp/step2.json','w') as f:
    json.dump(results,f,indent=2,ensure_ascii=False)
print("\nрезультаты в /tmp/step2.json")
