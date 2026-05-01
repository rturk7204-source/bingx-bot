"""tests/test_auto_rebalance.py — Block 8.5: auto_rebalance.py.

Проверяет:
  - decide_rebalance: триггеры, граничные значения, safety floor
  - _load_recent_transfers / _record_transfer round-trip
  - apply_rebalance: dry_run, cooldown, успешный перевод, ошибки API
  - CLI smoke
"""
from tests._bootstrap import cleanup_test_dir, TEST_BOT_DIR  # noqa: F401

import sys
import types
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Стаб bingx_transfer ДО импорта auto_rebalance, иначе он пытается читать /root/bingx-bot/.env
if "bingx_transfer" not in sys.modules:
    _stub = types.ModuleType("bingx_transfer")
    _stub.transfer_usdt = lambda amount, direction, asset="USDT", dry_run=False: (True, "STUB")
    _stub.get_wallet_balances = lambda asset="USDT": {
        "spot": 0.0, "fund": 0.0, "perp_balance": 0.0,
        "perp_avail": 0.0, "perp_equity": 0.0,
    }
    sys.modules["bingx_transfer"] = _stub

# auto_balance также импортирует bingx_transfer и пишет в /root — стабнем
if "auto_balance" not in sys.modules:
    _ab = types.ModuleType("auto_balance")
    _ab.SAFETY_LIMITS = {"perp_min_avail": 30.0}
    sys.modules["auto_balance"] = _ab

# Перенаправляем LOG_FILE в TEST_BOT_DIR, чтобы тесты не писали в /root
TEST_LOG_FILE = Path(TEST_BOT_DIR) / "auto_rebalance_log.json"

import auto_rebalance
auto_rebalance.LOG_FILE = TEST_LOG_FILE


def _fresh():
    cleanup_test_dir()
    auto_rebalance.LOG_FILE = TEST_LOG_FILE
    if TEST_LOG_FILE.exists():
        TEST_LOG_FILE.unlink()


def _balances(spot=150.0, perp_avail=50.0, fund=0.0,
              perp_balance=None, perp_equity=None):
    return {
        "spot": float(spot),
        "fund": float(fund),
        "perp_balance": float(perp_balance if perp_balance is not None else perp_avail),
        "perp_avail": float(perp_avail),
        "perp_equity": float(perp_equity if perp_equity is not None else perp_avail),
    }


# ============ decide_rebalance ============

def test_decide_balanced_noop():
    """Балансы в норме → noop."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=150, perp_avail=50))
    assert d["action"] == "noop", d
    assert d["amount"] == 0.0


def test_decide_spot_low_triggers_perp_to_spot():
    """spot=50 (< 100) И perp=200 → перевести 100 на spot (до target 150)."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=50, perp_avail=200))
    assert d["action"] == "perp_to_spot", d
    # Нужно: SPOT_TARGET(150) - 50 = 100; perp может дать 200-30=170 → берем 100
    assert d["amount"] == 100.0, d


def test_decide_spot_low_perp_safety_respected():
    """spot=10, perp=40 (только $10 над safety) → переводим только $10."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=10, perp_avail=40))
    assert d["action"] == "perp_to_spot", d
    # max_from_perp = 40 - 30 = 10; needed = 150-10 = 140 → берем min = 10
    assert d["amount"] == 10.0, d


def test_decide_spot_low_perp_below_safety_noop():
    """spot=10, perp=25 (< safety floor 30) → noop (нечего переводить)."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=10, perp_avail=25))
    assert d["action"] == "noop", d
    assert "MIN_TRANSFER" in d["reason"] or "оставляет" in d["reason"], d["reason"]


def test_decide_spot_at_threshold_noop():
    """spot ровно 100 (= threshold) → noop."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=100, perp_avail=200))
    assert d["action"] == "noop", d


def test_decide_spot_just_below_threshold_triggers():
    """spot=99 (< 100) → должен сработать."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=99, perp_avail=200))
    assert d["action"] == "perp_to_spot", d
    assert d["amount"] == 51.0, d  # 150-99


def test_decide_perp_overflow_triggers_spot_offload():
    """perp_avail=300 (> 30+100=130) И spot=200 (>= target) → overflow → spot."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=200, perp_avail=300))
    assert d["action"] == "perp_to_spot", d
    # excess = 300 - 30 - 100 = 170
    assert d["amount"] == 170.0, d


def test_decide_perp_overflow_but_spot_below_target_priority_to_spot():
    """perp=300 И spot=120 (< target 150 но > threshold 100) → noop (spot не <100)."""
    _fresh()
    # spot=120 → не триггерит первый бранч (>=100), и не >= target (150) → noop
    d = auto_rebalance.decide_rebalance(_balances(spot=120, perp_avail=300))
    assert d["action"] == "noop", d


def test_decide_perp_high_but_spot_low_takes_perp_to_spot():
    """spot=50 (<100), perp=300 → берём первый бранч (spot низкий), не overflow."""
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=50, perp_avail=300))
    assert d["action"] == "perp_to_spot", d
    assert d["amount"] == 100.0  # SPOT_TARGET - 50


def test_decide_min_transfer_floor():
    """spot=147 (нужно 3$ до target) → < MIN_TRANSFER_USD=5 → noop."""
    _fresh()
    # spot 99 чтобы триггернулся первый бранч; needed=51; perp=33 (avail=3 над safety) → transfer 3 → noop
    d = auto_rebalance.decide_rebalance(_balances(spot=99, perp_avail=33))
    assert d["action"] == "noop", d
    assert "MIN_TRANSFER" in d["reason"] or "MIN_TRANSFER" in d["reason"], d["reason"]


def test_decide_real_world_scenario_may1():
    """
    Реальный сценарий 1 мая 2026: spot=$93, perp_free=$172.
    Должен триггериться: spot<100, перевести (150-93)=57 на спот.
    """
    _fresh()
    d = auto_rebalance.decide_rebalance(_balances(spot=93, perp_avail=172))
    assert d["action"] == "perp_to_spot", d
    assert d["amount"] == 57.0, d


# ============ _load_recent_transfers / _record_transfer ============

def test_record_and_load_roundtrip():
    _fresh()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "decision": {"action": "perp_to_spot", "amount": 50.0},
        "success": True,
    }
    auto_rebalance._record_transfer(entry)
    recent = auto_rebalance._load_recent_transfers(window_min=60)
    assert len(recent) == 1
    assert recent[0]["decision"]["amount"] == 50.0


def test_load_recent_filters_by_window():
    _fresh()
    old = {
        "ts": (datetime.now(timezone.utc) - timedelta(minutes=120)).isoformat(),
        "success": True,
    }
    new = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "success": True,
    }
    auto_rebalance._record_transfer(old)
    auto_rebalance._record_transfer(new)
    recent = auto_rebalance._load_recent_transfers(window_min=30)
    assert len(recent) == 1, recent
    # Должен быть именно new (свежий)
    assert recent[0]["ts"] == new["ts"]


def test_load_recent_handles_missing_file():
    _fresh()
    # Файла нет — должен вернуть [].
    assert TEST_LOG_FILE.exists() is False
    assert auto_rebalance._load_recent_transfers() == []


def test_load_recent_handles_corrupt_file():
    _fresh()
    TEST_LOG_FILE.write_text("not valid json{{{")
    assert auto_rebalance._load_recent_transfers() == []


# ============ apply_rebalance ============

def test_apply_dry_run_does_not_record():
    """dry_run=True не должен писать в LOG_FILE (cooldown не блокирует следующий запуск)."""
    _fresh()
    # Заглушаем get_wallet_balances
    auto_rebalance.get_wallet_balances = lambda asset="USDT": _balances(spot=50, perp_avail=200)
    result = auto_rebalance.apply_rebalance(dry_run=True)
    assert result["decision"]["action"] == "perp_to_spot"
    assert result["executed"] is False
    assert result["tran_id"] == "DRY_RUN"
    # LOG_FILE не должен был быть создан
    recent = auto_rebalance._load_recent_transfers()
    assert recent == [], f"dry_run should not record, got {recent}"


def test_apply_noop_records_but_no_transfer():
    """Когда decision=noop, всё равно записываем для аудита, но не вызываем transfer."""
    _fresh()
    auto_rebalance.get_wallet_balances = lambda asset="USDT": _balances(spot=150, perp_avail=50)

    transfer_calls = []
    def fake_transfer(*args, **kwargs):
        transfer_calls.append((args, kwargs))
        return True, "TX-FAKE"
    auto_rebalance.transfer_usdt = fake_transfer

    result = auto_rebalance.apply_rebalance(dry_run=False)
    assert result["decision"]["action"] == "noop"
    assert result["executed"] is False
    assert transfer_calls == [], "transfer_usdt should NOT be called on noop"


def test_apply_successful_transfer():
    """Реальный перевод: get_balances → decide → transfer → verify."""
    _fresh()
    call_count = {"n": 0}
    def fake_balances(asset="USDT"):
        call_count["n"] += 1
        # Первый вызов: spot низкий; второй (post-transfer) — поправленный
        if call_count["n"] == 1:
            return _balances(spot=50, perp_avail=200)
        return _balances(spot=150, perp_avail=100)
    auto_rebalance.get_wallet_balances = fake_balances

    transfer_calls = []
    def fake_transfer(amount, direction, asset="USDT", dry_run=False):
        transfer_calls.append({"amount": amount, "direction": direction})
        return True, "TX-12345"
    auto_rebalance.transfer_usdt = fake_transfer

    result = auto_rebalance.apply_rebalance(dry_run=False)
    assert result["executed"] is True, result
    assert result["success"] is True
    assert result["tran_id"] == "TX-12345"
    assert result["decision"]["action"] == "perp_to_spot"
    assert len(transfer_calls) == 1
    assert transfer_calls[0]["amount"] == 100.0
    assert transfer_calls[0]["direction"] == "perp_to_spot"


def test_apply_cooldown_blocks_second_run():
    """После успешного transfer следующий вызов в течение COOLDOWN → noop."""
    _fresh()
    # Записываем недавний успешный transfer
    auto_rebalance._record_transfer({
        "ts": datetime.now(timezone.utc).isoformat(),
        "decision": {"action": "perp_to_spot", "amount": 100.0},
        "success": True,
        "tran_id": "TX-PREV",
    })

    transfer_calls = []
    def fake_transfer(*args, **kwargs):
        transfer_calls.append(args)
        return True, "TX-NEW"
    auto_rebalance.transfer_usdt = fake_transfer
    auto_rebalance.get_wallet_balances = lambda asset="USDT": _balances(spot=50, perp_avail=200)

    result = auto_rebalance.apply_rebalance(dry_run=False)
    assert result["executed"] is False
    assert "cooldown" in result["decision"]["reason"].lower()
    assert transfer_calls == [], "transfer must NOT fire during cooldown"


def test_apply_handles_balance_fetch_failure():
    """Если get_wallet_balances падает — apply возвращает success=False, не падает."""
    _fresh()
    def fake_balances(asset="USDT"):
        raise RuntimeError("API down")
    auto_rebalance.get_wallet_balances = fake_balances

    result = auto_rebalance.apply_rebalance(dry_run=False)
    assert result["success"] is False
    assert "API down" in (result.get("error") or "")
    assert result["executed"] is False


def test_apply_handles_transfer_failure():
    """Если transfer_usdt вернул (False, error) — записать как fail."""
    _fresh()
    auto_rebalance.get_wallet_balances = lambda asset="USDT": _balances(spot=50, perp_avail=200)
    def fake_transfer(amount, direction, asset="USDT", dry_run=False):
        return False, "INSUFFICIENT_BALANCE"
    auto_rebalance.transfer_usdt = fake_transfer

    result = auto_rebalance.apply_rebalance(dry_run=False)
    assert result["executed"] is True
    assert result["success"] is False
    assert "INSUFFICIENT_BALANCE" in (result.get("error") or "")


def test_apply_handles_transfer_exception():
    """Если transfer_usdt бросил exception — apply поймает, запишет fail."""
    _fresh()
    auto_rebalance.get_wallet_balances = lambda asset="USDT": _balances(spot=50, perp_avail=200)
    def fake_transfer(amount, direction, asset="USDT", dry_run=False):
        raise ConnectionError("network died")
    auto_rebalance.transfer_usdt = fake_transfer

    result = auto_rebalance.apply_rebalance(dry_run=False)
    assert result["executed"] is True
    assert result["success"] is False
    assert "network died" in (result.get("error") or "")


def test_apply_failed_transfer_does_not_block_retry():
    """
    Cooldown срабатывает только на УСПЕШНЫХ transfer.
    Неудачный → следующий вызов не должен быть заблокирован cooldown.
    """
    _fresh()
    auto_rebalance.get_wallet_balances = lambda asset="USDT": _balances(spot=50, perp_avail=200)

    # Первый вызов — fail
    def fake_fail(amount, direction, asset="USDT", dry_run=False):
        return False, "TEMP_ERROR"
    auto_rebalance.transfer_usdt = fake_fail
    r1 = auto_rebalance.apply_rebalance(dry_run=False)
    assert r1["success"] is False

    # Второй вызов — успех; cooldown не должен блокировать
    def fake_ok(amount, direction, asset="USDT", dry_run=False):
        return True, "TX-OK"
    auto_rebalance.transfer_usdt = fake_ok
    r2 = auto_rebalance.apply_rebalance(dry_run=False)
    assert r2["executed"] is True, r2
    assert r2["success"] is True
    assert r2["tran_id"] == "TX-OK"


TESTS = [
    test_decide_balanced_noop,
    test_decide_spot_low_triggers_perp_to_spot,
    test_decide_spot_low_perp_safety_respected,
    test_decide_spot_low_perp_below_safety_noop,
    test_decide_spot_at_threshold_noop,
    test_decide_spot_just_below_threshold_triggers,
    test_decide_perp_overflow_triggers_spot_offload,
    test_decide_perp_overflow_but_spot_below_target_priority_to_spot,
    test_decide_perp_high_but_spot_low_takes_perp_to_spot,
    test_decide_min_transfer_floor,
    test_decide_real_world_scenario_may1,
    test_record_and_load_roundtrip,
    test_load_recent_filters_by_window,
    test_load_recent_handles_missing_file,
    test_load_recent_handles_corrupt_file,
    test_apply_dry_run_does_not_record,
    test_apply_noop_records_but_no_transfer,
    test_apply_successful_transfer,
    test_apply_cooldown_blocks_second_run,
    test_apply_handles_balance_fetch_failure,
    test_apply_handles_transfer_failure,
    test_apply_handles_transfer_exception,
    test_apply_failed_transfer_does_not_block_retry,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_auto_rebalance] {len(TESTS)} passed")
