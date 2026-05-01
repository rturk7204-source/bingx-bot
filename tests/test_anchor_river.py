"""tests/test_anchor_river.py — Block 8.7: RIVER-USDT возвращён якорем.

History:
- До 29.04: RIVER был якорем bot1 (приносил больше всех)
- 29.04: ушёл в ~10% APY → ANCHOR_BOTS обнулён, RIVER ротирует как клон
- 01.05: восстановился до ~76% APR → возвращаем якорем

Тест защищает конфигурацию от случайного обнуления.
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401
import rotation


def test_river_is_anchor():
    assert "RIVER-USDT" in rotation.ANCHOR_BOTS, \
        "RIVER-USDT должен быть в ANCHOR_BOTS (Block 8.7)"


def test_only_river_is_anchor():
    """Только RIVER — других якорей нет (стратегия один якорь + клоны)."""
    assert rotation.ANCHOR_BOTS == {"RIVER-USDT"}


def test_anchor_bots_is_set_type():
    """ANCHOR_BOTS должен быть set для O(1) lookup."""
    assert isinstance(rotation.ANCHOR_BOTS, set)
