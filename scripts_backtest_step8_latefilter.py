"""
Шаг 8: фильтры «поздних входов внутри импульса».

База = step5_final: mb=0, ATR>=0.4, ch24 anti-countertrend, h1 ±1.5, no_mom, no_fade,
chandelier act+2R 3xATR, BE при +1R → entry.

Гипотезы:
  PROD     — без новых фильтров
  A_pull   — LONG только если entry <= last_swing_high - 0.3*ATR (откатились после пробоя)
             SHORT только если entry >= last_swing_low + 0.3*ATR
  A_pull05 — то же, но 0.5*ATR
  A_pull10 — то же, но 1.0*ATR
  C_range  — LONG только если (entry - low24) / (high24 - low24) <= 0.85 (не в верхних 15% диапазона)
             SHORT только если >= 0.15 (не в нижних 15%)
  C_range7 — то же, но порог 0.75
  AC       — A_pull05 + C_range
  Wick     — отсечь свечу пробоя с длинным wick: для LONG upper_wick / body > 0.6 → reject
  AC_wick  — комбо A_pull05 + C_range + Wick

И отдельно проверим: сколько из JASMY-like (LONG ch24>3) сохранится и какой их PnL.
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
        return {'dir':'LONG','entry':last,'sl':sl,'atr':a,'i':i,'swing_h':last_h[1],'swing_l':last_l[1]}
    if short_b:
        sl_idx=max(0,last_l[0]-3)
        sl=max(K[max(0,i-100)+sl_idx:max(0,i-100)+last_l[0]+1], key=lambda k:k['h'])['h']
        return {'dir':'SHORT','entry':last,'sl':sl,'atr':a,'i':i,'swing_h':last_h[1],'swing_l':last_l[1]}
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
        # BE +1R → entry
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

print("Ищу сетапы (база step5_final)...")
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
        # суточный диапазон (96 баров = 24ч на 15m)
        if i>=96:
            win24=K[i-96:i+1]
            h24=max(k['h'] for k in win24); l24=min(k['l'] for k in win24)
            sig['high24']=h24; sig['low24']=l24
            if h24>l24: sig['pos_in_range']=(sig['entry']-l24)/(h24-l24)
            else: sig['pos_in_range']=0.5
        else:
            sig['pos_in_range']=0.5
        # wick свечи входа
        cur=K[i]
        body=abs(cur['c']-cur['o']) or 0.0001
        if sig['dir']=='LONG':
            upper_wick=cur['h']-max(cur['c'],cur['o'])
            sig['wick_ratio']=upper_wick/body
        else:
            lower_wick=min(cur['c'],cur['o'])-cur['l']
            sig['wick_ratio']=lower_wick/body
        # расстояние от swing high/low до входа в ATR
        if sig['dir']=='LONG':
            sig['dist_sw']=(sig['swing_h']-sig['entry'])/sig['atr']  # отрицательное если выше swing
        else:
            sig['dist_sw']=(sig['entry']-sig['swing_l'])/sig['atr']
        setups.append(sig)

print(f"  сетапов после базовых фильтров: {len(setups)}\n")

def passes(s, cfg):
    if cfg.get('pull'):
        # для LONG: вход должен быть >= swing_h + pull*ATR (т.е. пробили высоко) — обратный знак
        # на самом деле мы хотим обратное: вход НЕ должен быть прямо у swing_h
        # entry должен отступить от swing_h хотя бы на pull*ATR ВНИЗ (после отката),
        # либо пробить высоко ВВЕРХ. Берём: |entry - swing_h| >= pull*ATR
        if s['dir']=='LONG':
            if abs(s['entry']-s['swing_h'])/s['atr'] < cfg['pull']: return False
        else:
            if abs(s['entry']-s['swing_l'])/s['atr'] < cfg['pull']: return False
    if cfg.get('range_top') is not None:
        if s['dir']=='LONG' and s['pos_in_range'] > cfg['range_top']: return False
        if s['dir']=='SHORT' and s['pos_in_range'] < (1-cfg['range_top']): return False
    if cfg.get('wick_max') is not None:
        if s['wick_ratio'] > cfg['wick_max']: return False
    return True

def run(cfg, label):
    trs=[]; kept=0
    for s in setups:
        if not passes(s, cfg): continue
        kept+=1
        out=sim_chandelier(s['K'], s)
        if out: trs.append(out)
    return aggregate(trs, label), kept

CFGS = [
    ('PROD',       {}),
    ('A_pull03',   {'pull':0.3}),
    ('A_pull05',   {'pull':0.5}),
    ('A_pull10',   {'pull':1.0}),
    ('C_range85',  {'range_top':0.85}),
    ('C_range75',  {'range_top':0.75}),
    ('AC_05_85',   {'pull':0.5,'range_top':0.85}),
    ('AC_05_75',   {'pull':0.5,'range_top':0.75}),
    ('Wick06',     {'wick_max':0.6}),
    ('AC_wick',    {'pull':0.5,'range_top':0.85,'wick_max':0.6}),
]

print("=== STEP 8: фильтры поздних входов ===")
print(f"{'config':>15s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD|  Δ")
print("-"*92)
results={}
prod_sum=None
for label, cfg in CFGS:
    r, kept = run(cfg, label)
    results[label]=r
    if label=='PROD': prod_sum=r['sum_R']
    delta=r['sum_R']-prod_sum if prod_sum is not None else 0
    sd=r['sum_R']/abs(r['max_DD']) if r['max_DD']!=0 else 0
    print(f"  {label:>13s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f}  Δ={delta:+.1f}R")

# срез по «JASMY-like»: LONG с ch24>+3, и сколько каждый фильтр от них оставит
print("\n=== Срез: LONG ch24>+3 (JASMY-like) — сколько остаётся после каждого фильтра ===")
jasmy_like = [s for s in setups if s['dir']=='LONG' and s['ch24']>3]
print(f"  всего таких сетапов: {len(jasmy_like)}")
# их базовый PnL
trs_base=[sim_chandelier(s['K'],s) for s in jasmy_like]; trs_base=[t for t in trs_base if t]
if trs_base:
    print(f"  базовый PnL: sum {sum(t[0] for t in trs_base):+.1f}R   WR {len([t for t in trs_base if t[0]>0.1])/len(trs_base)*100:.0f}%")
for label, cfg in CFGS[1:]:
    kept=[s for s in jasmy_like if passes(s,cfg)]
    trs=[sim_chandelier(s['K'],s) for s in kept]; trs=[t for t in trs if t]
    if trs:
        s_sum=sum(t[0] for t in trs); wr=len([t for t in trs if t[0]>0.1])/len(trs)*100
        print(f"  {label:>13s}: оставлено {len(kept):>3d}/{len(jasmy_like)}   sum {s_sum:+6.1f}R   WR {wr:.0f}%")
    else:
        print(f"  {label:>13s}: оставлено 0")

with open('/tmp/step8.json','w') as f:
    json.dump(results,f,indent=2,ensure_ascii=False)
print("\nрезультаты в /tmp/step8.json")
