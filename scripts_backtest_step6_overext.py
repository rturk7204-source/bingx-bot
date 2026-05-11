"""
Шаг 6: тест нового симметричного anti-overextension фильтра.
Сетка: меняем ОДНУ строку логики ch24:
  - PROD: LONG reject если ch24<-3,  SHORT reject если ch24>+3  (anti-countertrend)
  - HYP_A: LONG reject если |ch24|>3 ИЛИ ch24<-3 (отрезаем JASMY-like перекупы LONG)
  - HYP_B: то же, но порог +2.5
  - HYP_C: то же, но порог +2.0
  - HYP_D: вообще выключить ch24 для sanity

Базовая сборка такая же как в проде после step5: mb=0, no_mom, no_fade, h1 on, atr>=0.4, chandelier act+2R 3xATR.
"""
import statistics, json
from assistant.core.exchange import get_klines

SYMBOLS = [
    "BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT","ADA-USDT",
    "BNB-USDT","LINK-USDT","AVAX-USDT","DOT-USDT","LTC-USDT",
    "TRX-USDT","NEAR-USDT","ATOM-USDT","ICP-USDT","APT-USDT","ARB-USDT","OP-USDT",
    "JASMY-USDT","JUP-USDT","UNI-USDT","ENS-USDT"  # включаем виновников
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

def detect_setup(K, i, min_break_atr=0.0):
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

def sim_chandelier(K, s, max_bars=200, act_R=2.0, ch_period=22, ch_mult=3.0):
    ep=s['entry']; sl0=s['sl']; d=s['dir']; a0=s['atr']
    R=abs(ep-sl0)
    if R==0: return None
    sl=sl0; activated=False
    for i in range(s['i']+1, min(s['i']+max_bars, len(K))):
        h=K[i]['h']; l=K[i]['l']
        if d=='LONG' and l<=sl: return ((sl-ep)/R, i-s['i'])
        if d=='SHORT' and h>=sl: return ((ep-sl)/R, i-s['i'])
        cur_R=(h-ep)/R if d=='LONG' else (ep-l)/R
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

print("Ищу сетапы (mb=0)...")
setups=[]
for sym,K in data.items():
    for i in range(100, len(K)-50, 4):
        sig=detect_setup(K,i,0.0)
        if not sig: continue
        ch24=0; ch4=0
        if i>=96: ch24=(K[i]['c']/K[i-96]['c']-1)*100
        if i>=16: ch4=(K[i]['c']/K[i-16]['c']-1)*100
        sig['ch24']=ch24; sig['ch4']=ch4
        sig['atr_pct']=sig['atr']/sig['entry']*100
        sig['symbol']=sym; sig['K']=K
        setups.append(sig)
print(f"  всего сетапов: {len(setups)}\n")

def passes(s, ch24_mode):
    # ATR >= 0.4
    if s['atr_pct']<0.4: return False
    # h1 trend ±1.5
    if s['dir']=='LONG'  and s['ch4']<-1.5: return False
    if s['dir']=='SHORT' and s['ch4']>1.5:  return False
    # ch24 правила
    if ch24_mode == 'PROD':           # anti-countertrend (текущий)
        if s['dir']=='LONG'  and s['ch24']<-3: return False
        if s['dir']=='SHORT' and s['ch24']>3:  return False
    elif ch24_mode == 'SYM_3':        # симметричный |ch24|<=3
        if abs(s['ch24'])>3: return False
    elif ch24_mode == 'SYM_2_5':
        if abs(s['ch24'])>2.5: return False
    elif ch24_mode == 'SYM_2':
        if abs(s['ch24'])>2: return False
    elif ch24_mode == 'SYM_4':
        if abs(s['ch24'])>4: return False
    elif ch24_mode == 'OFF':
        pass
    return True

def run(mode, label):
    trs=[]
    for s in setups:
        if not passes(s, mode): continue
        out=sim_chandelier(s['K'], s)
        if out: trs.append(out)
    return aggregate(trs, label)

CFGS = [
    ('PROD',     'A_PROD (anti-countertrend, текущий)'),
    ('SYM_4',    'B_SYM_|ch24|≤4'),
    ('SYM_3',    'C_SYM_|ch24|≤3 (гипотеза)'),
    ('SYM_2_5',  'D_SYM_|ch24|≤2.5'),
    ('SYM_2',    'E_SYM_|ch24|≤2 (агрессив)'),
    ('OFF',      'F_OFF (sanity)'),
]

print("=== STEP 6: симметричный anti-overext, остальное = step5_final ===")
print(f"{'config':>30s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD|  Δ")
print("-"*100)
results={}
prod_sum=None
for mode, label in CFGS:
    r = run(mode, label)
    results[label]=r
    if mode=='PROD': prod_sum=r['sum_R']
    delta = r['sum_R'] - prod_sum if prod_sum is not None else 0
    sd = r['sum_R']/abs(r['max_DD']) if r['max_DD']!=0 else 0
    print(f"  {label:>28s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f}  Δ={delta:+.1f}R")

# отдельно — проверка как фильтр повлиял бы на JASMY-like (ch24>3 LONG)
print("\n=== Срез: сколько LONG с ch24>+3 отрезается ===")
overext_long = [s for s in setups if s['dir']=='LONG' and s['ch24']>3]
overext_long_4 = [s for s in setups if s['dir']=='LONG' and s['ch24']>4]
print(f"  LONG с ch24>+3: {len(overext_long)} setups")
print(f"  LONG с ch24>+4: {len(overext_long_4)} setups")
# их фактический PnL
trs_ovr3 = [sim_chandelier(s['K'],s) for s in overext_long]
trs_ovr3 = [t for t in trs_ovr3 if t]
if trs_ovr3:
    print(f"  их сумма R (LONG ch24>+3): {sum(t[0] for t in trs_ovr3):+.1f}R   WR {len([t for t in trs_ovr3 if t[0]>0.1])/len(trs_ovr3)*100:.0f}%")
trs_ovr4 = [sim_chandelier(s['K'],s) for s in overext_long_4]
trs_ovr4 = [t for t in trs_ovr4 if t]
if trs_ovr4:
    print(f"  их сумма R (LONG ch24>+4): {sum(t[0] for t in trs_ovr4):+.1f}R   WR {len([t for t in trs_ovr4 if t[0]>0.1])/len(trs_ovr4)*100:.0f}%")

with open('/tmp/step6.json','w') as f:
    json.dump(results,f,indent=2,ensure_ascii=False)
print("\nрезультаты в /tmp/step6.json")
