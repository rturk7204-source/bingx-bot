"""Скоринг кандидатов: критерии 1, 2, 5, 7, 11."""
import time
from statistics import mean
from ..core import exchange as ex
from .klines import fetch_klines
from .smc import analyze_smc


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = mean(gains[-period:])
    avg_l = mean(losses[-period:]) or 1e-9
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def ema(values, period):
    if not values:
        return 0
    k = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def macd_hist(closes):
    if len(closes) < 35:
        return 0.0
    macd_line = ema(closes, 12) - ema(closes, 26)
    sig = ema([ema(closes[:i+1], 12) - ema(closes[:i+1], 26) for i in range(len(closes))][-9:], 9)
    return macd_line - sig




def score_candidate(row):
    sym = row["symbol"]
    kl = fetch_klines(sym, "15m", 100)
    if len(kl) < 50:
        return None
    closes = [k["c"] for k in kl]
    vols = [k["v"] for k in kl]
    last = kl[-1]
    score = 0
    detail = {}

    ch24 = row["change_24h"]
    last5 = closes[-5:]
    reversal = (ch24 > 15 and last5[-1] < mean(last5)) or (ch24 < -15 and last5[-1] > mean(last5))
    if abs(ch24) > 20:
        return None  # пост-импульс
    if abs(ch24) > 15 and reversal:
        score += 25
        detail["exhaustion"] = f"{ch24:+.1f}% + разворот"

    r = rsi(closes)
    detail["rsi"] = round(r, 1)
    if r > 75 or r < 25:
        score += 15
    elif r > 70 or r < 30:
        score += 8

    avg_v = mean(vols[-20:-1])
    vol_ratio = 0
    if avg_v > 0:
        vol_ratio = last["v"] / avg_v
        detail["vol_ratio"] = round(vol_ratio, 2)
        if vol_ratio > 3:
            score += 20
        elif vol_ratio > 2:
            score += 12

    px = row["price"]
    hi, lo = row["high_24h"], row["low_24h"]
    rng = hi - lo or 1e-9
    pos_in_range = (px - lo) / rng
    detail["range_pos"] = round(pos_in_range, 2)
    if pos_in_range > 0.95 or pos_in_range < 0.05:
        score += 15

    ranges = [(k["h"] - k["l"]) for k in kl[-21:-1]]
    avg_range = mean(ranges) or 1e-9
    last_range = last["h"] - last["l"]
    if last_range > 2 * avg_range and last["v"] > 1.5 * avg_v:
        score += 15
        detail["liq_spike"] = round(last_range / avg_range, 2)

    mh = macd_hist(closes)
    detail["macd_h"] = round(mh, 6)
    if (mh > 0 and ch24 < 0) or (mh < 0 and ch24 > 0):
        score += 10

    if ch24 > 0 and r > 70:
        direction = "SHORT"
    elif ch24 < 0 and r < 30:
        direction = "LONG"
    elif mh > 0 and pos_in_range < 0.3:
        direction = "LONG"
    elif mh < 0 and pos_in_range > 0.7:
        direction = "SHORT"
    else:
        direction = "LONG" if ch24 < 0 else "SHORT"

    # SMC: сетап перебивает направление и даёт высокий score
    try:
        smc = analyze_smc(sym)
    except Exception:
        smc = None
    if smc:
        smc_base = 60 if smc["type"].startswith("CHoCH") else (55 if smc["type"].startswith("BOS") else 50)
        score = max(score + smc["score_boost"], smc_base)
        direction = smc["direction"]
        detail["smc"] = smc["type"]
        detail["smc_level"] = round(smc["structure_level"], 6)

    return {"symbol": sym, "price": px, "score": min(score, 100),
            "direction": direction, "change_24h": ch24,
            "volume_usd": row["volume_usd"], "detail": detail}


def scan(universe, top_n=10):
    results = []
    for row in universe:
        try:
            s = score_candidate(row)
            if s and s["score"] >= 30:
                results.append(s)
        except Exception:
            pass
        time.sleep(0.15)
    results.sort(key=lambda x: -x["score"])
    return results[:top_n]


def score_major(row):
    """Скоринг для крупняка на 1h таймфрейме. Сделки длиннее, score ниже-приоритет."""
    sym = row["symbol"]
    kl = fetch_klines(sym, "1h", 100)
    if len(kl) < 50:
        return None
    closes = [k["c"] for k in kl]
    vols = [k["v"] for k in kl]
    last = kl[-1]
    score = 0
    detail = {"timeframe": "1h"}

    ch24 = row["change_24h"]
    r = rsi(closes)
    detail["rsi"] = round(r, 1)
    if r > 70 or r < 30:
        score += 10

    avg_v = mean(vols[-20:-1])
    if avg_v > 0:
        vol_ratio = last["v"] / avg_v
        detail["vol_ratio"] = round(vol_ratio, 2)
        if vol_ratio > 2:
            score += 12

    px = row["price"]
    hi, lo = row["high_24h"], row["low_24h"]
    rng = hi - lo or 1e-9
    pos_in_range = (px - lo) / rng
    detail["range_pos"] = round(pos_in_range, 2)
    if pos_in_range > 0.95 or pos_in_range < 0.05:
        score += 10

    mh = macd_hist(closes)
    detail["macd_h"] = round(mh, 6)

    direction = "LONG" if pos_in_range < 0.4 else "SHORT"

    # SMC на 1h
    try:
        smc = analyze_smc(sym, interval="1h")
    except Exception:
        smc = None
    if smc:
        smc_base = 55 if smc["type"].startswith("CHoCH") else (50 if smc["type"].startswith("BOS") else 45)
        score = max(score + smc["score_boost"], smc_base)
        direction = smc["direction"]
        detail["smc"] = smc["type"]
        detail["smc_level"] = round(smc["structure_level"], 6)

    return {
        "symbol": sym, "price": px, "score": min(score, 100),
        "direction": direction, "change_24h": ch24,
        "volume_usd": row["volume_usd"], "detail": detail,
        "is_major": True,
    }
