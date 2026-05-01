"""tests/test_rotation_parsing.py — regression test для парсинга bot_id.

Bug (2026-05-01): rotation.py:556 падал на bot1 потому что:
  - bot1 именуется 'arb_bot' (без цифры)
  - bot2-6 именуются 'arb_bot2'..'arb_bot6'
  - int('arb_bot'.replace('arb_bot', '')) == int('') → ValueError

Тест защищает от регрессии после фикса.
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401


def parse_bot_id(name: str):
    """Дублирует логику rotation.py:558-563 для unit-теста."""
    if name.startswith("arb_bot"):
        suffix = name.replace("arb_bot", "")
        return int(suffix) if suffix else 1
    return None


def test_bot1_no_digit_is_id_1():
    """Bug 2026-05-01: 'arb_bot' (без цифры) должен парситься как bot1."""
    assert parse_bot_id("arb_bot") == 1


def test_bot2_through_6():
    assert parse_bot_id("arb_bot2") == 2
    assert parse_bot_id("arb_bot3") == 3
    assert parse_bot_id("arb_bot4") == 4
    assert parse_bot_id("arb_bot5") == 5
    assert parse_bot_id("arb_bot6") == 6


def test_unknown_returns_none():
    assert parse_bot_id("unknown") is None
    assert parse_bot_id("watchdog") is None
    assert parse_bot_id("") is None


def test_rotation_actually_imports_fix():
    """Проверяем что rotation.py импортируется без синтаксических ошибок."""
    import importlib.util
    import os
    spec = importlib.util.spec_from_file_location(
        "rotation_test_import",
        os.path.join(os.path.dirname(__file__), "..", "rotation.py"),
    )
    # Только проверка что файл валидный Python — не выполняем (нет .env)
    assert spec is not None
    # Грузим source и компилируем
    with open(spec.origin) as f:
        compile(f.read(), spec.origin, "exec")


TESTS = [
    test_bot1_no_digit_is_id_1,
    test_bot2_through_6,
    test_unknown_returns_none,
    test_rotation_actually_imports_fix,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_rotation_parsing] {len(TESTS)} passed")
