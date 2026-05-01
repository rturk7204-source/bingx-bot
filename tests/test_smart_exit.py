"""tests/test_smart_exit.py — Block 7 (D): should_force_exit().

Проверяет логику forced exit на funding-rate threshold:
  - negative funding → exit немедленно (игнорируя min_hold)
  - low APR (rate < floor) → exit ТОЛЬКО если age >= min_hold_h
  - good rate → не выходим
  - custom thresholds работают
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401

import rotation


def test_negative_funding_exits_immediately():
    """Отрицательный funding → forced exit, даже если позиция молодая."""
    should, reason = rotation.should_force_exit(
        current_rate=-0.0001, age_hours=1.0
    )
    assert should is True
    assert reason == "negative_funding"


def test_negative_funding_exits_at_zero_age():
    """Граничный случай: age=0, negative rate → exit."""
    should, reason = rotation.should_force_exit(
        current_rate=-0.00001, age_hours=0.0
    )
    assert should is True
    assert reason == "negative_funding"


def test_zero_rate_does_not_exit_when_negative_threshold_is_zero():
    """rate=0 НЕ ниже SMART_EXIT_NEGATIVE_RATE=0 (strict <), age=0 → нет exit."""
    should, reason = rotation.should_force_exit(
        current_rate=0.0, age_hours=1.0
    )
    # rate=0 не triggers negative_funding, не triggers low_apr (age<min_hold)
    assert should is False
    assert reason == ""


def test_low_apr_with_old_position_exits():
    """rate < floor, age >= min_hold → exit с reason='low_apr'."""
    should, reason = rotation.should_force_exit(
        current_rate=0.00005,  # 5.5% APR — ниже floor 11%
        age_hours=25.0,        # старше 24ч min_hold
    )
    assert should is True
    assert reason == "low_apr"


def test_low_apr_with_young_position_does_not_exit():
    """rate < floor, age < min_hold → НЕ exit (даём шанс восстановиться)."""
    should, reason = rotation.should_force_exit(
        current_rate=0.00005,  # ниже floor
        age_hours=10.0,        # моложе 24ч
    )
    assert should is False
    assert reason == ""


def test_low_apr_at_exact_min_hold_exits():
    """Граничный случай: age == min_hold (24ч) — exit срабатывает."""
    should, reason = rotation.should_force_exit(
        current_rate=0.00005,
        age_hours=24.0,
    )
    assert should is True
    assert reason == "low_apr"


def test_good_rate_does_not_exit():
    """Высокий funding rate → не выходим, всё в порядке."""
    should, reason = rotation.should_force_exit(
        current_rate=0.00050,  # 55% APR — отличный rate
        age_hours=100.0,
    )
    assert should is False
    assert reason == ""


def test_rate_above_floor_does_not_exit_old_position():
    """Граничный случай: rate >= floor, даже старая позиция → нет exit."""
    should, reason = rotation.should_force_exit(
        current_rate=0.00010,  # ровно SMART_EXIT_FLOOR (strict <, не triggers)
        age_hours=100.0,
    )
    assert should is False
    assert reason == ""


def test_negative_funding_priority_over_low_apr():
    """Если current_rate < 0 — reason всегда 'negative_funding', не 'low_apr'.
    Даже если age >= min_hold."""
    should, reason = rotation.should_force_exit(
        current_rate=-0.00005,
        age_hours=100.0,
    )
    assert should is True
    assert reason == "negative_funding"  # NOT 'low_apr'


def test_custom_negative_threshold():
    """Кастомный negative_threshold перекрывает дефолт."""
    # rate=-0.001, threshold=-0.005 → rate НЕ ниже threshold → не exit
    should, reason = rotation.should_force_exit(
        current_rate=-0.001,
        age_hours=1.0,
        negative_threshold=-0.005,
    )
    assert should is False


def test_custom_floor_threshold():
    """Кастомный floor_threshold перекрывает дефолт."""
    # rate=0.0002, default floor=0.0001 → rate выше → нет exit
    # Но custom floor=0.0005 → rate ниже → exit (с min_hold)
    should, reason = rotation.should_force_exit(
        current_rate=0.0002,
        age_hours=25.0,
        floor_threshold=0.0005,
    )
    assert should is True
    assert reason == "low_apr"


def test_custom_min_hold():
    """Кастомный min_hold_h перекрывает дефолт."""
    # rate ниже floor, age=10ч < default 24ч → нет exit
    # Но custom min_hold=5 → age=10 >= 5 → exit
    should, reason = rotation.should_force_exit(
        current_rate=0.00005,
        age_hours=10.0,
        min_hold_h=5.0,
    )
    assert should is True
    assert reason == "low_apr"


def test_constants_match_documentation():
    """Sanity-check: константы совпадают с заявленными в Block 7 (D)."""
    assert rotation.SMART_EXIT_NEGATIVE_RATE == 0.0
    assert rotation.SMART_EXIT_FLOOR == 0.00010  # 11% APR
    assert rotation.SMART_EXIT_MIN_HOLD_H == 24.0


def test_negative_funding_ignores_min_hold_argument():
    """Даже если min_hold огромный, negative funding всё равно triggers."""
    should, reason = rotation.should_force_exit(
        current_rate=-0.001,
        age_hours=0.5,
        min_hold_h=999.0,
    )
    assert should is True
    assert reason == "negative_funding"


def test_floor_just_below_triggers():
    """Граничный случай: rate ровно на 1e-9 ниже floor → triggers."""
    should, reason = rotation.should_force_exit(
        current_rate=rotation.SMART_EXIT_FLOOR - 1e-9,
        age_hours=25.0,
    )
    assert should is True
    assert reason == "low_apr"


TESTS = [
    test_negative_funding_exits_immediately,
    test_negative_funding_exits_at_zero_age,
    test_zero_rate_does_not_exit_when_negative_threshold_is_zero,
    test_low_apr_with_old_position_exits,
    test_low_apr_with_young_position_does_not_exit,
    test_low_apr_at_exact_min_hold_exits,
    test_good_rate_does_not_exit,
    test_rate_above_floor_does_not_exit_old_position,
    test_negative_funding_priority_over_low_apr,
    test_custom_negative_threshold,
    test_custom_floor_threshold,
    test_custom_min_hold,
    test_constants_match_documentation,
    test_negative_funding_ignores_min_hold_argument,
    test_floor_just_below_triggers,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_smart_exit] {len(TESTS)} passed")
