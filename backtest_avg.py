import numpy as np
import pandas as pd
import requests

SYMBOLS = ["BTC-USDT", "ETH-USDT", "BNB-USDT", "SOL-USDT", "SUI-USDT", "XRP-USDT"]
BINGX_API = "https://open-api.bingx.com"
POSITION_SIZE = 10.0
STOP_LOSS_PCT = 3.0
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
        rsi_vals.append(calc_rsi(closes[:j+1], period))
    prices = closes[-20:]
    if prices[-1] < prices[-10] and rsi_vals[-1] > rsi_vals[-10]:
        return "BULLISH"
    if prices[-1] > prices[-10] and rsi_vals[-1] < rsi_vals[-10]:
        return "BEARISH"
    return "NONE"

def get_signal(closes, volumes):
    rsi = calc_rsi(closes)
    ema_fast = calc_ema(closes, 20)
    ema_slow = calc_ema(closes, 50)
    macd_hist, macd_prev = calc_macd(closes)
    price = closes[-1]
    buy = sell = 0
    if rsi < 40: buy += 1
    elif rsi > 60: sell += 1
    if price > ema_fast > ema_slow: buy += 1
    elif price < ema_fast < ema_slow: sell += 1
    if macd_hist > 0 and macd_prev <= 0: buy += 1
    elif macd_hist < 0 and macd_prev >= 0: sell += 1
    avg_vol = sum(volumes[-20:]) / 20
    if volumes[-1] > avg_vol * 1.2: 
        if buy > sell: buy += 1
        elif sell > buy: sell += 1
    if buy >= 3 and buy > sell: return "BUY"
    if sell >= 3 and sell > buy: return "SELL"
    return "HOLD"

def should_average(closes, volumes, pos_side):
    rsi = calc_rsi(closes)
    rsi_ok = (rsi < 40 and pos_side == "LONG") or (rsi > 60 and pos_side == "SHORT")
    div = detect_rsi_divergence(closes)
    div_ok = (div == "BULLISH" and pos_side == "LONG") or (div == "BEARISH" and pos_side == "SHORT")
    vol_ok = volumes[-1] > sum(volumes[-5:-1]) / 4
    return (rsi_ok or div_ok), rsi, div

def run_backtest(symbol, use_averaging=False):
    klines = get_klines(symbol)
    if len(klines) < 100:
        return None

    trades = []
    position = None
    max_price = None
    trailing_stop = None
    avg_count = 0
    avg_entries = []

    for i in range(60, len(klines)):
        window = klines[max(0, i-200):i]
        closes = [float(k["close"]) for k in window]
        volumes = [float(k["volume"]) for k in window]
        current_price = float(klines[i]["close"])
        if len(closes) < 50:
            continue

        # Управление позицией
        if position:
            entry = position["entry"]
            side = position["side"]
            total_size = position["total_size"]
            pnl_pct = ((current_price - entry) / entry * 100) if side == "LONG" else ((entry - current_price) / entry * 100)
            sl_price = entry * (1 - STOP_LOSS_PCT/100) if side == "LONG" else entry * (1 + STOP_LOSS_PCT/100)

            # Умное усреднение
            if use_averaging and avg_count < AVG_MAX_TIMES:
                drop_pct = ((entry - current_price) / entry * 100) if side == "LONG" else ((current_price - entry) / entry * 100)
                sl_dist = abs(current_price - sl_price) / current_price * 100
                if drop_pct >= AVG_DROP_PCT and sl_dist >= 1.0:
                    confirmed, rsi_v, div_v = should_average(closes, volumes, side)
                    if confirmed:
                        add_size = POSITION_SIZE * AVG_SIZE_MULT
                        new_entry = (entry * total_size + current_price * add_size) / (total_size + add_size)
                        position["entry"] = new_entry
                        position["total_size"] = total_size + add_size
                        entry = new_entry
                        avg_count += 1
                        avg_entries.append({"price": current_price, "rsi": rsi_v, "div": div_v})

            # Trailing stop
            if side == "LONG":
                if max_price is None or current_price > max_price:
                    max_price = current_price
                if ((max_price - entry) / entry * 100) >= TRAILING_PCT:
                    new_trail = max_price * (1 - TRAILING_PCT / 100)
                    if trailing_stop is None or new_trail > trailing_stop:
                        trailing_stop = new_trail
                if trailing_stop and current_price <= trailing_stop:
                    trades.append({"pnl": pnl_pct, "type": "TRAIL", "size": total_size, "avgs": avg_count})
                    position = None; max_price = None; trailing_stop = None; avg_count = 0; continue

            # Stop-loss
            if pnl_pct <= -STOP_LOSS_PCT:
                trades.append({"pnl": pnl_pct, "type": "SL", "size": total_size, "avgs": avg_count})
                position = None; max_price = None; trailing_stop = None; avg_count = 0; continue

        else:
            sig = get_signal(closes, volumes)
            if sig == "BUY":
                position = {"entry": current_price, "side": "LONG", "total_size": POSITION_SIZE}
                max_price = current_price
                trailing_stop = None
                avg_count = 0
            elif sig == "SELL":
                position = {"entry": current_price, "side": "SHORT", "total_size": POSITION_SIZE}
                max_price = current_price
                trailing_stop = None
                avg_count = 0

    if not trades:
        return None

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    total_avgs = sum(t["avgs"] for t in trades)

    return {
        "symbol": symbol,
        "trades": len(trades),
        "win_rate": round(len(wins)/len(trades)*100, 1),
        "avg_pnl": round(np.mean(pnls), 2),
        "total_pnl": round(sum(pnls), 2),
        "best": round(max(pnls), 2),
        "worst": round(min(pnls), 2),
        "avg_win": round(np.mean(wins), 2) if wins else 0,
        "avg_loss": round(np.mean(losses), 2) if losses else 0,
        "total_averagings": total_avgs,
    }

print("=" * 70)
print("БЭКТЕСТ: БЕЗ УСРЕДНЕНИЯ vs С УМНЫМ УСРЕДНЕНИЕМ")
print("=" * 70)

total_no_avg = {"trades": 0, "wins": 0, "pnl": 0}
total_with_avg = {"trades": 0, "wins": 0, "pnl": 0}

for symbol in SYMBOLS:
    print(f"\n[{symbol}] Скачиваю данные...")
    r1 = run_backtest(symbol, use_averaging=False)
    r2 = run_backtest(symbol, use_averaging=True)
    if not r1 or not r2:
        print(f"  Недостаточно данных")
        continue

    print(f"\n  {'Метрика':<25} {'Без усреднения':>15} {'С усреднением':>15} {'Разница':>10}")
    print(f"  {'-'*65}")
    print(f"  {'Сделок':<25} {r1['trades']:>15} {r2['trades']:>15}")
    print(f"  {'Win Rate %':<25} {r1['win_rate']:>15} {r2['win_rate']:>15} {r2['win_rate']-r1['win_rate']:>+10.1f}")
    print(f"  {'Avg PnL %':<25} {r1['avg_pnl']:>15} {r2['avg_pnl']:>15} {r2['avg_pnl']-r1['avg_pnl']:>+10.2f}")
    print(f"  {'Total PnL %':<25} {r1['total_pnl']:>15} {r2['total_pnl']:>15} {r2['total_pnl']-r1['total_pnl']:>+10.2f}")
    print(f"  {'Best trade %':<25} {r1['best']:>15} {r2['best']:>15}")
    print(f"  {'Worst trade %':<25} {r1['worst']:>15} {r2['worst']:>15}")
    print(f"  {'Avg win %':<25} {r1['avg_win']:>15} {r2['avg_win']:>15}")
    print(f"  {'Avg loss %':<25} {r1['avg_loss']:>15} {r2['avg_loss']:>15}")
    print(f"  {'Усреднений всего':<25} {'—':>15} {r2['total_averagings']:>15}")

    total_no_avg["trades"] += r1["trades"]
    total_no_avg["pnl"] += r1["total_pnl"]
    total_with_avg["trades"] += r2["trades"]
    total_with_avg["pnl"] += r2["total_pnl"]

print("\n" + "=" * 70)
print(f"  ИТОГО по всем парам:")
print(f"  {'Без усреднения:':<25} {total_no_avg['trades']} сделок | Total PnL: {total_no_avg['pnl']:+.2f}%")
print(f"  {'С усреднением:':<25} {total_with_avg['trades']} сделок | Total PnL: {total_with_avg['pnl']:+.2f}%")
print(f"  {'Разница PnL:':<25} {total_with_avg['pnl']-total_no_avg['pnl']:+.2f}%")
print("=" * 70)
