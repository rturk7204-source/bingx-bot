"""tests/test_top_up.py — Block 8 (Часть 2): top_up.py.

Проверяет логику доливки:
  - is_eligible_for_topup: возраст, payments, bad, rate, cap, cooldown
  - select_topup_candidate: выбор лучшего кандидата
  - record_topup / load_recent_topups: round-trip
  - apply_topup: обновление state файла
"""
from tests._bootstrap import cleanup_test_dir, TEST_BOT_DIR  # noqa: F401

import os
import json
from datetime import datetime, timezone, timedelta

# top_up читает BOT_DIR из env при импорте — переопределяем перед import
os.environ["BOT_DIR"] = TEST_BOT_DIR
import top_up
top_up.BOT_DIR = TEST_BOT_DIR
top_up.TOP_UP_LOG_FILE = os.path.join(TEST_BOT_DIR, "top_up_log.json")


def _fresh():
    cleanup_test_dir()
    top_up.TOP_UP_LOG_FILE = os.path.join(TEST_BOT_DIR, "top_up_log.json")


def _state(age_hours=80, payments=20, bad=0, budget=100,
           position_open=True, symbol="ABC-USDT"):
    """Помощник: формирует state с возрастом N часов назад."""
    et = (datetime.now(timezone.utc) - timedelta(hours=age_hours))
    return {
        "position_open":     position_open,
        "entry_time":        et.strftime("%Y-%m-%d %H:%M UTC"),
        "spot_budget":       budget,
        "payments_received": payments,
        "bad_periods":       bad,
        "symbol":            symbol,
    }


# ── is_eligible_for_topup ─────────────────────────────────────────────────
def test_eligible_full_match():
    _fresh()
    s = _state(age_hours=80, payments=20, bad=0, budget=100)
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1400, recent_topups={},
        bot_name="arb_bot",
    )
    assert ok is True, reason
    assert reason == "ok"


def test_not_eligible_closed_position():
    _fresh()
    s = _state(position_open=False)
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1400, recent_topups={},
    )
    assert ok is False
    assert "not_open" in reason


def test_not_eligible_too_young():
    _fresh()
    s = _state(age_hours=24)  # < 72h
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1400, recent_topups={},
    )
    assert ok is False
    assert "too_young" in reason


def test_not_eligible_few_payments():
    _fresh()
    s = _state(payments=10)  # < 15
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1400, recent_topups={},
    )
    assert ok is False
    assert "few_payments" in reason


def test_not_eligible_has_bad_periods():
    _fresh()
    s = _state(bad=1)
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1400, recent_topups={},
    )
    assert ok is False
    assert "bad_periods" in reason


def test_not_eligible_low_rate():
    _fresh()
    s = _state()
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0003, total_capital=1400, recent_topups={},
    )
    assert ok is False
    assert "low_rate" in reason


def test_not_eligible_cap_exceeded():
    _fresh()
    # 25% cap of $1000 = $250. budget=$220 + $40 tranche = $260 > $250 → cap_exceeded
    s = _state(budget=220)
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1000, recent_topups={},
    )
    assert ok is False
    assert "cap_exceeded" in reason


def test_eligible_at_cap_boundary():
    """budget + tranche == cap (точно граница) → допустимо (не >)."""
    _fresh()
    # cap = $250, budget=$210 + $40 = $250 → ok
    s = _state(budget=210)
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1000, recent_topups={},
    )
    assert ok is True


def test_not_eligible_cooldown_active():
    _fresh()
    s = _state()
    recent = {"arb_bot": datetime.now(timezone.utc) - timedelta(hours=10)}
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1400,
        recent_topups=recent, bot_name="arb_bot",
    )
    assert ok is False
    assert "cooldown" in reason


def test_eligible_after_cooldown():
    _fresh()
    s = _state()
    recent = {"arb_bot": datetime.now(timezone.utc) - timedelta(hours=25)}
    ok, reason = top_up.is_eligible_for_topup(
        s, current_rate=0.0005, total_capital=1400,
        recent_topups=recent, bot_name="arb_bot",
    )
    assert ok is True


# ── select_topup_candidate ────────────────────────────────────────────────
def test_select_returns_none_when_no_eligible():
    _fresh()
    pos = [{"name": "arb_bot", "current_rate": 0.0001,
            "state": _state(payments=5)}]
    cand = top_up.select_topup_candidate(pos, total_capital=1400,
                                          free_spot_usd=100)
    assert cand is None


def test_select_returns_none_when_insufficient_spot():
    _fresh()
    pos = [{"name": "arb_bot", "current_rate": 0.0006,
            "state": _state()}]
    cand = top_up.select_topup_candidate(pos, total_capital=1400,
                                          free_spot_usd=20)  # < $40
    assert cand is None


def test_select_picks_highest_rate():
    _fresh()
    pos = [
        {"name": "arb_bot",  "current_rate": 0.00045, "state": _state(symbol="LOW")},
        {"name": "arb_bot2", "current_rate": 0.00080, "state": _state(symbol="HIGH")},
        {"name": "arb_bot3", "current_rate": 0.00060, "state": _state(symbol="MID")},
    ]
    cand = top_up.select_topup_candidate(pos, total_capital=1400,
                                          free_spot_usd=200)
    assert cand is not None
    assert cand["bot"] == "arb_bot2"
    assert cand["symbol"] == "HIGH"
    assert cand["new_budget"] == 100 + 40  # default budget + tranche


# ── record_topup / load_recent_topups round-trip ──────────────────────────
def test_record_and_load_roundtrip():
    _fresh()
    now = datetime.now(timezone.utc)
    top_up.record_topup("arb_bot",  "FOO-USDT", 40, 140, now=now)
    top_up.record_topup("arb_bot2", "BAR-USDT", 40, 140,
                         now=now + timedelta(hours=2))
    recent = top_up.load_recent_topups()
    assert "arb_bot" in recent
    assert "arb_bot2" in recent
    # latest для arb_bot2 — позже
    assert recent["arb_bot2"] > recent["arb_bot"]


def test_load_recent_topups_empty_when_no_log():
    _fresh()
    recent = top_up.load_recent_topups()
    assert recent == {}


# ── apply_topup ───────────────────────────────────────────────────────────
def test_apply_topup_updates_state():
    _fresh()
    state_file = os.path.join(TEST_BOT_DIR, "arb_state.json")
    s = _state(budget=100)
    with open(state_file, "w") as f:
        json.dump(s, f)
    ok, info = top_up.apply_topup("arb_bot", state_file, tranche_usd=40)
    assert ok is True
    assert info["old_budget"] == 100
    assert info["new_budget"] == 140
    # state перезаписан
    with open(state_file) as f:
        new_s = json.load(f)
    assert new_s["spot_budget"] == 140


def test_apply_topup_dry_run_does_not_write():
    _fresh()
    state_file = os.path.join(TEST_BOT_DIR, "arb_state.json")
    s = _state(budget=100)
    with open(state_file, "w") as f:
        json.dump(s, f)
    ok, info = top_up.apply_topup("arb_bot", state_file,
                                   tranche_usd=40, dry_run=True)
    assert ok is True
    assert info["new_budget"] == 140
    with open(state_file) as f:
        unchanged = json.load(f)
    assert unchanged["spot_budget"] == 100  # не изменилось


def test_apply_topup_fails_on_closed_position():
    _fresh()
    state_file = os.path.join(TEST_BOT_DIR, "arb_state.json")
    s = _state(position_open=False)
    with open(state_file, "w") as f:
        json.dump(s, f)
    ok, info = top_up.apply_topup("arb_bot", state_file)
    assert ok is False
    assert "not_open" in info.get("error", "")


def test_apply_topup_records_to_log():
    _fresh()
    state_file = os.path.join(TEST_BOT_DIR, "arb_state.json")
    s = _state(budget=100)
    with open(state_file, "w") as f:
        json.dump(s, f)
    top_up.apply_topup("arb_bot", state_file, tranche_usd=40)
    log = json.load(open(top_up.TOP_UP_LOG_FILE))
    assert len(log) == 1
    assert log[0]["bot"] == "arb_bot"
    assert log[0]["amount"] == 40
    assert log[0]["new_budget"] == 140


TESTS = [
    test_eligible_full_match,
    test_not_eligible_closed_position,
    test_not_eligible_too_young,
    test_not_eligible_few_payments,
    test_not_eligible_has_bad_periods,
    test_not_eligible_low_rate,
    test_not_eligible_cap_exceeded,
    test_eligible_at_cap_boundary,
    test_not_eligible_cooldown_active,
    test_eligible_after_cooldown,
    test_select_returns_none_when_no_eligible,
    test_select_returns_none_when_insufficient_spot,
    test_select_picks_highest_rate,
    test_record_and_load_roundtrip,
    test_load_recent_topups_empty_when_no_log,
    test_apply_topup_updates_state,
    test_apply_topup_dry_run_does_not_write,
    test_apply_topup_fails_on_closed_position,
    test_apply_topup_records_to_log,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_top_up] {len(TESTS)} passed")
