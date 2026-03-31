import numpy as np
import pandas as pd
import requests
import sys
sys.path.insert(0, '/root/bingx-bot')
from smc_analyzer import SMCAnalyzer

SYMBOLS = ["ETH-USDT", "SOL-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "XRP-USDT", "BNB-USDT", "SUI-USDT"]
BINGX_API = "https://open-api.bingx.com"
POSITION_SIZE = 10.0
MIN_VOLUME_RATIO = 1.2
STOP_LOSS_PCT = 3.0
HARD_STOP_PCT = 30.0
TRAILING_PCT = 1.5
AVG_DROP_PCT = 2.0
AVG_MAX_TIMES = 2
AVG_SIZE_MULT = 1.5

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

def detect_rsi_divergence(closes, period=14):
    if len(closes) < 30:
        return "NONE"
    rsi_vals = []
    for j in range(len(closes)-20, len(closes)):
        c = closes[:j+1]
        if len(c) < period+1:
            rsi_vals.append(50)
            continue
        d = np.diff(c)
        g = np.mean(np.where(d>0,d,0)[-period:])
        l = np.mean(np.where(d<0,-d,0)[-period:])
        rsi_vals.append(50 if l==0 else 100-(100/(1+g/l)))
    prices = closes[-20:]
    if prices[-1] < prices[-10] and rsi_vals[-1] > rsi_vals[-10]:
        return "BULLISH"
    if prices[-1] > prices[-10] and rsi_vals[-1] < rsi_vals[-10]:
        return "BEARISH"
    return "NONE"

def should_average(closes, volumes, pos_side):
    rsi = calc_rsi(closes)
    rsi_ok = (rsi < 40 and pos_side == "LONG") or (rsi > 60 and pos_side == "SHORT")
    div = detect_rsi_divergence(closes)
    div_ok = (div == "BULLISH" and pos_side == "LONG") or (div == "BEARISH" and pos_side == "SHORT")
    vol_ok = volumes[-1] > sum(volumes[-5:-1]) / 4
    return (rsi_ok or div_ok), rsi

def backtest(symbol, use_averaging=False):
    klines = get_klines(symbol)
    if len(klines) < 100:
        return None

    smc = SMCAnalyzer()
    trades = []
    position = None
    max_price = None
    trailing_stop = None
    avg_count = 0
    total_averagings = 0

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
                    trades.append({"pnl": pnl_pct, "type": "TRAIL", "avgs": avg_count})
                    position = None; max_price = None; trailing_stop = None; avg_count = 0; continue
            else:
                if max_price is None or current_price < max_price:
                    max_price = current_price
                if ((entry - max_price) / entry * 100) >= TRAILING_PCT:
                    new_trail = max_price * (1 + TRAILING_PCT / 100)
                    if trailing_stop is None or new_trail < trailing_stop:
                        trailing_stop = new_trail
                if trailing_stop and current_price >= trailing_stop:
                    trades.append({"pnl": pnl_pct, "type": "TRAIL", "avgs": avg_count})
                    position = None; max_price = None; trailing_stop = None; avg_count = 0; continue

            if pnl_pct <= -STOP_LOSS_PCT:
                trades.append({"pnl": pnl_pct, "type": "SL", "avgs": avg_count})
                position = None; max_price = None; trailing_stop = None; avg_count = 0; continue

            # Умное усреднение
            if use_averaging and avg_count < AVG_MAX_TIMES:
                entry = position["entry"]
                side = position["side"]
                sl_price = entry * (1 - STOP_LOSS_PCT/100) if side == "LONG" else entry * (1 + STOP_LOSS_PCT/100)
                drop_pct = ((entry - current_price)/entry*100) if side == "LONG" else ((current_price - entry)/entry*100)
                sl_dist = abs(current_price - sl_price) / current_price * 100
                if drop_pct >= AVG_DROP_PCT and sl_dist >= 1.0:
                    confirmed, rsi_v = should_average(closes, volumes, side)
                    if confirmed:
                        add_size = POSITION_SIZE * AVG_SIZE_MULT
                        total_size = POSITION_SIZE + avg_count * POSITION_SIZE * AVG_SIZE_MULT
                        new_entry = (entry * total_size + current_price * add_size) / (total_size + add_size)
                        position["entry"] = new_entry
                        avg_count += 1
                        total_averagings += 1
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
        "trail": len([t for t in trades if t["type"] == "TRAIL"]),
        "averagings": total_averagings
    }

print("=" * 70)
print("  БЭКТЕСТ SMC: БЕЗ УСРЕДНЕНИЯ vs С УМНЫМ УСРЕДНЕНИЕМ")
print("=" * 70)

total_pnl_no = 0
total_pnl_yes = 0

for symbol in SYMBOLS:
    print(f"\n[{symbol}]", flush=True)
    r1 = backtest(symbol, use_averaging=False)
    r2 = backtest(symbol, use_averaging=True)
    if not r1 or not r2:
        print("  нет данных")
        continue
    print(f"  {'Метрика':<22} {'Без усреднения':>15} {'С усреднением':>15} {'Разница':>10}")
    print(f"  {'-'*62}")
    print(f"  {'Сделок':<22} {r1['trades']:>15} {r2['trades']:>15}")
    print(f"  {'Win Rate %':<22} {r1['win_rate']:>15} {r2['win_rate']:>15} {r2['win_rate']-r1['win_rate']:>+10.1f}")
    print(f"  {'Total PnL %':<22} {r1['total_pnl']:>15} {r2['total_pnl']:>15} {r2['total_pnl']-r1['total_pnl']:>+10.2f}")
    print(f"  {'Avg PnL %':<22} {r1['avg_pnl']:>15} {r2['avg_pnl']:>15} {r2['avg_pnl']-r1['avg_pnl']:>+10.2f}")
    print(f"  {'Best %':<22} {r1['best']:>15} {r2['best']:>15}")
    print(f"  {'Worst %':<22} {r1['worst']:>15} {r2['worst']:>15}")
    print(f"  {'SL hits':<22} {r1['sl']:>15} {r2['sl']:>15}")
    print(f"  {'Усреднений':<22} {'—':>15} {r2['averagings']:>15}")
    total_pnl_no += r1['total_pnl']
    total_pnl_yes += r2['total_pnl']

print("\n" + "=" * 70)
print(f"  ИТОГО:")
print(f"  Без усреднения : Total PnL {total_pnl_no:+.2f}%")
print(f"  С усреднением  : Total PnL {total_pnl_yes:+.2f}%")
print(f"  Разница        : {total_pnl_yes - total_pnl_no:+.2f}%")
print("=" * 70)
