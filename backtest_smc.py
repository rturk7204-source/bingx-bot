import numpy as np
import pandas as pd
import requests
import sys
sys.path.insert(0, '/root/bingx-bot')
from smc_analyzer import SMCAnalyzer

SYMBOLS = ["BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "LINK-USDT", "SUI-USDT"]
BINGX_API = "https://open-api.bingx.com"
POSITION_SIZE = 10.0
STOP_LOSS_PCT = 3.0
TRAILING_PCT = 1.5

def get_klines(symbol, limit=1000):
    url = f"{BINGX_API}/openApi/swap/v3/quote/klines"
    r = requests.get(url, params={"symbol": symbol, "interval": "1h", "limit": limit}, timeout=15)
    data = r.json()
    if data.get("code") == 0:
        return data["data"]
    return []

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calc_ema(closes, period):
    return pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1]

def calc_macd(closes):
    ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean()
    hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    return float(hist.iloc[-1]), float(hist.iloc[-2])

def backtest(symbol):
    klines = get_klines(symbol)
    if len(klines) < 100:
        return None

    smc = SMCAnalyzer()
    trades = []
    position = None
    max_price = None
    trailing_stop = None

    for i in range(60, len(klines)):
        window = klines[max(0, i-200):i]
        closes = [float(k["close"]) for k in window]
        volumes = [float(k["volume"]) for k in window]
        current_price = float(klines[i]["close"])

        if len(closes) < 50:
            continue

        # Фильтр объёма
        if volumes[-1] < np.mean(volumes[-20:]) * 0.5:
            continue

        rsi = calc_rsi(closes)
        ema_fast = calc_ema(closes, 20)
        ema_slow = calc_ema(closes, 50)
        macd_hist, macd_prev = calc_macd(closes)

        # Проверка SL/Trailing
        if position:
            entry = position["entry"]
            side = position["side"]
            pnl_pct = ((current_price - entry) / entry * 100) if side == "LONG" else ((entry - current_price) / entry * 100)

            if side == "LONG":
                if max_price is None or current_price > max_price:
                    max_price = current_price
                if ((max_price - entry) / entry * 100) >= TRAILING_PCT:
                    new_trail = max_price * (1 - TRAILING_PCT / 100)
                    if trailing_stop is None or new_trail > trailing_stop:
                        trailing_stop = new_trail
                if trailing_stop and current_price <= trailing_stop:
                    trades.append({"pnl": pnl_pct, "type": "TRAIL"})
                    position = None; max_price = None; trailing_stop = None; continue
            else:
                if max_price is None or current_price < max_price:
                    max_price = current_price
                if ((entry - max_price) / entry * 100) >= TRAILING_PCT:
                    new_trail = max_price * (1 + TRAILING_PCT / 100)
                    if trailing_stop is None or new_trail < trailing_stop:
                        trailing_stop = new_trail
                if trailing_stop and current_price >= trailing_stop:
                    trades.append({"pnl": pnl_pct, "type": "TRAIL"})
                    position = None; max_price = None; trailing_stop = None; continue

            if pnl_pct <= -STOP_LOSS_PCT:
                trades.append({"pnl": pnl_pct, "type": "SL"})
                position = None; max_price = None; trailing_stop = None; continue
            continue

        # Сигналы RSI + EMA + MACD
        buy_signals = sell_signals = 0
        if rsi < 35: buy_signals += 1
        elif rsi > 65: sell_signals += 1

        # RSI Divergence
        if i >= 30:
            div_closes = closes[-30:]
            rsi_vals = []
            for j in range(len(div_closes)):
                w = div_closes[max(0,j-14):j+1]
                if len(w) < 5:
                    rsi_vals.append(50)
                    continue
                d = np.diff(w)
                g = np.mean(np.where(d>0,d,0)[-14:])
                l = np.mean(np.where(d<0,-d,0)[-14:])
                rsi_vals.append(100-(100/(1+g/(l+1e-9))))
            p_lows = [(j,div_closes[j]) for j in range(2,len(div_closes)-2) if div_closes[j]<div_closes[j-1] and div_closes[j]<div_closes[j+1]]
            r_lows = [(j,rsi_vals[j]) for j in range(2,len(rsi_vals)-2) if rsi_vals[j]<rsi_vals[j-1] and rsi_vals[j]<rsi_vals[j+1]]
            p_highs = [(j,div_closes[j]) for j in range(2,len(div_closes)-2) if div_closes[j]>div_closes[j-1] and div_closes[j]>div_closes[j+1]]
            r_highs = [(j,rsi_vals[j]) for j in range(2,len(rsi_vals)-2) if rsi_vals[j]>rsi_vals[j-1] and rsi_vals[j]>rsi_vals[j+1]]
            if len(p_lows)>=2 and len(r_lows)>=2:
                if p_lows[-1][1]<p_lows[-2][1] and r_lows[-1][1]>r_lows[-2][1] and r_lows[-1][1]<45:
                    buy_signals += 2
            if len(p_highs)>=2 and len(r_highs)>=2:
                if p_highs[-1][1]>p_highs[-2][1] and r_highs[-1][1]<r_highs[-2][1] and r_highs[-1][1]>55:
                    sell_signals += 2
        if current_price > ema_fast > ema_slow: buy_signals += 1
        elif current_price < ema_fast < ema_slow: sell_signals += 1
        if macd_hist > 0 and macd_prev <= 0: buy_signals += 1
        elif macd_hist < 0 and macd_prev >= 0: sell_signals += 1

        # SMC сигналы
        smc_result = smc.analyze(window[-50:])
        if smc_result["signal"] == "BULLISH":
            buy_signals += min(smc_result["score"], 3)
        elif smc_result["signal"] == "BEARISH":
            sell_signals += min(smc_result["score"], 3)

        if buy_signals >= 3 and buy_signals > sell_signals:
            position = {"entry": current_price, "side": "LONG"}
            max_price = current_price; trailing_stop = None
        elif sell_signals >= 3 and sell_signals > buy_signals:
            position = {"entry": current_price, "side": "SHORT"}
            max_price = current_price; trailing_stop = None

    if not trades:
        return None

    profits = [t["pnl"] for t in trades]
    wins = [p for p in profits if p > 0]
    return {
        "symbol": symbol,
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "total_pnl": round(sum(profits), 2),
        "avg_pnl": round(np.mean(profits), 2),
        "best": round(max(profits), 2),
        "worst": round(min(profits), 2),
        "sl": len([t for t in trades if t["type"] == "SL"]),
        "trail": len([t for t in trades if t["type"] == "TRAIL"])
    }

print("=" * 65)
print("  БЭКТЕСТИНГ С SMC — 1000 свечей 1h (~42 дня)")
print("=" * 65)

results = []
for symbol in SYMBOLS:
    print(f"[{symbol}] тестирую...", end=" ", flush=True)
    r = backtest(symbol)
    if r:
        results.append(r)
        print(f"сделок: {r['trades']} | WR: {r['win_rate']}% | PnL: {r['total_pnl']:+.2f}% | SL:{r['sl']} Trail:{r['trail']}")
    else:
        print("нет сделок")

print("\n" + "=" * 65)
if results:
    df = pd.DataFrame(results)
    print(df[["symbol","trades","win_rate","total_pnl","best","worst"]].to_string(index=False))
    print(f"\n  Средний Win Rate: {df['win_rate'].mean():.1f}%")
    print(f"  Суммарный PnL: {df['total_pnl'].sum():+.2f}%")
    profitable = len(df[df['total_pnl'] > 0])
    print(f"  Прибыльных пар: {profitable}/{len(df)}")
print("=" * 65)
