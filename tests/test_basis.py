"""tests/test_basis.py — basis (T4) и T5 drawdown calculator pure-логика.

Не вызываем сетевые функции hedge_health: проверяем sub-логику расчёта
basis% и dd% так, чтобы убедиться что фундаментальная математика корректна.
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401


def basis_pct(spot: float, perp: float) -> float:
    """То же что в check_T4_basis (без сетевого вызова)."""
    if spot <= 0 or perp <= 0:
        return 0.0
    return abs(spot - perp) / spot * 100


def dd_pct(working_base: float, unrealized: float) -> float:
    """То же что в check_T5_total_dd."""
    if working_base <= 0:
        return 0.0
    return abs(min(0, unrealized)) / working_base * 100


def test_basis_zero_when_equal():
    assert basis_pct(1.0, 1.0) == 0.0


def test_basis_positive_when_diverge():
    # 1% divergence
    assert abs(basis_pct(1.00, 1.01) - 1.0) < 1e-6
    # 2.5% divergence
    assert abs(basis_pct(100.0, 102.5) - 2.5) < 1e-6


def test_basis_symmetric():
    a = basis_pct(100.0, 102.0)
    b = basis_pct(100.0, 98.0)
    assert abs(a - b) < 1e-6, "basis должен быть симметричен (abs)"


def test_basis_invalid_inputs():
    assert basis_pct(0, 100) == 0.0
    assert basis_pct(100, 0) == 0.0
    assert basis_pct(-1, 100) == 0.0


def test_dd_no_loss_when_unrealized_positive():
    assert dd_pct(1000, +50) == 0.0
    assert dd_pct(1000, 0) == 0.0


def test_dd_basic():
    # $20 убыток на $1000 базе = 2%
    assert abs(dd_pct(1000, -20) - 2.0) < 1e-6


def test_dd_invalid_base():
    assert dd_pct(0, -100) == 0.0
    assert dd_pct(-100, -50) == 0.0


def test_dd_threshold_classification():
    """T5_dd_warn=2%, T5_dd_critical=4% (примерно). Мы убеждаемся что
    математика правильно различает <warn / warn / critical."""
    assert dd_pct(1000, -10) == 1.0     # < warn
    assert dd_pct(1000, -25) == 2.5     # warn зона
    assert dd_pct(1000, -50) == 5.0     # critical зона


TESTS = [
    test_basis_zero_when_equal,
    test_basis_positive_when_diverge,
    test_basis_symmetric,
    test_basis_invalid_inputs,
    test_dd_no_loss_when_unrealized_positive,
    test_dd_basic,
    test_dd_invalid_base,
    test_dd_threshold_classification,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_basis] {len(TESTS)} passed")
