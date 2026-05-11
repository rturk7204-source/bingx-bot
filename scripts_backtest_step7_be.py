"""
Шаг 7: BE триггер по цене (%), а не по R.

Базовая сборка = step5_final: mb=0, ATR>=0.4, ch24 anti-countertrend, h1 ±1.5, no_mom, no_fade,
chandelier act+2R 3xATR.

К ней добавляем BE-логику (раньше чанделира):
  PROD:   BE триггер при +1R → SL = entry
  A:      BE триггер при +1% цены → SL = entry            (классический BE раньше)
  B:      BE триггер при +1% цены → SL = entry*(1+1%)     (замок +1% профита)
  C:      BE триггер при +0.5% цены → SL = entry          (агрессивнее A)
  D:      BE триггер при +0.5% цены → SL = entry*(1+0.5%) (микро-замок)
"""
import statistics, json
from assistant.core.exchange import get_klines

SYMBOLS = [
    "BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT","ADA-USDT",
    "BNB-USDT","LINK-USDT","AVAX-USDT","DOT-USDT","LTC-USDT",
    "TRX-USDT","NEAR-USDT","ATOM-USDT","ICP-USDT","APT-USDT","ARB-USDT","OP-USDT",
    "JASMY-USDT","JUP-USDT","UNI-USDT","ENS-USDT"
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

def sim(K, s, be_mode, max_bars=200, act_R=2.0, ch_period=22, ch_mult=3.0):
    """
    be_mode: dict {'trig':'R'|'pct', 'trig_val':float, 'lock_pct':float}
      trig='R': BE активируется когда profit>=trig_val R
      trig='pct': BE активируется когда |price-entry|/entry*100>=trig_val
      lock_pct: новый SL = entry*(1+lock_pct/100) для LONG (зеркально SHORT)
    """
    ep=s['entry']; sl0=s['sl']; d=s['dir']; a0=s['atr']
    R=abs(ep-sl0)
    if R==0: return None
    sl=sl0
    be_done=False; ch_activated=False
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        # стоп проверяем первым
        if d=='LONG' and l<=sl: return ((sl-ep)/R, i-s['i'])
        if d=='SHORT' and h>=sl: return ((ep-sl)/R, i-s['i'])
        # текущий профит
        if d=='LONG':
            cur_pct=(h-ep)/ep*100
            cur_R=(h-ep)/R
        else:
            cur_pct=(ep-l)/ep*100
            cur_R=(ep-l)/R
        # BE триггер
        if not be_done:
            trig_hit=False
            if be_mode['trig']=='R' and cur_R>=be_mode['trig_val']: trig_hit=True
            if be_mode['trig']=='pct' and cur_pct>=be_mode['trig_val']: trig_hit=True
            if trig_hit:
                if d=='LONG':
                    new_sl=ep*(1+be_mode['lock_pct']/100)
                    if new_sl>sl: sl=new_sl
                else:
                    new_sl=ep*(1-be_mode['lock_pct']/100)
                    if new_sl<sl: sl=new_sl
                be_done=True
        # chandelier act+2R
        if cur_R>=act_R: ch_activated=True
        if ch_activated:
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
    if n==0: return {'label':label,'n':0,'WR':0,'avg_R':0,'sum_R':0,'max_DD':0,'be_saves':0}
    pnls=[t[0] for t in trs]
    wins=[p for p in pnls if p>0.1]
    eq=[0]
    for p in pnls: eq.append(eq[-1]+p)
    peak=0; dd=0
    for e in eq:
        peak=max(peak,e); dd=min(dd,e-peak)
    # сколько сделок закрылись около BE (-0.1R..+0.2R) — индикатор «BE-кладбища»
    be_zone = len([p for p in pnls if -0.15<=p<=0.15])
    return {'label':label,'n':n,'WR':round(len(wins)/n*100,1),
            'avg_R':round(statistics.mean(pnls),3),'sum_R':round(sum(pnls),1),
            'max_DD':round(dd,1),'be_zone':be_zone}

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
        # ATR>=0.4
        if sig['atr_pct']<0.4: continue
        # h1 ±1.5
        if sig['dir']=='LONG'  and sig['ch4']<-1.5: continue
        if sig['dir']=='SHORT' and sig['ch4']>1.5:  continue
        # ch24 anti-countertrend
        if sig['dir']=='LONG'  and sig['ch24']<-3: continue
        if sig['dir']=='SHORT' and sig['ch24']>3:  continue
        # R-distance в %
        R_pct = abs(sig['entry']-sig['sl'])/sig['entry']*100
        sig['R_pct']=R_pct
        setups.append(sig)
print(f"  сетапов прошло фильтры: {len(setups)}")
# распределение R в %
R_pcts = sorted([s['R_pct'] for s in setups])
mid = R_pcts[len(R_pcts)//2]
print(f"  медианный R в %: {mid:.2f}%   min {R_pcts[0]:.2f}  max {R_pcts[-1]:.2f}\n")

CFGS = [
    ('PROD_BE_1R',          {'trig':'R',  'trig_val':1.0, 'lock_pct':0.0}),
    ('A_BE_1pct_entry',     {'trig':'pct','trig_val':1.0, 'lock_pct':0.0}),
    ('B_BE_1pct_lock1pct',  {'trig':'pct','trig_val':1.0, 'lock_pct':1.0}),
    ('C_BE_05pct_entry',    {'trig':'pct','trig_val':0.5, 'lock_pct':0.0}),
    ('D_BE_05pct_lock05',   {'trig':'pct','trig_val':0.5, 'lock_pct':0.5}),
    ('E_BE_15pct_entry',    {'trig':'pct','trig_val':1.5, 'lock_pct':0.0}),
]

print("=== STEP 7: BE по % vs по R, c chandelier act+2R 3xATR ===")
print(f"{'config':>22s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD| | BE-zone")
print("-"*100)
results={}
prod_sum=None
for label, bm in CFGS:
    trs=[]
    for s in setups:
        out=sim(s['K'], s, bm)
        if out: trs.append(out)
    r=aggregate(trs, label)
    results[label]=r
    if label=='PROD_BE_1R': prod_sum=r['sum_R']
    delta=r['sum_R']-prod_sum if prod_sum is not None else 0
    sd=r['sum_R']/abs(r['max_DD']) if r['max_DD']!=0 else 0
    print(f"  {label:>20s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f} | {r['be_zone']:>4d}   Δ={delta:+.1f}R")

with open('/tmp/step7.json','w') as f:
    json.dump(results,f,indent=2,ensure_ascii=False)
print("\nрезультаты в /tmp/step7.json")
print("\nЛегенда:")
print("  trig — что триггерит BE (R от риска, или % от цены)")
print("  lock_pct — что замыкаем (0=безубыток, 1=+1% профит)")
print("  BE-zone — сколько сделок закрылось около BE (−0.15..+0.15R) — показывает «кладбище»")
