"""
Шаг 4: ablation фильтров на лучшей сборке (chandelier act+2R, 3xATR, без TP).
Каждый фильтр включается/выключается ПО ОЧЕРЕДИ от baseline (всё включено).
Если выключение даёт +EV → фильтр вредит. Если -EV → фильтр полезен.

Фильтры:
F1: ATR_pct >= 0.4%
F2: ch24 anti-countertrend (LONG отказ ch24<-3%, SHORT отказ ch24>+3%)
F3: SMC min_break >= 0.3 ATR
F4: ADX >= 20 (НЕ в проде сейчас, проверим стоит ли вводить)
F5: BTC коррелированность ±0.7% (НЕ моделируется, скип)
F6: 1h тренд против < 1.5% — приближаем как ch4 (последние 16 баров)
F7: momentum-reversal (LONG отказ если ch24>+2% И ch2h<-0.5%)
F8: anti-fade (2 красные/зелёные свечи 15m против сделки)
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

def adx(K, n=14):
    if len(K)<n*2: return 0
    plus_dm=[]; minus_dm=[]; trs=[]
    for i in range(1,len(K)):
        up=K[i]['h']-K[i-1]['h']; dn=K[i-1]['l']-K[i]['l']
        plus_dm.append(up if up>dn and up>0 else 0)
        minus_dm.append(dn if dn>up and dn>0 else 0)
        trs.append(max(K[i]['h']-K[i]['l'], abs(K[i]['h']-K[i-1]['c']), abs(K[i]['l']-K[i-1]['c'])))
    if len(trs)<n: return 0
    a=sum(trs[-n:])/n
    if a==0: return 0
    pdi=100*sum(plus_dm[-n:])/n/a
    mdi=100*sum(minus_dm[-n:])/n/a
    if pdi+mdi==0: return 0
    return 100*abs(pdi-mdi)/(pdi+mdi)

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

# Кэшируем сетапы дважды: с min_break=0.3 (текущий прод) и с 0.0 (отключён)
print("Ищу сетапы...")
all_setups={'mb_0.3':[], 'mb_0.0':[]}
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
            sig['adx']=adx(K[max(0,i-30):i+1],14)
            sig['atr_pct']=sig['atr']/sig['entry']*100
            # anti-fade: 2 свечи против сделки подряд перед входом
            d=sig['dir']
            if i>=2:
                c1=K[i-1]; c2=K[i-2]
                red1=c1['c']<c1['o']; red2=c2['c']<c2['o']
                grn1=c1['c']>c1['o']; grn2=c2['c']>c2['o']
                if d=='LONG': sig['fade']= red1 and red2  # 2 красные перед лонгом
                else:         sig['fade']= grn1 and grn2
            else: sig['fade']=False
            sig['symbol']=sym; sig['K']=K
            all_setups[mb_key].append(sig)
print(f"сетапов mb_0.3: {len(all_setups['mb_0.3'])}, mb_0.0: {len(all_setups['mb_0.0'])}\n")

# === функции фильтров ===
def f_atr(s, on=True):  return (not on) or (s['atr_pct']>=0.4)
def f_ch24(s, on=True):
    if not on: return True
    if s['dir']=='LONG'  and s['ch24']<-3: return False
    if s['dir']=='SHORT' and s['ch24']>3:  return False
    return True
def f_adx(s, on=False, thr=20):
    if not on: return True
    return s['adx']>=thr
def f_h1(s, on=True):
    if not on: return True
    # 1h тренд против < 1.5% (приближаем ch4 = последние 4 часа)
    if s['dir']=='LONG'  and s['ch4']<-1.5: return False
    if s['dir']=='SHORT' and s['ch4']>1.5:  return False
    return True
def f_mom(s, on=True):
    if not on: return True
    if s['dir']=='LONG'  and s['ch24']>2 and s['ch2h']<-0.5: return False
    if s['dir']=='SHORT' and s['ch24']<-2 and s['ch2h']>0.5: return False
    return True
def f_fade(s, on=True):
    if not on: return True
    return not s.get('fade',False)

def run(setups, filters_on, label):
    trs=[]
    for s in setups:
        if not f_atr(s, filters_on['atr']): continue
        if not f_ch24(s, filters_on['ch24']): continue
        if not f_adx(s, filters_on['adx']): continue
        if not f_h1(s, filters_on['h1']): continue
        if not f_mom(s, filters_on['mom']): continue
        if not f_fade(s, filters_on['fade']): continue
        out=sim_chandelier(s['K'], s)
        if out: trs.append(out)
    return aggregate(trs, label)

# baseline = всё что в проде сейчас (без ADX)
BASELINE = {'atr':True,'ch24':True,'adx':False,'h1':True,'mom':True,'fade':True}

results={}
print("=== ABLATION: каждый фильтр выкл/вкл, остальное = прод. Сборка: chandelier act+2R 3xATR ===")
print(f"{'config':>26s} | {'n':>4s} | {'WR%':>5s} | {'avg_R':>7s} | {'sum_R':>8s} | {'DD':>7s}")
print("-"*72)

# A. baseline (mb=0.3)
r=run(all_setups['mb_0.3'], BASELINE, 'BASELINE_(prod)')
results['baseline']=r
print(f"  {'BASELINE (prod)':>24s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f}")

# B. выключаем по очереди каждый фильтр
for key, name in [('atr','no_ATR_filter'), ('ch24','no_ch24'), ('h1','no_h1_trend'),
                  ('mom','no_momentum_rev'), ('fade','no_anti_fade')]:
    cfg=dict(BASELINE); cfg[key]=False
    r=run(all_setups['mb_0.3'], cfg, name)
    results[name]=r
    delta=r['sum_R']-results['baseline']['sum_R']
    mark = "+" if delta>0 else ""
    print(f"  {name:>24s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f}  Δ={mark}{delta:+.1f}R")

# C. min_break = 0 (выкл SMC фильтр)
print("  --- SMC min_break=0 (вкл всех остальных) ---")
r=run(all_setups['mb_0.0'], BASELINE, 'no_min_break')
results['no_min_break']=r
delta=r['sum_R']-results['baseline']['sum_R']
print(f"  {'no_min_break (mb=0)':>24s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f}  Δ={delta:+.1f}R")

# D. ADX варианты
print("  --- ADX (включаем фильтр поверх прода) ---")
for thr in [15, 20, 25]:
    cfg=dict(BASELINE); cfg['adx']=True
    # запускаем вручную с порогом
    trs=[]
    for s in all_setups['mb_0.3']:
        if not f_atr(s,True): continue
        if not f_ch24(s,True): continue
        if s['adx']<thr: continue
        if not f_h1(s,True): continue
        if not f_mom(s,True): continue
        if not f_fade(s,True): continue
        out=sim_chandelier(s['K'], s)
        if out: trs.append(out)
    r=aggregate(trs, f'add_ADX>={thr}')
    results[f'add_adx_{thr}']=r
    delta=r['sum_R']-results['baseline']['sum_R']
    mark = "+" if delta>0 else ""
    print(f"  {'add ADX>=' + str(thr):>24s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f}  Δ={mark}{delta:+.1f}R")

# E. minimal-config: только то что показало plus в ablation (заполнится после прогона руками)
print("\n=== Идеальная сборка по ablation (если выкл вредных фильтров) ===")
# найдём какие выключения дают плюс
useful_off = []
for key in ['atr','ch24','h1','mom','fade']:
    name = {'atr':'no_ATR_filter','ch24':'no_ch24','h1':'no_h1_trend','mom':'no_momentum_rev','fade':'no_anti_fade'}[key]
    if results[name]['sum_R'] > results['baseline']['sum_R']:
        useful_off.append(key)
print(f"  Выключаем фильтры: {useful_off if useful_off else 'НИ ОДИН (все полезны)'}")
cfg = dict(BASELINE)
for k in useful_off: cfg[k]=False
r=run(all_setups['mb_0.3'], cfg, 'OPTIMAL')
results['optimal']=r
delta=r['sum_R']-results['baseline']['sum_R']
print(f"  {'OPTIMAL':>24s} | {r['n']:>4d} | {r['WR']:>5.1f} | {r['avg_R']:>+7.3f} | {r['sum_R']:>+8.1f} | {r['max_DD']:>7.1f}  Δ={delta:+.1f}R")

with open('/tmp/step4.json','w') as f:
    json.dump(results,f,indent=2,ensure_ascii=False)
print("\nрезультаты в /tmp/step4.json")
