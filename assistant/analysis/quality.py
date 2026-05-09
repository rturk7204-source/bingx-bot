"""
Quality filters: фильтруем сетапы перед расчётом qty.
Цель — отсекать прострелы и сетапы против HTF.
"""
from .klines import fetch_klines


def _atr(K, n=14):
    if len(K) < n + 1:
        return 0
    trs = []
    for i in range(1, len(K)):
        h, l, pc = K[i]["h"], K[i]["l"], K[i-1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def _swings(K, left=3, right=3):
    """Все swing high/low за период."""
    highs, lows = [], []
    for i in range(left, len(K) - right):
        if all(K[i]["h"] > K[i-j]["h"] for j in range(1, left+1)) and \
           all(K[i]["h"] > K[i+j]["h"] for j in range(1, right+1)):
            highs.append((i, K[i]["h"]))
        if all(K[i]["l"] < K[i-j]["l"] for j in range(1, left+1)) and \
           all(K[i]["l"] < K[i+j]["l"] for j in range(1, right+1)):
            lows.append((i, K[i]["l"]))
    return highs, lows


def check_quality(symbol, direction, entry, sl, tp, interval="15m"):
    """
    Возвращает (ok: bool, reason: str|None, info: dict).
    Прогоняет 4 фильтра. Любой провал = отказ.
    """
    info = {}

    # 0. BTC контекст: если BTC валится >0.7% за 1ч — не лонг; растёт >0.7% — не шорт
    try:
        Kbtc = fetch_klines("BTC-USDT", "1h", 5)
        if len(Kbtc) >= 2:
            btc_pct = (Kbtc[-1]["c"] - Kbtc[-2]["c"]) / Kbtc[-2]["c"] * 100
            info["btc_1h_pct"] = round(btc_pct, 2)
            if direction == "LONG" and btc_pct < -0.7:
                return False, f"BTC -{abs(btc_pct):.2f}% за 1ч (рынок падает)", info
            if direction == "SHORT" and btc_pct > 0.7:
                return False, f"BTC +{btc_pct:.2f}% за 1ч (рынок растёт)", info
    except Exception:
        pass

    # 1. HTF контекст: 1h тренд не против сделки сильнее 1.5%
    K1h = fetch_klines(symbol, "1h", 50)
    if len(K1h) >= 24:
        first = K1h[-20]["c"]
        last = K1h[-1]["c"]
        htf_pct = (last - first) / first * 100
        info["htf_1h_pct"] = round(htf_pct, 2)
        if direction == "LONG" and htf_pct < -1.5:
            return False, f"1h тренд против LONG ({htf_pct:.1f}%)", info
        if direction == "SHORT" and htf_pct > 1.5:
            return False, f"1h тренд против SHORT (+{htf_pct:.1f}%)", info

        # 1b. MOMENTUM REVERSAL ОТКЛЮЧЕН (step4: -3.3R EV при остальных равных)
        ch24 = (K1h[-1]["c"] - K1h[-24]["c"]) / K1h[-24]["c"] * 100
        ch2h = (K1h[-1]["c"] - K1h[-2]["c"]) / K1h[-2]["c"] * 100
        info["ch24h"] = round(ch24, 2)
        info["ch2h"] = round(ch2h, 2)

        # 1d. ANTI-COUNTERTREND: запрет ловить нож/верх на 24h-тренде
        if direction == "LONG" and ch24 < -3.0:
            return False, f"актив в нисходе 24h={ch24:+.1f}% (LONG = ловля ножа)", info
        if direction == "SHORT" and ch24 > 3.0:
            return False, f"актив в восходе 24h={ch24:+.1f}% (SHORT против тренда)", info

    # 1c. ANTI-FADE ОТКЛЮЧЕН (step4: -2.0R EV)

    # 2. Дистанция до ближайшего препятствия
    K = fetch_klines(symbol, interval, 100)
    if len(K) < 30:
        return False, "мало баров для анализа", info
    R = abs(entry - sl)
    if R <= 0:
        return False, "R = 0", info
    highs, lows = _swings(K, 3, 3)
    a_clust = _atr(K, 14)
    # кластеризуем близкие swings (в пределах 1 ATR) — берём дальний край
    raw = []
    if direction == "LONG":
        raw = sorted([hi for _, hi in highs[-15:] if hi > entry])
    else:
        raw = sorted([lo for _, lo in lows[-15:] if lo < entry], reverse=True)
    clustered = []
    for px in raw:
        if not clustered or abs(px - clustered[-1]) > a_clust:
            clustered.append(px)
        else:
            clustered[-1] = px  # двигаем границу кластера дальше
    obstacles = []
    for px in clustered:
        tag = "swing_high" if direction == "LONG" else "swing_low"
        obstacles.append((tag, px, abs(px - entry) / R))
    # 24h hi/lo как отдельный, но не главный
    hi24 = max(k["h"] for k in K[-96:])
    lo24 = min(k["l"] for k in K[-96:])
    if direction == "LONG" and hi24 > entry and (not obstacles or hi24 < obstacles[-1][1]):
        obstacles.append(("hi24", hi24, abs(hi24 - entry) / R))
    if direction == "SHORT" and lo24 < entry and (not obstacles or lo24 > obstacles[-1][1]):
        obstacles.append(("lo24", lo24, abs(lo24 - entry) / R))

    a_for_buf = _atr(K, 14)
    if obstacles:
        # первый барьер за пределами 1.7R = реалистичная цель
        target = next((o for o in obstacles if o[2] >= 1.7), None)
        nearest = min(obstacles, key=lambda x: x[2])
        info["nearest_obstacle"] = f"{nearest[0]}@{nearest[1]} ({nearest[2]:.2f}R)"
        if target is None:
            return False, f"все препятствия в <1.7R (ближайшее {nearest[2]:.2f}R)", info
        nearest = target
        # ближайшее препятствие — реалистичный TP (с запасом 1 ATR не доходя)
        obs_price = nearest[1]
        if direction == "LONG":
            adj_tp = obs_price - a_for_buf
        else:
            adj_tp = obs_price + a_for_buf
        adj_dist = abs(adj_tp - entry)
        adj_rr = adj_dist / R if R > 0 else 0
        info["adjusted_tp"] = round(adj_tp, 6)
        info["adjusted_rr"] = round(adj_rr, 2)
        if adj_rr < 1.7:
            return False, f"до {nearest[0]} только {adj_rr:.2f}R (минимум 1.7R)", info
        # передаём наружу новый TP
        info["use_tp"] = adj_tp

    # 3. Запас хода: ATR(15m) × 12 баров ≥ дистанция до TP
    a = _atr(K, 14)
    tp_dist = abs(tp - entry)
    bars_needed = tp_dist / a if a > 0 else 999
    info["atr"] = round(a, 6)
    info["bars_to_tp"] = int(bars_needed)
    if bars_needed > 12:
        return False, f"до TP нужно ~{int(bars_needed)} баров (>12)", info

    # 4. Стоп не вплотную к недавнему wick'у
    last5 = K[-5:]
    for k in last5:
        wick_h = k["h"]
        wick_l = k["l"]
        if direction == "LONG":
            if wick_l < entry and abs(entry - wick_l) / entry < 0.001:
                return False, "только что был wick вниз у входа (риск повтора)", info
        if direction == "SHORT":
            if wick_h > entry and abs(wick_h - entry) / entry < 0.001:
                return False, "только что был wick вверх у входа (риск повтора)", info


    # 5. Свежесть SMC: если CHoCH/BOS дальше 30 баров назад — сетап остыл
    try:
        from .smc import analyze_smc
        smc = analyze_smc(symbol, interval, 100)
        last_event_idx = smc.get("event_idx") if isinstance(smc, dict) else None
        if isinstance(last_event_idx, int):
            bars_ago = len(K) - 1 - last_event_idx
            info["smc_age_bars"] = bars_ago
            if bars_ago > 12:
                return False, f"SMC старее 12 баров ({bars_ago})", info
    except Exception:
        pass


    # 6. MTF подтверждение: 1h EMA50 — направление совпадает с сделкой
    try:
        K1h_full = fetch_klines(symbol, "1h", 60)
        if len(K1h_full) >= 51:
            closes = [k["c"] for k in K1h_full]
            # EMA50
            k_mult = 2 / 51
            ema = sum(closes[:50]) / 50
            for c in closes[50:]:
                ema = c * k_mult + ema * (1 - k_mult)
            last_close = closes[-1]
            ema_dist_pct = (last_close - ema) / ema * 100
            info["mtf_1h_ema50"] = round(ema, 6)
            info["mtf_dist_pct"] = round(ema_dist_pct, 2)
            # MTF EMA50 фильтр
            if direction == "LONG" and last_close < ema:
                return False, f"1h EMA50 {ema:.4f} выше цены ({ema_dist_pct:.2f}%) — против LONG", info
            if direction == "SHORT" and last_close > ema:
                return False, f"1h EMA50 {ema:.4f} ниже цены (+{ema_dist_pct:.2f}%) — против SHORT", info
    except Exception:
        pass

    return True, None, info
