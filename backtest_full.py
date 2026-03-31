#!/usr/bin/env python3
import numpy as np, pandas as pd, requests, sys
sys.path.insert(0, '/root/bingx-bot')
from smc_analyzer import SMCAnalyzer

BINGX_API = "https://open-api.bingx.com"
SYMBOLS = ["ETH-USDT","SUI-USDT","ADA-USDT","DOGE-USDT","XRP-USDT","EIGEN-USDT","AVAX-USDT","TIA-USDT","OP-USDT"]
POSITION_SIZE = 50.0
STOP_LOSS_PCT = 3.0
TRAILING_PCT = 1.0
BREAKEVEN_PCT = 0.5
smc = SMCAnalyzer()

def get_klines(symbol, interval="1h", limit=1000):
    r = requests.get(f"{BINGX_API}/openApi/swap/v3/quote/klines", params={"symbol":symbol,"interval":interval,"limit":limit}, timeout=15)
    d = r.json()
    return d["data"] if d.get("code")==0 else []

def calc_rsi(closes, period=14):
    if len(closes)<period+1: return 50
    d=np.diff(closes); g=np.mean(np.where(d>0,d,0)[-period:]); l=np.mean(np.where(d<0,-d,0)[-period:])
    return 50 if l==0 else 100-(100/(1+g/l))

def calc_ema(closes, period):
    return pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1]

def calc_macd(closes):
    s=pd.Series(closes); e12=s.ewm(span=12,adjust=False).mean(); e26=s.ewm(span=26,adjust=False).mean()
    h=(e12-e26)-(e12-e26).ewm(span=9,adjust=False).mean()
    return float(h.iloc[-1]), float(h.iloc[-2]) if len(h)>1 else 0

def calc_adx(klines_slice):
    if len(klines_slice)<15: return 20
    highs=[float(k["high"]) for k in klines_slice]; lows=[float(k["low"]) for k in klines_slice]; closes_l=[float(k["close"]) for k in klines_slice]
    tr=[max(highs[i]-lows[i],abs(highs[i]-closes_l[i-1]),abs(lows[i]-closes_l[i-1])) for i in range(1,len(highs))]
    if not tr: return 20
    atr=np.mean(tr[-14:])
    dmp=[max(highs[i]-highs[i-1],0) if highs[i]-highs[i-1]>lows[i-1]-lows[i] else 0 for i in range(1,len(highs))]
    dmm=[max(lows[i-1]-lows[i],0) if lows[i-1]-lows[i]>highs[i]-highs[i-1] else 0 for i in range(1,len(highs))]
    if atr==0: return 20
    dip=np.mean(dmp[-14:])/atr*100; dim=np.mean(dmm[-14:])/atr*100
    return abs(dip-dim)/(dip+dim)*100 if dip+dim>0 else 20

def get_4h_trend(klines_4h):
    if len(klines_4h)<20: return "NEUTRAL"
    closes=[float(k["close"]) for k in klines_4h]
    ema20=pd.Series(closes).ewm(span=20,adjust=False).mean().iloc[-1]
    if closes[-1]>ema20 and closes[-1]>closes[-5]: return "BULLISH"
    elif closes[-1]<ema20 and closes[-1]<closes[-5]: return "BEARISH"
    return "NEUTRAL"

def backtest(symbol, use_filters=True):
    klines_1h=get_klines(symbol,"1h",1000); klines_4h=get_klines(symbol,"4h",250)
    if len(klines_1h)<100: return None
    trades=[]; position=None
    for i in range(50,len(klines_1h)):
        window=klines_1h[max(0,i-50):i+1]
        closes=[float(k["close"]) for k in klines_1h[:i+1]]
        price=float(klines_1h[i]["close"])
        ts=klines_1h[i].get("time",klines_1h[i].get("timestamp",0))
        try:
            hour_utc=int((ts/1000)%86400/3600) if isinstance(ts,(int,float)) and ts>1e12 else 12
        except: hour_utc=12
        if position:
            pnl_pct=((price-position["entry"])/position["entry"]*100) if position["side"]=="BUY" else ((position["entry"]-price)/position["entry"]*100)
            if position["side"]=="BUY": position["high"]=max(position["high"],price)
            else: position["low"]=min(position["low"],price)
            if not position.get("breakeven") and pnl_pct>=BREAKEVEN_PCT: position["breakeven"]=True
            if use_filters and len(window)>=30:
                smc_exit=smc.analyze(window[-30:]); exit_sig=smc_exit.get("signal","NEUTRAL"); exit_score=smc_exit.get("score",0); exit_det=smc_exit.get("details",{})
                if position["side"]=="BUY" and exit_sig=="BEARISH" and exit_score>=5 and exit_det.get("bear_confluence",0)>=3:
                    trades.append({"pnl":pnl_pct,"type":"SMC_EXIT"}); position=None; continue
                elif position["side"]=="SELL" and exit_sig=="BULLISH" and exit_score>=5 and exit_det.get("bull_confluence",0)>=3:
                    trades.append({"pnl":pnl_pct,"type":"SMC_EXIT"}); position=None; continue
            if position.get("breakeven") and pnl_pct<=0:
                trades.append({"pnl":0.0,"type":"BE"}); position=None; continue
            if position["side"]=="BUY":
                fh=(position["high"]-price)/position["high"]*100
                if pnl_pct>TRAILING_PCT and fh>TRAILING_PCT: trades.append({"pnl":pnl_pct,"type":"TRAIL"}); position=None; continue
            else:
                fl=(price-position["low"])/position["low"]*100
                if pnl_pct>TRAILING_PCT and fl>TRAILING_PCT: trades.append({"pnl":pnl_pct,"type":"TRAIL"}); position=None; continue
            if use_filters:
                adx=calc_adx(window[-15:]); atr_pct=np.mean([float(k["high"])-float(k["low"]) for k in window[-6:]])/price*100
                sl=STOP_LOSS_PCT*0.7 if atr_pct>3.0 else (STOP_LOSS_PCT*1.3 if adx>=25 else STOP_LOSS_PCT)
            else: sl=STOP_LOSS_PCT
            if pnl_pct<=-sl: trades.append({"pnl":pnl_pct,"type":"SL"}); position=None; continue
            continue
        if len(closes)<50: continue
        rsi=calc_rsi(closes[-50:]); ema_fast=calc_ema(closes[-50:],9); ema_slow=calc_ema(closes[-50:],21)
        macd_hist,macd_prev=calc_macd(closes[-50:])
        buy_signals=0; sell_signals=0
        smc_result=smc.analyze(window[-30:]); smc_sig=smc_result.get("signal","NEUTRAL"); smc_score=smc_result.get("score",0)
        smc_det=smc_result.get("details",{}); bull_conf=smc_det.get("bull_confluence",0); bear_conf=smc_det.get("bear_confluence",0)
        if smc_sig=="BULLISH": buy_signals+=min(smc_score,3)
        elif smc_sig=="BEARISH": sell_signals+=min(smc_score,3)
        if rsi<30: buy_signals+=1
        elif rsi>70: sell_signals+=1
        if price>ema_fast>ema_slow: buy_signals+=1
        elif price<ema_fast<ema_slow: sell_signals+=1
        if macd_hist>0 and macd_prev<=0: buy_signals+=1
        elif macd_hist<0 and macd_prev>=0: sell_signals+=1
        i_4h=min(int(i/4),len(klines_4h)-1)
        trend_4h=get_4h_trend(klines_4h[max(0,i_4h-20):i_4h+1]) if i_4h>=20 else "NEUTRAL"
        if trend_4h=="BULLISH": buy_signals+=1
        elif trend_4h=="BEARISH": sell_signals+=1
        if use_filters:
            pass  # session filter disabled for backtest
            pass  # no entry filters, only session
        if buy_signals>=3 and buy_signals>sell_signals:
            position={"side":"BUY","entry":price,"qty":POSITION_SIZE/price,"high":price,"low":price,"breakeven":False}
        elif sell_signals>=3 and sell_signals>buy_signals:
            position={"side":"SELL","entry":price,"qty":POSITION_SIZE/price,"high":price,"low":price,"breakeven":False}
    if not trades: return None
    profits=[t["pnl"] for t in trades]; wins=[p for p in profits if p>0]
    return {"trades":len(trades),"win_rate":round(len(wins)/len(trades)*100,1),"total_pnl":round(sum(profits),2),
        "avg_pnl":round(np.mean(profits),2),"best":round(max(profits),2),"worst":round(min(profits),2),
        "sl":len([t for t in trades if t["type"]=="SL"]),"trail":len([t for t in trades if t["type"]=="TRAIL"]),
        "be":len([t for t in trades if t["type"]=="BE"]),"smc_exit":len([t for t in trades if t["type"]=="SMC_EXIT"])}

print("="*75)
print("  BACKTEST: WITHOUT FILTERS vs ALL FILTERS")
print("="*75)
tn,ty,ttn,tty,twn,twy=0,0,0,0,0,0
for symbol in SYMBOLS:
    print(f"\n[{symbol}]",flush=True)
    r1=backtest(symbol,use_filters=False); r2=backtest(symbol,use_filters=True)
    if not r1 or not r2: print("  no data"); continue
    for label,v1,v2,fmt in [("Trades",r1["trades"],r2["trades"],"d"),("Win Rate %",r1["win_rate"],r2["win_rate"],".1f"),
        ("Total PnL %",r1["total_pnl"],r2["total_pnl"],".2f"),("Avg PnL %",r1["avg_pnl"],r2["avg_pnl"],".2f")]:
        diff=v2-v1
        print(f"  {label:<22} {v1:>12{fmt}} {v2:>12{fmt}} {diff:>+10{fmt}}")
    print(f"  {'SL/Trail/BE/SMCexit':<22} {r1['sl']}/{r1['trail']}/{r1['be']}/{r1['smc_exit']:>5} {r2['sl']}/{r2['trail']}/{r2['be']}/{r2['smc_exit']:>5}")
    tn+=r1["total_pnl"]; ty+=r2["total_pnl"]; ttn+=r1["trades"]; tty+=r2["trades"]
    twn+=int(r1["trades"]*r1["win_rate"]/100); twy+=int(r2["trades"]*r2["win_rate"]/100)
wrn=twn/ttn*100 if ttn>0 else 0; wry=twy/tty*100 if tty>0 else 0
print("\n"+"="*75)
print(f"  TOTAL ({len(SYMBOLS)} pairs):")
print(f"  No filters  : {ttn} trades | WR {wrn:.1f}% | PnL {tn:+.2f}%")
print(f"  All filters : {tty} trades | WR {wry:.1f}% | PnL {ty:+.2f}%")
print(f"  Delta WR    : {wry-wrn:+.1f}%")
print(f"  Delta PnL   : {ty-tn:+.2f}%")
print("="*75)
