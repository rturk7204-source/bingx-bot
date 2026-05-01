"""tests/test_kelly.py — adaptive Kelly sizing + composite score."""
from tests._bootstrap import cleanup_test_dir  # noqa: F401
from rotation_v2_score import (
    composite_score,
    funding_history_stats,
    adaptive_kelly_size,
    should_rotate_by_score,
    can_rotate_by_age,
    position_age_hours,
)
from datetime import datetime, timezone


def test_composite_positive_ev():
    s = composite_score(
        rate=0.0005,  # ~55% APR
        stability={"history": [0.0005] * 6, "positive_count": 6},
        slippage_pct=0.002,
        notional_usd=80,
        horizon_days=7,
    )
    assert s["score"] > 0, f"high stability + decent rate must yield positive EV: {s}"
    assert s["stability_factor"] == 1.0


def test_composite_zero_capital():
    s = composite_score(0.001, {}, 0.001, 0, 7)
    assert s["score"] == 0.0


def test_composite_negative_when_high_slippage():
    """Высокий slippage должен делать score отрицательным даже при норм rate."""
    s = composite_score(
        rate=0.0001,
        stability={"history": [0.0001] * 6, "positive_count": 6},
        slippage_pct=0.05,  # 5% — катастрофа
        notional_usd=80,
        horizon_days=1,
    )
    assert s["score"] < 0, f"high slippage must yield negative: {s}"


def test_funding_stats_zero_when_empty():
    st = funding_history_stats([])
    assert st["n"] == 0 and st["mean"] == 0


def test_funding_stats_basic():
    st = funding_history_stats([0.001, 0.002, 0.0015, 0.0012, 0.0008,
                                0.0011, 0.0014, 0.0013, 0.0009, 0.001,
                                0.0012, 0.0011])
    assert st["n"] == 12
    assert st["mean"] > 0
    assert st["std"] > 0
    assert st["sharpe"] > 0


def test_kelly_below_floor_returns_zero():
    cand = {"apr_pct": 30, "stability": {"history": []}}
    size = adaptive_kelly_size(cand, 1000, [cand], min_apr_floor_pct=40)
    assert size == 0.0, "below APR floor must return 0"


def test_kelly_basic_allocation():
    # Один кандидат, total=2000, APR=80% → должна быть значимая аллокация
    cand = {"apr_pct": 80, "stability": {"history": [0.001] * 12}}
    size = adaptive_kelly_size(cand, 2000, [cand],
                               min_position_usd=80, max_position_pct=0.30)
    assert size >= 80, f"single candidate must allocate ≥ min: {size}"
    assert size <= 2000 * 0.30, f"must respect max_position_pct: {size}"


def test_kelly_variance_penalty():
    """High variance candidate должен получить меньше чем low variance при одинаковом APR."""
    low_var = {"apr_pct": 80, "stability": {"history": [0.001] * 12}}
    high_var = {"apr_pct": 80,
                "stability": {"history": [0.0001, 0.005, 0.0001, 0.005,
                                          0.0001, 0.005, 0.0001, 0.005,
                                          0.0001, 0.005, 0.0001, 0.005]}}
    s_low = adaptive_kelly_size(low_var, 5000, [low_var])
    s_high = adaptive_kelly_size(high_var, 5000, [high_var])
    assert s_low >= s_high, f"low_var ({s_low}) должен быть ≥ high_var ({s_high})"


def test_should_rotate_existing_loss():
    ok, _, _ = should_rotate_by_score(-1.0, 0.5)
    assert ok is True, "existing position losing → rotate to any positive"
    ok, _, _ = should_rotate_by_score(-1.0, -0.1)
    assert ok is False


def test_should_rotate_hysteresis():
    ok, _, _ = should_rotate_by_score(1.0, 1.10)  # 10% better
    assert ok is False, "10% improvement insufficient"
    ok, _, _ = should_rotate_by_score(1.0, 1.30)  # 30% better
    assert ok is True


def test_age_guard():
    fresh = {"entry_time": datetime.now(timezone.utc).isoformat()}
    old = {"entry_time": "2026-04-01T00:00:00+00:00"}
    assert position_age_hours(fresh) < 1.0
    assert position_age_hours(old) > 24.0
    assert can_rotate_by_age(fresh, 8.0)[0] is False
    assert can_rotate_by_age(old, 8.0)[0] is True


TESTS = [
    test_composite_positive_ev,
    test_composite_zero_capital,
    test_composite_negative_when_high_slippage,
    test_funding_stats_zero_when_empty,
    test_funding_stats_basic,
    test_kelly_below_floor_returns_zero,
    test_kelly_basic_allocation,
    test_kelly_variance_penalty,
    test_should_rotate_existing_loss,
    test_should_rotate_hysteresis,
    test_age_guard,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_kelly] {len(TESTS)} passed")
