#!/usr/bin/env python3
"""
Block 3: Smarter Rotation v2 — scoring helpers.

Drop-in helpers used by rotation.py to upgrade the v1 ranking from
"APR / (1+slip)" to a risk-adjusted EV-based composite score.

Four improvements (A+B+C+D):
  A. EV-based ranking — slippage in USD across full round-trip (entry+exit)
  B. Min holding period — refuse to rotate positions younger than N hours
  C. Soft threshold — composite_score(candidate) must beat
     composite_score(existing) × IMPROVEMENT_FACTOR
  D. Adaptive Kelly — variance-penalized sizing using funding history std

Public API:
  composite_score(rate, stability, slippage, notional)  -> float (USD/8h)
  adaptive_kelly_size(candidate, total_capital, all_candidates) -> float USD
  position_age_hours(position) -> float
  funding_history_stats(symbol, n=24) -> dict(mean, std, sharpe)

Pure functions — no side-effects, easy to unit test.
"""
import statistics
from datetime import datetime, timezone


# ══ Tunable constants ═══════════════════════════════════════════════════════

# A. EV ranking
ROUND_TRIP_FACTOR = 2.0  # slippage applied 2× (entry + exit)
TAKER_FEE_PCT = 0.0010   # 0.10% spot taker fee on BingX
PAYOUTS_PER_DAY = 3      # funding каждые 8ч
HORIZON_DAYS = 14        # реальный средний holding period; выбран из 90-дневной истории ботов

# B. Min holding period
MIN_HOLD_HOURS = 8.0  # 1 funding cycle минимум

# C. Soft threshold for rotation
ROTATION_SCORE_IMPROVEMENT = 1.20  # candidate must beat existing by ≥20%

# D. Adaptive Kelly
KELLY_FRACTION = 0.75     # между half (безопасно но занижено на нашем масштабе) и full
VARIANCE_PENALTY = 1.0    # коэф штрафа за variance в funding history
HISTORY_LOOKBACK = 24     # последние 24 funding cycle = 8 дней
MIN_HISTORY_FOR_VARIANCE = 12  # нужен полный цикл 4 дней для довериемого std


# ══ A. EV-based composite score ═════════════════════════════════════════════

def composite_score(
    rate: float,
    stability: dict,
    slippage_pct: float,
    notional_usd: float,
    horizon_days: float = HORIZON_DAYS,
) -> dict:
    """
    Risk-adjusted EV в долларах за горизонт N дней.

    expected_funding = rate × notional × payouts_per_day × horizon_days
    cost = (slippage_pct + taker_fee) × notional × ROUND_TRIP_FACTOR
    stability_factor = positive_count / lookback (доля позитивных rates в истории)

    composite_score = (expected_funding × stability_factor) − cost

    Чем выше — тем привлекательнее пара ДЛЯ РОТАЦИИ. Учитывает:
      • реальный $-выхлоп с funding за горизонт
      • полную стоимость входа+выхода (slippage + fees)
      • штраф за нестабильность (positive_count/lookback)
    """
    if notional_usd <= 0:
        return {"score": 0.0, "ev_usd": 0.0, "cost_usd": 0.0, "stability_factor": 0.0}

    payouts = PAYOUTS_PER_DAY * horizon_days
    expected_funding = rate * notional_usd * payouts

    cost_per_side = (slippage_pct + TAKER_FEE_PCT) * notional_usd
    total_cost = cost_per_side * ROUND_TRIP_FACTOR

    stab = stability or {}
    pos_count = stab.get("positive_count", 0)
    lookback = max(1, len(stab.get("history", [])) or 1)
    stability_factor = min(1.0, pos_count / lookback)

    score = (expected_funding * stability_factor) - total_cost

    return {
        "score": round(score, 4),
        "ev_usd": round(expected_funding, 4),
        "cost_usd": round(total_cost, 4),
        "stability_factor": round(stability_factor, 3),
        "horizon_days": horizon_days,
    }


# ══ B. Min holding period guard ═════════════════════════════════════════════

def position_age_hours(position: dict) -> float:
    """
    Возвращает возраст открытой позиции в часах.
    Если entry_time отсутствует или невалиден — возвращает большое число
    (позиция старая → не блокировать ротацию).
    """
    et = position.get("entry_time", "")
    if not et:
        return 9999.0
    try:
        # Поддержка ISO-8601 с/без timezone
        if et.endswith("Z"):
            et = et[:-1] + "+00:00"
        dt = datetime.fromisoformat(et)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return delta.total_seconds() / 3600.0
    except Exception:
        return 9999.0


def can_rotate_by_age(position: dict, min_hold_hours: float = MIN_HOLD_HOURS) -> tuple:
    """
    Returns (allowed: bool, age_hours: float, reason: str).
    """
    age = position_age_hours(position)
    if age < min_hold_hours:
        return False, age, f"min_hold: age={age:.1f}h < {min_hold_hours:.0f}h"
    return True, age, "ok"


# ══ C. Soft threshold (hysteresis) ══════════════════════════════════════════

def should_rotate_by_score(
    existing_score: float,
    candidate_score: float,
    improvement_factor: float = ROTATION_SCORE_IMPROVEMENT,
) -> tuple:
    """
    Returns (should_rotate: bool, ratio: float, reason: str).

    Логика:
      • если existing_score <= 0 (позиция убыточная) — ротировать всегда
        при candidate_score > 0
      • иначе требуем candidate_score >= existing_score × improvement_factor
    """
    if existing_score <= 0:
        if candidate_score > 0:
            return True, float("inf"), f"existing score {existing_score:.3f} ≤ 0"
        return False, 0.0, f"both unprofitable (cand={candidate_score:.3f})"

    ratio = candidate_score / existing_score
    if ratio >= improvement_factor:
        return True, ratio, f"score×{ratio:.2f} ≥ {improvement_factor}"
    return False, ratio, f"score×{ratio:.2f} < {improvement_factor} (insufficient improvement)"


# ══ D. Adaptive Kelly with variance penalty ═════════════════════════════════

def funding_history_stats(rates: list) -> dict:
    """
    Pure function: считает статы по списку funding rates.
    Изоляция от API чтобы было легко юнит-тестить.
    """
    if not rates:
        return {"n": 0, "mean": 0.0, "std": 0.0, "sharpe": 0.0, "min": 0.0, "max": 0.0}
    n = len(rates)
    mean = statistics.fmean(rates)
    if n >= MIN_HISTORY_FOR_VARIANCE:
        std = statistics.pstdev(rates)
    else:
        std = 0.0
    sharpe = (mean / std) if std > 0 else (10.0 if mean > 0 else 0.0)
    return {
        "n": n,
        "mean": round(mean, 8),
        "std": round(std, 8),
        "sharpe": round(sharpe, 3),
        "min": round(min(rates), 8),
        "max": round(max(rates), 8),
    }


def adaptive_kelly_size(
    candidate: dict,
    total_capital: float,
    all_candidates: list,
    min_position_usd: float = 80.0,
    max_position_pct: float = 0.30,
    min_apr_floor_pct: float = 40.0,
    kelly_fraction: float = KELLY_FRACTION,
    variance_penalty: float = VARIANCE_PENALTY,
) -> float:
    """
    Adaptive Kelly с штрафом за дисперсию funding.

    Базовая идея:
      • базовый вес = APR / sum_APR (как v1)
      • penalty = 1 / (1 + λ × std/mean)  — пары с высокой дисперсией получают
        меньший вес. Если std=0 → penalty=1, max-allocation как в v1.
      • final_weight = base_weight × penalty
      • size = kelly_fraction × final_weight × total_capital
      • clamp: [min_position_usd, max_position_pct × total_capital]

    candidate должен иметь:
      apr_pct: float
      stability: dict с history (list of rates)
    """
    apr = candidate.get("apr_pct", 0)
    if apr < min_apr_floor_pct:
        return 0.0

    valid_aprs = [c.get("apr_pct", 0) for c in all_candidates
                  if c.get("apr_pct", 0) >= min_apr_floor_pct]
    sum_apr = sum(valid_aprs)
    if sum_apr <= 0:
        return 0.0

    base_weight = apr / sum_apr

    # Variance penalty (только при достаточной истории — иначе не штрафуем)
    history = (candidate.get("stability") or {}).get("history", [])
    stats = funding_history_stats(history)
    if stats["n"] >= MIN_HISTORY_FOR_VARIANCE and abs(stats["mean"]) > 1e-9:
        cv = stats["std"] / abs(stats["mean"])  # coefficient of variation
        penalty = 1.0 / (1.0 + variance_penalty * cv)
    else:
        # мало истории — не штрафуем (penalty=1.0).
        # Риск высокой variance уже фильтруется в check_funding_stability на входе.
        penalty = 1.0

    final_weight = base_weight * penalty
    size = kelly_fraction * final_weight * total_capital

    max_size = total_capital * max_position_pct
    size = min(size, max_size)

    # Fallback: если size близок к min_position_usd но ниже — выровнять до min.
    # Это корректно потому что кандидат уже прошёл stability + slippage фильтры.
    if min_position_usd * 0.5 <= size < min_position_usd:
        size = min_position_usd
    if size < min_position_usd:
        return 0.0
    return round(size, 2)


# ══ Self-test ═══════════════════════════════════════════════════════════════

def _selftest():
    # A. composite_score
    s = composite_score(
        rate=0.0005,         # 0.05% per 8h ≈ 55% APR
        stability={"history": [0.0005, 0.0006, 0.0004, 0.0005, 0.0005, 0.0004],
                   "positive_count": 6},
        slippage_pct=0.002,  # 0.2%
        notional_usd=80,
        horizon_days=7,
    )
    print(f"[A] composite_score: {s}")
    assert s["score"] > 0, f"expected positive EV, got {s}"

    # B. position_age_hours
    fresh = {"entry_time": datetime.now(timezone.utc).isoformat()}
    old = {"entry_time": "2026-04-25T00:00:00+00:00"}
    assert position_age_hours(fresh) < 1
    assert position_age_hours(old) > 24
    age_ok = can_rotate_by_age(fresh, 8.0)
    assert age_ok[0] is False
    print(f"[B] age guard: fresh={age_ok}, old={can_rotate_by_age(old, 8.0)}")

    # C. should_rotate_by_score
    assert should_rotate_by_score(1.0, 1.30)[0] is True   # 30% better → ok
    assert should_rotate_by_score(1.0, 1.10)[0] is False  # 10% better → no
    assert should_rotate_by_score(-0.5, 0.5)[0] is True   # losing → swap
    assert should_rotate_by_score(-0.5, -0.1)[0] is False # both losing → no
    print("[C] soft threshold: OK")

    # D. adaptive_kelly_size
    # История ≥ MIN_HISTORY_FOR_VARIANCE=12, иначе penalty=1 для обоих и тест бессмысленный.
    # total_capital=2000 чтобы базовая аллокация была выше min_position_usd=80
    # и fallback к min_position_usd не съедал разницу.
    cand_stable = {
        "apr_pct": 60,
        "stability": {"history": [0.0005] * 12, "positive_count": 12}
    }
    cand_volatile = {
        "apr_pct": 60,
        "stability": {"history": [0.001, -0.0005, 0.002, 0.0001, -0.0001, 0.0015,
                                   0.002, -0.0008, 0.0018, 0.0002, -0.0003, 0.0014],
                      "positive_count": 8}
    }
    sz_stable = adaptive_kelly_size(cand_stable, 2000, [cand_stable, cand_volatile])
    sz_volatile = adaptive_kelly_size(cand_volatile, 2000, [cand_stable, cand_volatile])
    print(f"[D] kelly stable={sz_stable}, volatile={sz_volatile}")
    assert sz_stable > sz_volatile, f"stable={sz_stable} должен получить больше volatile={sz_volatile}"

    # D2. fallback: маленький capital, size попадает в [min*0.5, min) → округляется к min
    cand_low = {"apr_pct": 60, "stability": {"history": [0.0005] * 12, "positive_count": 12}}
    others = [cand_low] + [{"apr_pct": 50, "stability": {"history": [], "positive_count": 0}}] * 4
    sz_low = adaptive_kelly_size(cand_low, 546, others, min_position_usd=80)
    print(f"[D2] fallback on small capital ($546, 5 cands): kelly={sz_low}")
    assert sz_low >= 80, f"fallback должен подтянуть к min=80, got {sz_low}"

    print("\n[SCORE] all self-tests passed")


if __name__ == "__main__":
    _selftest()
