#!/usr/bin/env python3
import sys, time, hmac, hashlib, requests, urllib.parse, os
from statistics import mean

K=os.environ['BINGX_API_KEY']; S=os.environ['BINGX_SECRET_KEY']
BASE='https://open-api.bingx.com'

def sign(p):
    q=urllib.parse.urlencode(sorted(p.items()))
    return q+'&signature='+hmac.new(S.encode(),q.encode(),hashlib.sha256).hexdigest()

def get(path, p=None, auth=False):
    p = p or {}
    if auth:
        p['timestamp']=int(time.time()*1000)
        url=BASE+path+'?'+sign(p)
        h={'X-BX-APIKEY':K}
    else:
        url=BASE+path+('?'+urllib.parse.urlencode(p) if p else '')
        h={}
    try:
        return requests.get(url, headers=h, timeout=10).json()
    except Exception as e:
        return {'err':str(e)}

def ema(vals, period):
    if len(vals)<period: return vals[-1]
    k=2/(period+1); e=mean(vals[:period])
    for v in vals[period:]: e=v*k+e*(1-k)
    return e

def rsi(closes, period=14):
    if len(closes)<period+1: return 50
    gains=[]; losses=[]
    for i in range(1,len(closes)):
        d=closes[i]-closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    avg_g=mean(gains[-period:]); avg_l=mean(losses[-period:])
    if avg_l==0: return 100
    rs=avg_g/avg_l
    return 100-(100/(1+rs))

def klines(sym, interval, limit=50):
    r=get('/openApi/swap/v3/quote/klines',{'symbol':sym,'interval':interval,'limit':limit})
    if r.get('code')!=0: return []
    data=r['data']
    if data and float(data[0]['time'])>float(data[-1]['time']):
        data=data[::-1]
    return [{'o':float(x['open']),'c':float(x['close']),'h':float(x['high']),'l':float(x['low']),'v':float(x['volume'])} for x in data]

def analyze(sym):
    print(f"\n{'='*60}\n  {sym}\n{'='*60}")

    # 1) Цена + 24h
    t=get('/openApi/swap/v2/quote/ticker',{'symbol':sym})
    if t.get('code')!=0:
        print(f"❌ {t}"); return
    d=t['data']
    px=float(d['lastPrice']); ch=float(d['priceChangePercent']); hi=float(d['highPrice']); lo=float(d['lowPrice']); vol=float(d['quoteVolume'])
    from_hi=(px-hi)/hi*100; from_lo=(px-lo)/lo*100
    print(f"💰 ${px:.5f} | 24h {ch:+.1f}% | vol ${vol/1e6:.1f}M")
    print(f"   От хая ${hi:.5f}: {from_hi:+.1f}% | От лоу ${lo:.5f}: {from_lo:+.1f}%")

    # 2) BTC контекст
    btc=get('/openApi/swap/v2/quote/ticker',{'symbol':'BTC-USDT'})
    if btc.get('code')==0:
        bch=float(btc['data']['priceChangePercent'])
        bias = "🟢 BTC растёт — лонги поддержаны" if bch>0.5 else ("🔴 BTC падает — шорты в фаворе" if bch<-0.5 else "⚪ BTC флэт")
        print(f"📊 BTC 24h: {bch:+.1f}% | {bias}")

    # 3) Klines + EMA + RSI
    k1h=klines(sym,'1h',50); k15=klines(sym,'15m',30); k5=klines(sym,'5m',12)
    if k1h:
        c1h=[x['c'] for x in k1h]
        e20=ema(c1h,20); e50=ema(c1h,min(50,len(c1h)))
        r1h=rsi(c1h,14)
        print(f"📈 EMA20 1ч: ${e20:.5f} ({(px/e20-1)*100:+.1f}%) | EMA50: ${e50:.5f} ({(px/e50-1)*100:+.1f}%)")
        rsi_tag = "🔥 перекуплен (шорт)" if r1h>70 else ("❄️ перепродан (лонг)" if r1h<30 else "⚪ нейтрал")
        print(f"📉 RSI 1ч: {r1h:.0f} | {rsi_tag}")
    if k15:
        c15=[x['c'] for x in k15]
        r15=rsi(c15,14)
        print(f"   RSI 15м: {r15:.0f}")
    if k5:
        c5=[x['c'] for x in k5][-6:]
        v5=[x['v'] for x in k5][-6:]
        avg_v=mean([x['v'] for x in k5])
        last_v=v5[-1]
        vol_tag = "📊 объём растёт (подтверждение)" if last_v>avg_v*1.3 else "📉 объём падает"
        print(f"   5м closes: {[f'{x:.5f}' for x in c5]}")
        print(f"   {vol_tag}")

    # 4) Funding
    fund=get('/openApi/swap/v2/quote/premiumIndex',{'symbol':sym})
    if fund.get('code')==0 and fund.get('data'):
        fd=fund['data'] if isinstance(fund['data'],dict) else fund['data'][0]
        fr=float(fd['lastFundingRate'])*100
        interval=fd.get('fundingIntervalHours',8)
        daily=fr*(24/interval)
        sign_tag = "🟢 платят шортам (толпа в лонгах)" if fr<0 else ("🔴 платят лонгам (толпа в шортах)" if fr>0.05 else "⚪ нейтрал")
        print(f"💵 Funding: {fr:+.4f}%/{interval}ч = {daily:+.3f}%/день | {sign_tag}")

    # 5) Long/Short ratio (top traders)
    ls=get('/openApi/swap/v1/quote/longShortRatio',{'symbol':sym,'interval':'1h','limit':1})
    if isinstance(ls.get('data'),list) and ls['data']:
        d=ls['data'][0]
        lp=float(d.get('longShortRatio',0))
        ls_tag = "🔴 лонгов МНОГО (риск каскадных ликвидаций вниз)" if lp>2 else ("🟢 шортов много (squeeze риск вверх)" if lp<0.5 else "⚪ баланс")
        print(f"⚖️  L/S ratio: {lp:.2f} | {ls_tag}")

    # 6) Orderbook depth
    ob=get('/openApi/swap/v2/quote/depth',{'symbol':sym,'limit':20})
    if ob.get('code')==0:
        bids=sum(float(b[1]) for b in ob['data']['bids'][:10])
        asks=sum(float(a[1]) for a in ob['data']['asks'][:10])
        ratio=bids/asks if asks else 0
        ob_tag = "🟢 покупатели сильнее" if ratio>1.3 else ("🔴 продавцы сильнее" if ratio<0.77 else "⚪ баланс")
        print(f"📚 Orderbook bid/ask: {ratio:.2f} | {ob_tag}")

    # 7) Open Interest тренд
    oi=get('/openApi/swap/v2/quote/openInterest',{'symbol':sym})
    if oi.get('code')==0:
        oi_val=float(oi['data']['openInterest'])
        print(f"💼 Open Interest: {oi_val/1e6:.2f}M контрактов")

    # 8) Вердикт
    print(f"\n🎯 ИТОГ:")
    score_short=0; score_long=0
    if ch>20 and from_hi<-10: score_short+=2
    if k1h and r1h>70: score_short+=2
    if k1h and r1h<30: score_long+=2
    if k1h and px<e20: score_short+=1
    if k1h and px>e20: score_long+=1
    if 'fr' in dir() and fr<-0.01: score_short+=1
    if 'lp' in dir() and lp>2: score_short+=2
    if 'ratio' in dir() and ratio<0.77: score_short+=1
    if 'ratio' in dir() and ratio>1.3: score_long+=1
    if 'bch' in dir() and bch<-0.5: score_short+=1
    if 'bch' in dir() and bch>0.5: score_long+=1
    print(f"   SHORT score: {score_short} | LONG score: {score_long}")
    if score_short>=5: print("   ✅ СИЛЬНЫЙ SHORT сетап")
    elif score_long>=5: print("   ✅ СИЛЬНЫЙ LONG сетап")
    elif abs(score_short-score_long)<=1: print("   ⚠️ нет чёткого сигнала — ПРОПУСК")
    else: print(f"   📍 слабый сигнал ({'SHORT' if score_short>score_long else 'LONG'})")

if __name__=='__main__':
    syms=sys.argv[1:] if len(sys.argv)>1 else ['BTC-USDT']
    for s in syms: analyze(s)
