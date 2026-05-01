"""tests/test_rotation_logging.py — Block 5.x bugfixes regression.

Защищает от regression на:
- Bug #2: applied=True писалось в лог ДО execute_rotation
- Bug #4: anti-flap для fill_empty (не входим в тот же бот <30 мин)
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401

import os
import sys
import json
import tempfile
from datetime import datetime, timedelta, timezone


def _read_rotation_history_anti_flap_logic():
    """
    Мини-репликация anti-flap логики из rotation.py (без import самого
    модуля — там сетевые зависимости). Проверяем что детектор корректно
    отбраковывает ботов которые недавно делали fill_empty.
    """
    FILL_EMPTY_ANTIFLAP_MIN = 30

    def detect(history, now):
        cutoff = now - timedelta(minutes=FILL_EMPTY_ANTIFLAP_MIN)
        recent = set()
        for h in history[-30:]:
            d = h.get("decision") or {}
            if d.get("action") != "fill_empty" or not h.get("applied"):
                continue
            try:
                ts = datetime.fromisoformat(h["timestamp"].replace("Z", ""))
            except (ValueError, KeyError):
                continue
            if ts > cutoff:
                recent.add(d.get("eject_bot"))
        return recent

    return detect


def test_antiflap_blocks_recent_fillempty():
    detect = _read_rotation_history_anti_flap_logic()
    now = datetime(2026, 5, 1, 14, 30, 0)
    history = [
        {
            "timestamp": "2026-05-01T14:20:00",
            "decision": {"action": "fill_empty", "eject_bot": "arb_bot5"},
            "applied": True,
        },
    ]
    recent = detect(history, now)
    assert "arb_bot5" in recent, "Бот вошёл 10 мин назад → должен быть в anti-flap"


def test_antiflap_allows_old_fillempty():
    detect = _read_rotation_history_anti_flap_logic()
    now = datetime(2026, 5, 1, 14, 30, 0)
    history = [
        {
            "timestamp": "2026-05-01T13:30:00",  # 60 мин назад
            "decision": {"action": "fill_empty", "eject_bot": "arb_bot5"},
            "applied": True,
        },
    ]
    recent = detect(history, now)
    assert "arb_bot5" not in recent, "Бот вошёл >30 мин назад → ОК работать"


def test_antiflap_ignores_failed_attempts():
    """Если applied=False (не получилось войти) — это не считается."""
    detect = _read_rotation_history_anti_flap_logic()
    now = datetime(2026, 5, 1, 14, 30, 0)
    history = [
        {
            "timestamp": "2026-05-01T14:20:00",
            "decision": {"action": "fill_empty", "eject_bot": "arb_bot5"},
            "applied": False,
        },
    ]
    recent = detect(history, now)
    assert "arb_bot5" not in recent, "applied=False не блокирует ретрай"


def test_antiflap_ignores_rotate_action():
    """anti-flap только для fill_empty, не для обычных rotate."""
    detect = _read_rotation_history_anti_flap_logic()
    now = datetime(2026, 5, 1, 14, 30, 0)
    history = [
        {
            "timestamp": "2026-05-01T14:20:00",
            "decision": {"action": "rotate", "eject_bot": "arb_bot5"},
            "applied": True,
        },
    ]
    recent = detect(history, now)
    assert "arb_bot5" not in recent, "обычный rotate не блокируется anti-flap"


def test_antiflap_handles_corrupt_timestamps():
    """Битый timestamp — пропускаем, не падаем."""
    detect = _read_rotation_history_anti_flap_logic()
    now = datetime(2026, 5, 1, 14, 30, 0)
    history = [
        {"timestamp": "GARBAGE", "decision": {"action": "fill_empty", "eject_bot": "arb_bot5"}, "applied": True},
        {"decision": {"action": "fill_empty", "eject_bot": "arb_bot4"}, "applied": True},  # без ts
    ]
    recent = detect(history, now)
    # битые/неполные записи просто скипаются
    assert recent == set()


def test_antiflap_multi_bot():
    """Несколько ботов — каждый отдельно."""
    detect = _read_rotation_history_anti_flap_logic()
    now = datetime(2026, 5, 1, 14, 30, 0)
    history = [
        {"timestamp": "2026-05-01T14:25:00",
         "decision": {"action": "fill_empty", "eject_bot": "arb_bot5"}, "applied": True},
        {"timestamp": "2026-05-01T14:28:00",
         "decision": {"action": "fill_empty", "eject_bot": "arb_bot6"}, "applied": True},
        {"timestamp": "2026-05-01T13:00:00",  # старый
         "decision": {"action": "fill_empty", "eject_bot": "arb_bot4"}, "applied": True},
    ]
    recent = detect(history, now)
    assert recent == {"arb_bot5", "arb_bot6"}


def test_log_decision_applied_consistent_with_execute_rotation():
    """
    Smoke-test того, что в новом cmd_rotate_smart `applied` в history
    отражает РЕАЛЬНЫЙ результат execute_rotation, а не предсказание.

    Это компиляционный тест — реально вызывать execute_rotation мы не можем
    без BingX API, но можем проверить, что:
      - переменная rotation_ok собирается из execute_rotation возврата
      - really_applied считается из rotation_ok
      - _log_decision вызывается ПОСЛЕ execute_rotation
    """
    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(os.path.dirname(here), "rotation.py")).read()
    # порядок проверяем грубо — _log_decision должен идти после execute_rotation
    idx_exec = src.find("rotation_ok, lines = execute_rotation(")
    idx_log = src.find("_log_decision(result, applied=really_applied)")
    assert idx_exec > 0, "не найден вызов execute_rotation в rotation.py"
    assert idx_log > 0, "не найден вызов _log_decision с really_applied"
    assert idx_log > idx_exec, \
        "_log_decision должен вызываться ПОСЛЕ execute_rotation (Bug #2)"


TESTS = [
    test_antiflap_blocks_recent_fillempty,
    test_antiflap_allows_old_fillempty,
    test_antiflap_ignores_failed_attempts,
    test_antiflap_ignores_rotate_action,
    test_antiflap_handles_corrupt_timestamps,
    test_antiflap_multi_bot,
    test_log_decision_applied_consistent_with_execute_rotation,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_rotation_logging] {len(TESTS)} passed")
