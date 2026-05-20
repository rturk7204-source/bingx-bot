"""HYPE-USDT full scan: цена, funding, OI, объёмы, RSI/EMA/ATR на 4h+1h+15m, последние свечи."""
import sys, time, statistics as st
sys.path.insert(0, "/root/bingx-bot")
from assistant.core import exchange as ex

SYM = "HYPE-USDT"

def ema(arr, n):
    k = 2/(n+1); e=arr[0]
    out=[e]
    for v in arr[1:]:
        e = v*k + e*(1-k); out.append(e)
    return out

def rsi(closes, n=14):
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        d = closes[i]-closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    if len(gains) < n: return None
    ag = sum(gains[:n])/n; al = sum(losses[:n])/n
    for i in range(n,len(gains)):
        ag = (ag*(n-1)+gains[i])/n; al = (al*(n-1)+losses[i])/n
    if al == 0: return 100.0
    rs = ag/al
    return 100 - 100/(1+rs)

def atr_pct(klines, n=14):
    trs=[]
    for i in range(1,len(klines)):
        h=klines[i][2]; l=klines[i][3]; pc=klines[i-1][4]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    if len(trs)<n: return None
    a = sum(trs[-n:])/n
    return a/klines[-1][4]*100

def get_klines(sym, interval, limit=200):
    r = ex.request("GET","/openApi/swap/v3/quote/klines",
                   {"symbol":sym,"interval":interval,"limit":limit}, auth=False)
    data = r.get("data") or []
    # [open,high,low,close,volume,time] varies; нормализуем
    out=[]
    for k in data:
        if isinstance(k, dict):
            out.append([float(k["open"]),float(k["high"]),float(k["low"]),float(k["close"]),float(k.get("volume",0)),int(k.get("time",0))])
        else:
            out.append([float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[5]),int(k[0])])
    out.sort(key=lambda x:x[5])
    # вернём в формате [time,o,h,l,c,v]
    return [[k[5],k[0],k[1],k[2],k[3],k[4]] for k in out]

def analyze_tf(sym, tf, label):
    k = get_klines(sym, tf, 200)
    if len(k)<60:
        print(f"  {label}: мало данных"); return
    closes=[x[4] for x in k]; vols=[x[5] for x in k]
    e20 = ema(closes,20)[-1]; e50 = ema(closes,50)[-1]; e200 = ema(closes,200)[-1] if len(closes)>=200 else None
    r = rsi(closes,14)
    a = atr_pct(k,14)
    last = closes[-1]
    v_avg20 = sum(vols[-21:-1])/20
    v_last = vols[-1]
    print(f"  {label}: close={last:.4f}  EMA20={e20:.4f}  EMA50={e50:.4f}  EMA200={'%.4f'%e200 if e200 else 'na'}")
    print(f"         RSI14={r:.1f}  ATR%={a:.2f}  vol_last/avg20={v_last/v_avg20:.2f}x")
    # последние 5 свечей
    print(f"         last 5 closes: {[round(c,4) for c in closes[-5:]]}")
    print(f"         last 5 highs:  {[round(x[2],4) for x in k[-5:]]}")
    print(f"         last 5 lows:   {[round(x[3],4) for x in k[-5:]]}")

print(f"=== {SYM} scan {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

# Текущая цена + 24h
t = ex.request("GET","/openApi/swap/v2/quote/ticker",{"symbol":SYM},auth=False).get("data") or {}
print(f"[ticker] last={t.get('lastPrice')}  24hChg={t.get('priceChangePercent')}%  "
      f"high={t.get('highPrice')}  low={t.get('lowPrice')}  volUSDT={t.get('quoteVolume')}\n")

# Funding
fr = ex.request("GET","/openApi/swap/v2/quote/premiumIndex",{"symbol":SYM},auth=False).get("data") or {}
print(f"[funding] last={fr.get('lastFundingRate')}  nextTime={fr.get('nextFundingTime')}\n")

# Open Interest
oi = ex.request("GET","/openApi/swap/v2/quote/openInterest",{"symbol":SYM},auth=False).get("data") or {}
print(f"[OI] {oi}\n")

# Long/short ratio
lsr = ex.request("GET","/openApi/swap/v2/quote/longShortRatio",
                 {"symbol":SYM,"interval":"4h","limit":3},auth=False).get("data") or {}
print(f"[LSR 4h] {lsr}\n")

# Multi-TF technicals
print("[technicals]")
analyze_tf(SYM,"4h","4h")
analyze_tf(SYM,"1h","1h")
analyze_tf(SYM,"15m","15m")

# Recent 4h candles полностью
print("\n[last 6 candles 4h: time/o/h/l/c/vol]")
k4 = get_klines(SYM,"4h",10)
for x in k4[-6:]:
    print(f"  {time.strftime('%m-%d %H:%M', time.gmtime(x[0]/1000))}  o={x[1]:.4f} h={x[2]:.4f} l={x[3]:.4f} c={x[4]:.4f} v={x[5]:.0f}")
