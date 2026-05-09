"""
Шаг 5: финальная проверка комбинации.
Прогоняем 4 сборки на одних данных:
  A) PROD (что в проде сейчас): mb=0.3, ATR>=0.4, ch24, h1, mom, fade, chandelier act+2R 3xATR
  B) OPTIMAL_solo (только убираем mom+fade)
  C) NO_MIN_BREAK (только mb=0, остальное прод)
  D) FINAL (mb=0 + no_mom + no_fade) — гипотеза: комбинированный максимум
  E) FINAL_strict (FINAL + h1_trend выкл) — на всякий, проверка
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

def detect_setup(K, i, min_break_atr):
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

# собираем сетапы для двух значений mb
print("Ищу сетапы (mb=0.3 и mb=0.0)...")
setups={'mb_0.3':[], 'mb_0.0':[]}
for mb_key, mb in [('mb_0.3', 0.3), ('mb_0.0', 0.0)]:
    for sym,K in data.items():
        for i in range(100, len(K)-50, 4):
            sig=detect_setup(K,i,mb)
            if not sig: continue
            ch24=0; ch2h=0; ch4=0
            if i>=96: ch24=(K[i]['c']/K[i-96]['c']-1)*100
            if i>=8:  ch2h=(K[i]['c']/K[i-8]['c']-1)*100
            if i>=16: ch4=(K[i]['c']/K[i-16]['c']-1)*100
            sig['ch24']=ch24; sig['ch2h']=ch2h; sig['ch4']=ch4
            sig['atr_pct']=sig['atr']/sig['entry']*100
            d=sig['dir']
            if i>=2:
                c1=K[i-1]; c2=K[i-2]
                red1=c1['c']<c1['o']; red2=c2['c']<c2['o']
                grn1=c1['c']>c1['o']; grn2=c2['c']>c2['o']
                if d=='LONG': sig['fade']= red1 and red2
                else:         sig['fade']= grn1 and grn2
            else: sig['fade']=False
            sig['symbol']=sym; sig['K']=K
            setups[mb_key].append(sig)
print(f"  mb_0.3: {len(setups['mb_0.3'])}  mb_0.0: {len(setups['mb_0.0'])}\n")

def passes(s, cfg):
    if cfg['atr'] and s['atr_pct']<0.4: return False
    if cfg['ch24']:
        if s['dir']=='LONG'  and s['ch24']<-3: return False
        if s['dir']=='SHORT' and s['ch24']>3:  return False
    if cfg['h1']:
        if s['dir']=='LONG'  and s['ch4']<-1.5: return False
        if s['dir']=='SHORT' and s['ch4']>1.5:  return False
    if cfg['mom']:
        if s['dir']=='LONG'  and s['ch24']>2 and s['ch2h']<-0.5: return False
        if s['dir']=='SHORT' and s['ch24']<-2 and s['ch2h']>0.5: return False
    if cfg['fade'] and s.get('fade',False): return False
    return True

def run(setups_list, cfg, label):
    trs=[]
    for s in setups_list:
        if not passes(s, cfg): continue
        out=sim_chandelier(s['K'], s)
        if out: trs.append(out)
    return aggregate(trs, label)

CFGS = [
    ('A_PROD',           'mb_0.3', {'atr':True,'ch24':True,'h1':True,'mom':True,'fade':True}),
    ('B_no_mom_fade',    'mb_0.3', {'atr':True,'ch24':True,'h1':True,'mom':False,'fade':False}),
    ('C_mb0_only',       'mb_0.0', {'atr':True,'ch24':True,'h1':True,'mom':True,'fade':True}),
    ('D_FINAL',          'mb_0.0', {'atr':True,'ch24':True,'h1':True,'mom':False,'fade':False}),
    ('E_FINAL_no_h1',    'mb_0.0', {'atr':True,'ch24':True,'h1':False,'mom':False,'fade':False}),
]

print("=== STEP 5: комбинированные сборки на chandelier act+2R 3xATR ===")
print(f"{'config':>20s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s} | sum/|DD|")
print("-"*82)
results={}
prod_sum=None
for name, mb, cfg in CFGS:
    r = run(setups[mb], cfg, name)
    results[name]=r
    if name=='A_PROD': prod_sum=r['sum_R']
    delta = r['sum_R'] - prod_sum if prod_sum is not None else 0
    sd = r['sum_R']/abs(r['max_DD']) if r['max_DD']!=0 else 0
    mark = "+" if delta>0 else ""
    print(f"  {name:>18s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f} | {sd:>7.2f}  Δ={mark}{delta:+.1f}R")

with open('/tmp/step5.json','w') as f:
    json.dump(results,f,indent=2,ensure_ascii=False)
print("\nрезультаты в /tmp/step5.json")
print("\nЛегенда:")
print("  A_PROD          = текущий прод (mb=0.3, ch24, h1, mom, fade)")
print("  B_no_mom_fade   = убираем mom+fade, остальное прод")
print("  C_mb0_only      = только mb=0, остальное прод")
print("  D_FINAL         = mb=0 + no_mom + no_fade (гипотеза максимума)")
print("  E_FINAL_no_h1   = D + h1 выключен (sanity)")
