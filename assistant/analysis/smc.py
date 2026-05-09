"""Smart Money Concepts: swing points, BOS, CHoCH, OB, FVG, liquidity."""
from ..core import exchange as ex
from .klines import fetch_klines


def find_swings(klines, left=2, right=2):
    """Фрактальные swing highs и lows.
    Возвращает [(idx, price, 'H'|'L'), ...]"""
    swings = []
    for i in range(left, len(klines) - right):
        h = klines[i]["h"]
        l = klines[i]["l"]
        is_high = all(klines[i-j-1]["h"] < h for j in range(left)) and \
                  all(klines[i+j+1]["h"] < h for j in range(right))
        is_low = all(klines[i-j-1]["l"] > l for j in range(left)) and \
                 all(klines[i+j+1]["l"] > l for j in range(right))
        if is_high:
            swings.append((i, h, "H"))
        if is_low:
            swings.append((i, l, "L"))
    return swings


def detect_bos_choch(klines, swings):
    """Определяет последнее структурное событие.
    BOS = пробой в направлении тренда. CHoCH = пробой против тренда.
    Берём ПОСЛЕДНИЙ swing любого типа и проверяем пробой именно его уровня —
    это симметрично для LONG/SHORT и убирает баг с порядком свингов."""
    if len(swings) < 4:
        return None
    last_close = klines[-1]["c"]
    highs = [s for s in swings if s[2] == "H"]
    lows  = [s for s in swings if s[2] == "L"]
    if len(highs) < 2 or len(lows) < 2:
        return None

    # тренд по последним 2 H и 2 L
    last_h, prev_h = highs[-1], highs[-2]
    last_l, prev_l = lows[-1],  lows[-2]
    trend_up = last_h[1] > prev_h[1] and last_l[1] > prev_l[1]
    trend_dn = last_h[1] < prev_h[1] and last_l[1] < prev_l[1]

    # Кандидаты пробоя: цена выше последнего H -> LONG, ниже последнего L -> SHORT.
    # Если оба выполнены (редко) — выбираем тот, чей swing СВЕЖЕЕ.
    long_break  = last_close > last_h[1]
    short_break = last_close < last_l[1]

    if long_break and short_break:
        if last_h[0] >= last_l[0]:
            short_break = False
        else:
            long_break = False

    if long_break:
        return {"type": "BOS" if trend_up else "CHoCH", "dir": "LONG",
                "level": last_h[1], "swing_idx": last_h[0]}
    if short_break:
        return {"type": "BOS" if trend_dn else "CHoCH", "dir": "SHORT",
                "level": last_l[1], "swing_idx": last_l[0]}
    return None


def find_order_block(klines, event):
    """Order Block = последняя противоположная свеча перед импульсом, который пробил структуру."""
    if not event:
        return None
    direction = event["dir"]
    swing_idx = event["swing_idx"]
    # ищем от swing к ближайшей противоположной свече
    for i in range(swing_idx, max(swing_idx - 20, 0), -1):
        if i >= len(klines):
            continue
        k = klines[i]
        is_bear = k["c"] < k["o"]
        is_bull = k["c"] > k["o"]
        if direction == "LONG" and is_bear:
            return {"high": k["h"], "low": k["l"], "idx": i}
        if direction == "SHORT" and is_bull:
            return {"high": k["h"], "low": k["l"], "idx": i}
    return None


def find_fvg(klines, lookback=20):
    """Fair Value Gap: разрыв между свечой[i-1] и свечой[i+1].
    Bullish FVG: low[i+1] > high[i-1]. Bearish FVG: high[i+1] < low[i-1]."""
    fvgs = []
    start = max(1, len(klines) - lookback)
    for i in range(start, len(klines) - 1):
        if klines[i+1]["l"] > klines[i-1]["h"]:
            fvgs.append({"type": "bull", "top": klines[i+1]["l"], "bot": klines[i-1]["h"], "idx": i})
        if klines[i+1]["h"] < klines[i-1]["l"]:
            fvgs.append({"type": "bear", "top": klines[i-1]["l"], "bot": klines[i+1]["h"], "idx": i})
    return fvgs


def find_liquidity_sweep(klines, swings, recent_bars=3, min_pierce_pct=0.3):
    """Строгий sweep: за последние N свечей был значимый прокол + закрытие назад.
    min_pierce_pct — минимум 0.3% прокола для значимости."""
    if len(swings) < 2 or len(klines) < recent_bars + 1:
        return None
    # рассматриваем только последние recent_bars свечей
    last_n = klines[-recent_bars:]
    # свинги старше последних recent_bars свечей (иначе сам свинг = текущая свеча)
    older_swings = [s for s in swings if s[0] < len(klines) - recent_bars]
    if not older_swings:
        return None
    # последние H и L из старых свингов
    last_h = next((s for s in reversed(older_swings) if s[2] == "H"), None)
    last_l = next((s for s in reversed(older_swings) if s[2] == "L"), None)

    for k in last_n:
        if last_h:
            pierce = (k["h"] - last_h[1]) / last_h[1] * 100
            if pierce >= min_pierce_pct and k["c"] < last_h[1]:
                return {"type": "sweep_high", "level": last_h[1], "dir": "SHORT", "pierce_pct": round(pierce, 2)}
        if last_l:
            pierce = (last_l[1] - k["l"]) / last_l[1] * 100
            if pierce >= min_pierce_pct and k["c"] > last_l[1]:
                return {"type": "sweep_low", "level": last_l[1], "dir": "LONG", "pierce_pct": round(pierce, 2)}
    return None


def analyze_smc(symbol, interval="15m", limit=100):
    """Главный SMC-анализ символа. Возвращает сетап или None."""
    kl = fetch_klines(symbol, interval, limit)
    if len(kl) < 30:
        return None

    swings = find_swings(kl, left=2, right=2)
    event = detect_bos_choch(kl, swings)
    ob = find_order_block(kl, event) if event else None
    fvgs = find_fvg(kl, lookback=20)
    sweep = find_liquidity_sweep(kl, swings, recent_bars=3)

    # собираем сетап: приоритет CHoCH+OB > BOS+OB > sweep > FVG
    setup = None
    if event and ob:
        last_fvg = None
        for f in fvgs[::-1]:
            if (event["dir"] == "LONG" and f["type"] == "bull") or \
               (event["dir"] == "SHORT" and f["type"] == "bear"):
                last_fvg = f
                break
        setup = {
            "symbol": symbol,
            "direction": event["dir"],
            "type": f"{event['type']}+OB" + ("+FVG" if last_fvg else ""),
            "structure_level": event["level"],
            "ob_high": ob["high"],
            "ob_low": ob["low"],
            "fvg": last_fvg,
            "score_boost": 30 if event["type"] == "CHoCH" else 25,
        }
    elif sweep:
        setup = {
            "symbol": symbol,
            "direction": sweep["dir"],
            "type": "Liquidity sweep",
            "structure_level": sweep["level"],
            "ob_high": None, "ob_low": None, "fvg": None,
            "score_boost": 20,
        }

    return setup
