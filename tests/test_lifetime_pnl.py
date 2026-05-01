"""tests/test_lifetime_pnl.py — lifetime_pnl module.

Проверяет:
  - record_exit накопляет earned по разным ботам независимо
  - record_exit увеличивает rotations_count
  - get_summary даёт правильный совокупный итог
  - get_bot_lifetime возвращает пустую заготовку если бот незнаком
  - reset обнуляет всё
  - битый JSON файл не валит модуль (fail-safe для cron)
  - история обрезается до HISTORY_LIMIT
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401

import os
import json
import tempfile

import lifetime_pnl


def _tmp_path():
    fd, path = tempfile.mkstemp(suffix=".json", prefix="lpnl_test_")
    os.close(fd)
    os.unlink(path)  # удаляем — пусть модуль создаст с нуля
    return path


def test_empty_summary_when_no_file():
    p = _tmp_path()
    s = lifetime_pnl.get_summary(p)
    assert s["total_earned_usdt"] == 0.0
    assert s["total_rotations"] == 0
    assert s["by_bot"] == {}


def test_record_exit_basic():
    p = _tmp_path()
    rec = lifetime_pnl.record_exit("arb_bot", "AIN-USDT", 0.67,
                                   cycles=12, reason="weak_apr", path=p)
    assert abs(rec["total_earned_usdt"] - 0.67) < 1e-6
    assert rec["rotations_count"] == 1
    assert len(rec["history"]) == 1
    assert rec["history"][0]["symbol"] == "AIN-USDT"
    assert rec["history"][0]["cycles"] == 12
    assert rec["history"][0]["reason"] == "weak_apr"
    os.unlink(p)


def test_record_exit_accumulates():
    p = _tmp_path()
    lifetime_pnl.record_exit("arb_bot", "AIN-USDT", 0.67, path=p)
    lifetime_pnl.record_exit("arb_bot", "FIGHTID-USDT", 0.30, path=p)
    rec = lifetime_pnl.get_bot_lifetime("arb_bot", path=p)
    assert abs(rec["total_earned_usdt"] - 0.97) < 1e-6
    assert rec["rotations_count"] == 2
    assert len(rec["history"]) == 2
    os.unlink(p)


def test_record_exit_isolates_bots():
    p = _tmp_path()
    lifetime_pnl.record_exit("arb_bot",  "AIN-USDT",  0.67, path=p)
    lifetime_pnl.record_exit("arb_bot2", "EVAA-USDT", 1.25, path=p)
    s = lifetime_pnl.get_summary(p)
    assert abs(s["total_earned_usdt"] - 1.92) < 1e-6
    assert s["total_rotations"] == 2
    assert "arb_bot"  in s["by_bot"]
    assert "arb_bot2" in s["by_bot"]
    assert abs(s["by_bot"]["arb_bot"]["total_earned_usdt"] - 0.67) < 1e-6
    assert abs(s["by_bot"]["arb_bot2"]["total_earned_usdt"] - 1.25) < 1e-6
    os.unlink(p)


def test_get_bot_lifetime_unknown_returns_empty():
    p = _tmp_path()
    rec = lifetime_pnl.get_bot_lifetime("arb_bot999", path=p)
    assert rec["total_earned_usdt"] == 0.0
    assert rec["rotations_count"] == 0
    assert rec["history"] == []


def test_reset():
    p = _tmp_path()
    lifetime_pnl.record_exit("arb_bot", "AIN-USDT", 0.67, path=p)
    assert lifetime_pnl.reset(p)
    s = lifetime_pnl.get_summary(p)
    assert s["total_earned_usdt"] == 0.0
    assert s["total_rotations"] == 0
    os.unlink(p)


def test_corrupted_file_fails_safe():
    """Битый JSON не должен валить cron — модуль возвращает пустое."""
    p = _tmp_path()
    # пишем мусор
    with open(p, "w") as f:
        f.write("{this is not valid json")
    s = lifetime_pnl.get_summary(p)
    # должно вернуть пустое, не упасть
    assert s["total_earned_usdt"] == 0.0
    assert s["by_bot"] == {}
    # И последующая запись должна работать (перезатрёт пустым state)
    rec = lifetime_pnl.record_exit("arb_bot", "X-USDT", 0.10, path=p)
    assert abs(rec["total_earned_usdt"] - 0.10) < 1e-6
    os.unlink(p)


def test_history_truncates_at_limit():
    """История > HISTORY_LIMIT обрезается, но total/rotations растут."""
    p = _tmp_path()
    limit = lifetime_pnl.HISTORY_LIMIT
    # запишем limit+5 закрытий
    for i in range(limit + 5):
        lifetime_pnl.record_exit("arb_bot", f"X{i}-USDT", 0.01, path=p)
    rec = lifetime_pnl.get_bot_lifetime("arb_bot", path=p)
    assert len(rec["history"]) == limit, f"история должна быть обрезана до {limit}"
    assert rec["rotations_count"] == limit + 5
    # последняя запись — самая свежая
    assert rec["history"][-1]["symbol"] == f"X{limit + 4}-USDT"
    os.unlink(p)


def test_zero_earned_still_records_rotation():
    """Закрытие пары с 0.00 earned — всё равно ротация (важно для FIGHTID/IDOL
    которые ушли быстро). Нужно, чтобы счётчик работал."""
    p = _tmp_path()
    lifetime_pnl.record_exit("arb_bot", "ZERO-USDT", 0.0, path=p)
    rec = lifetime_pnl.get_bot_lifetime("arb_bot", path=p)
    assert rec["total_earned_usdt"] == 0.0
    assert rec["rotations_count"] == 1
    assert len(rec["history"]) == 1
    os.unlink(p)


def test_atomic_write_creates_no_tmp_residue():
    """После успешной записи .tmp файла оставаться не должно."""
    p = _tmp_path()
    lifetime_pnl.record_exit("arb_bot", "A-USDT", 0.1, path=p)
    assert os.path.exists(p)
    assert not os.path.exists(p + ".tmp"), ".tmp residue после успешной записи"
    os.unlink(p)


def test_schema_persisted_correctly():
    """JSON на диске должен иметь version + bots + updated_at."""
    p = _tmp_path()
    lifetime_pnl.record_exit("arb_bot", "A-USDT", 0.5, path=p)
    with open(p) as f:
        data = json.load(f)
    assert data["version"] == lifetime_pnl.SCHEMA_VERSION
    assert "updated_at" in data
    assert "bots" in data
    assert "arb_bot" in data["bots"]
    os.unlink(p)


TESTS = [
    test_empty_summary_when_no_file,
    test_record_exit_basic,
    test_record_exit_accumulates,
    test_record_exit_isolates_bots,
    test_get_bot_lifetime_unknown_returns_empty,
    test_reset,
    test_corrupted_file_fails_safe,
    test_history_truncates_at_limit,
    test_zero_earned_still_records_rotation,
    test_atomic_write_creates_no_tmp_residue,
    test_schema_persisted_correctly,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_lifetime_pnl] {len(TESTS)} passed")
