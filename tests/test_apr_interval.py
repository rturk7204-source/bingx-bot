"""tests/test_apr_interval.py — Block 8.7 APR formula based on actual funding interval.

Bug (2026-05-01): rotation.py:370 hardcoded `* 3` (8h interval, 3 payouts/day).
BingX moved most pairs to 4h interval (6 payouts/day) → rotation saw HALF the
real APR and made bad selection decisions.

Fix: use `fundingIntervalHours` from premiumIndex API to compute payouts_per_day.
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401


def compute_apr_pct(stab_avg, payouts_per_day):
    """Дублирует логику rotation.py Block 8.7 для unit-теста."""
    return stab_avg * payouts_per_day * 365 * 100


def payouts_from_interval(interval_h):
    """Дублирует логику rotation.py Block 8.7."""
    return 24 / interval_h if interval_h and interval_h > 0 else 3


def test_4h_interval_gives_6_payouts():
    assert payouts_from_interval(4) == 6


def test_8h_interval_gives_3_payouts():
    assert payouts_from_interval(8) == 3


def test_zero_or_none_falls_back_to_3():
    """Защита от 0 / None в API — fallback на старое поведение (3 выплаты)."""
    assert payouts_from_interval(0) == 3
    assert payouts_from_interval(None) == 3


def test_river_4h_apr_calculation():
    """RIVER-USDT: rate 0.000349 / 4h → ~76.4% APR (не 38%)."""
    stab_avg = 0.000349
    apr = compute_apr_pct(stab_avg, payouts_from_interval(4))
    assert 75.0 < apr < 78.0, f"Expected ~76%, got {apr:.1f}%"


def test_old_8h_formula_was_half():
    """Регрессия: старая формула давала половину при 4h интервале."""
    stab_avg = 0.000349
    old_apr = compute_apr_pct(stab_avg, 3)   # старое: всегда 3
    new_apr = compute_apr_pct(stab_avg, 6)   # новое: 4h → 6
    assert abs(new_apr - 2 * old_apr) < 0.01


def test_8h_interval_unchanged():
    """Для пар с реальным 8h интервалом APR не меняется."""
    stab_avg = 0.0005
    new_apr = compute_apr_pct(stab_avg, payouts_from_interval(8))
    old_apr = compute_apr_pct(stab_avg, 3)
    assert abs(new_apr - old_apr) < 0.01
