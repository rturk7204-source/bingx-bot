"""Расчёт сделки: SL за структурой + 2% буфер, без ATR-клампа."""
from statistics import mean
from ..core import exchange as ex
from ..analysis.klines import fetch_klines
from ..analysis import quality

RISK_PCT = 1.0
RR = 3.0
SL_BUFFER = 0.01            # 2% за уровнем
COMMISSION = 0.0005
MAX_LEVERAGE = 10

ATR_MIN = 0.4               # ниже — висяк, не торгуем
ATR_HOT = 5.0               # выше — флаг HOT


def atr(klines, period=14):
    if len(klines) < period + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["h"], klines[i]["l"], klines[i-1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return mean(trs[-period:])


def find_structure(klines, direction, lookback=8):
    """Локальный экстремум — зона стопов/ликвидности."""
    recent = klines[-lookback:]
    if direction == "LONG":
        return min(k["l"] for k in recent)
    return max(k["h"] for k in recent)


def calc_leverage(atr_pct, sl_pct):
    """Плечо так чтобы стоп съел не больше ~50% маржи."""
    if sl_pct <= 0:
        return 1
    # цель: sl_pct * lev <= 50%
    lev = int(50 / sl_pct)
    lev = max(1, min(lev, MAX_LEVERAGE))
    return lev


def calc_trade(signal, balance_usdt):
    sym = signal["symbol"]
    direction = signal["direction"]
    px = signal["price"]

    kl = fetch_klines(sym, "15m", 100)
    if len(kl) < 30:
        return {"error": "мало свечей"}

    a = atr(kl, 14)
    if a == 0 or px == 0:
        return {"error": "нулевой ATR"}
    atr_pct = a / px * 100

    if atr_pct < ATR_MIN:
        return {"error": f"ATR {atr_pct:.2f}% < {ATR_MIN}% (висяк)"}

    flag = "HOT" if atr_pct > ATR_HOT else ""

    entry = px
    struct = find_structure(kl, direction, lookback=8)

    if direction == "LONG":
        sl = struct * (1 - SL_BUFFER)
        sl_dist = entry - sl
        tp = entry + sl_dist * RR
    else:
        sl = struct * (1 + SL_BUFFER)
        sl_dist = sl - entry
        tp = entry - sl_dist * RR

    if sl_dist <= 0:
        return {"error": "SL по неправильную сторону от entry"}

    sl_pct = sl_dist / entry * 100
    if sl_pct > 8.0:
        return {"error": f"SL {sl_pct:.1f}% > 8% (структура слишком далеко)"}
    lev = calc_leverage(atr_pct, sl_pct)

    risk_usd = balance_usdt * RISK_PCT / 100
    qty = risk_usd / sl_dist
    notional = qty * entry
    margin = notional / lev

    be_offset = entry * COMMISSION * 2
    breakeven = entry + be_offset if direction == "LONG" else entry - be_offset

    bars_to_tp = int(sl_dist * RR / a) if a > 0 else 0

    # PnL в долларах (qty * dist - комиссия)
    commission_total = notional * COMMISSION * 2  # вход + выход
    profit_usd = qty * sl_dist * RR - commission_total
    _q_ok, _q_reason, _q_info = quality.check_quality(signal["symbol"], direction, entry, sl, tp, signal.get("interval","15m"))
    if _q_ok and _q_info.get("use_tp"):
        tp = round(_q_info["use_tp"], 6)
        tp_dist = abs(tp - entry)
        profit_usd = qty * tp_dist - commission_total
    loss_usd = qty * sl_dist + commission_total
    profit_pct_margin = profit_usd / margin * 100 if margin > 0 else 0
    loss_pct_margin = loss_usd / margin * 100 if margin > 0 else 0

    return {
        "symbol": sym,
        "direction": direction,
        "flag": flag,
        "entry": round(entry, 6),
        "sl": round(sl, 6),
        "tp": round(tp, 6),
        "breakeven": round(breakeven, 6),
        "structure": round(struct, 6),
        "sl_pct": round(sl_pct, 2),
        "rr": RR,
        "quality_ok": _q_ok,
        "quality_reason": _q_reason,
        "quality_info": _q_info,
        "qty": round(qty, 4),
        "notional_usd": round(notional, 2),
        "margin_usd": round(margin, 2),
        "leverage": lev,
        "risk_usd": round(risk_usd, 2),
        "atr_pct": round(atr_pct, 3),
        "duration_min": bars_to_tp * 15,
        "score": signal["score"],
        "smc_type": signal.get("detail", {}).get("smc"),
        "profit_usd": round(profit_usd, 2),
        "loss_usd": round(loss_usd, 2),
        "profit_pct_margin": round(profit_pct_margin, 1),
        "loss_pct_margin": round(loss_pct_margin, 1),
        "commission_usd": round(commission_total, 2),
    }
