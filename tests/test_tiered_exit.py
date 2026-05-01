"""tests/test_tiered_exit.py — Block 8 (Часть 1): should_force_exit_tiered().

Проверяет tiered logic:
  - <6h: только negative funding выгоняет
  - 6-24h (young): требуется bad_periods >= 2
  - 24-48h (mid): floor 0.00025
  - 48-96h (mature): floor 0.00040
  - >96h (old): floor 0.00055
  - Negative funding всегда выгоняет (даже в grace)
  - Reason формат: 'tier_<name>' или 'negative_funding'
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401

import rotation


# ── Grace period (<6h) ────────────────────────────────────────────────────
def test_grace_does_not_exit_low_rate():
    """Молодая (<6h) позиция с низким rate → НЕ exit (grace)."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00005, age_hours=2.0, bad_periods=5
    )
    assert should is False
    assert reason == ""


def test_grace_does_not_exit_zero_rate():
    """Молодая позиция с rate=0 → НЕ exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.0, age_hours=1.0, bad_periods=10
    )
    assert should is False


def test_grace_exits_negative_funding():
    """<6h + negative funding → ВСЁ РАВНО exit (платим за позицию)."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=-0.0001, age_hours=1.0, bad_periods=0
    )
    assert should is True
    assert reason == "negative_funding"


# ── Young tier (6-24h) ────────────────────────────────────────────────────
def test_young_no_bad_periods_no_exit():
    """6-24h, низкий rate, но bad_periods=0 → НЕ exit (нужно подтверждение)."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00005, age_hours=12.0, bad_periods=0
    )
    assert should is False


def test_young_one_bad_no_exit():
    """6-24h, bad_periods=1 (нужно >=2) → НЕ exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00005, age_hours=12.0, bad_periods=1
    )
    assert should is False


def test_young_two_bad_low_rate_exits():
    """6-24h, bad_periods=2, rate < floor → exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00005, age_hours=12.0, bad_periods=2
    )
    assert should is True
    assert reason == "tier_young"


def test_young_bad_but_good_rate_no_exit():
    """6-24h, bad_periods=5, но rate выше young floor → НЕ exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00020, age_hours=12.0, bad_periods=5
    )
    assert should is False


# ── Mid tier (24-48h, floor 0.00025) ──────────────────────────────────────
def test_mid_low_rate_exits():
    """24-48h, rate < 0.00025 → exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00020, age_hours=36.0, bad_periods=0
    )
    assert should is True
    assert reason == "tier_mid"


def test_mid_at_floor_no_exit():
    """24-48h, rate = floor → НЕ exit (strict <)."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00025, age_hours=36.0, bad_periods=0
    )
    assert should is False


def test_mid_above_floor_no_exit():
    """24-48h, rate > floor → НЕ exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00050, age_hours=36.0, bad_periods=0
    )
    assert should is False


# ── Mature tier (48-96h, floor 0.00040) ───────────────────────────────────
def test_mature_low_rate_exits():
    """48-96h, rate < 0.00040 → exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00035, age_hours=72.0, bad_periods=0
    )
    assert should is True
    assert reason == "tier_mature"


def test_mature_above_floor_no_exit():
    """48-96h, rate >= 0.00040 → НЕ exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00050, age_hours=72.0, bad_periods=0
    )
    assert should is False


# ── Old tier (>96h, floor 0.00055) ────────────────────────────────────────
def test_old_low_rate_exits():
    """>96h, rate < 0.00055 → exit (хочешь сидеть неделю — давай 60% APR)."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00050, age_hours=120.0, bad_periods=0
    )
    assert should is True
    assert reason == "tier_old"


def test_old_above_floor_no_exit():
    """>96h, rate >= 0.00055 → НЕ exit."""
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00060, age_hours=120.0, bad_periods=0
    )
    assert should is False


# ── Negative funding overrides everything ─────────────────────────────────
def test_negative_funding_at_any_age():
    """Negative funding → exit в любом возрасте."""
    for age in [0.0, 5.0, 23.0, 47.0, 95.0, 200.0]:
        should, reason = rotation.should_force_exit_tiered(
            current_rate=-0.0001, age_hours=age, bad_periods=0
        )
        assert should is True
        assert reason == "negative_funding"


# ── Boundary checks ───────────────────────────────────────────────────────
def test_exact_grace_boundary_no_exit():
    """age=6.0 (точно граница grace) — попадает уже в young, нужны bad_periods."""
    # 6.0 не < 6.0, значит не grace; tier = young (max_age 24)
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00005, age_hours=6.0, bad_periods=0
    )
    assert should is False  # young требует bad_periods >= 2


def test_custom_tiers_work():
    """Кастомные tiers работают."""
    custom = (
        (10.0, 0.001, "fresh"),
        (1e9,  0.005, "rest"),
    )
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.0009, age_hours=20.0, bad_periods=0,
        tiers=custom, grace_age_h=0.0,
    )
    assert should is True
    assert reason == "tier_rest"


def test_aggressive_floor_old_at_46pct_apr():
    """46% APR (rate≈0.00042) НА old tier → exit (floor 0.00055 = 60% APR)."""
    # 0.00042 < 0.00055 → exit
    should, reason = rotation.should_force_exit_tiered(
        current_rate=0.00042, age_hours=150.0, bad_periods=0
    )
    assert should is True
    assert reason == "tier_old"


TESTS = [
    test_grace_does_not_exit_low_rate,
    test_grace_does_not_exit_zero_rate,
    test_grace_exits_negative_funding,
    test_young_no_bad_periods_no_exit,
    test_young_one_bad_no_exit,
    test_young_two_bad_low_rate_exits,
    test_young_bad_but_good_rate_no_exit,
    test_mid_low_rate_exits,
    test_mid_at_floor_no_exit,
    test_mid_above_floor_no_exit,
    test_mature_low_rate_exits,
    test_mature_above_floor_no_exit,
    test_old_low_rate_exits,
    test_old_above_floor_no_exit,
    test_negative_funding_at_any_age,
    test_exact_grace_boundary_no_exit,
    test_custom_tiers_work,
    test_aggressive_floor_old_at_46pct_apr,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_tiered_exit] {len(TESTS)} passed")
