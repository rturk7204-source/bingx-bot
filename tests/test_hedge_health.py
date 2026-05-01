"""tests/test_hedge_health.py — Block 2 capital-protection триггеры.

Тестируем чистые функции: check_T1, check_T5, check_T6 (state-only),
которые не требуют сетевых вызовов.
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401
import time
import hedge_health as hh


def test_T1_critical_when_close_to_liq():
    # T1 thresholds: warn=20%, critical=10%
    state = {"symbol": "TEST-USDT"}
    perp_pos = {"liquidationPrice": "105.0", "positionAmt": "-1.0"}
    mark = 100.0  # 5% distance — ниже critical=10%
    level, _ = hh.check_T1_liq_distance(1, state, perp_pos, mark)
    assert level == "CRITICAL", f"5% liq distance must be CRITICAL: {level}"


def test_T1_warn_at_intermediate():
    state = {"symbol": "TEST-USDT"}
    perp_pos = {"liquidationPrice": "115.0", "positionAmt": "-1.0"}
    mark = 100.0  # 15%: ниже warn=20%, но выше critical=10% → WARN
    level, _ = hh.check_T1_liq_distance(1, state, perp_pos, mark)
    assert level == "WARN", f"15% liq distance must be WARN: {level}"


def test_T1_ok_when_far():
    state = {"symbol": "TEST-USDT"}
    perp_pos = {"liquidationPrice": "130.0", "positionAmt": "-1.0"}
    mark = 100.0  # 30% — выше warn=20% → OK
    level, _ = hh.check_T1_liq_distance(1, state, perp_pos, mark)
    assert level is None, f"30% liq distance must be OK: {level}"


def test_T1_no_pos_skips():
    level, _ = hh.check_T1_liq_distance(1, {}, None, 100)
    assert level is None


def test_T5_no_dd_when_positive():
    level, _ = hh.check_T5_total_dd(1000.0, +50.0)
    assert level is None


def test_T5_critical_when_large_loss():
    # threshold T5_dd_critical_pct=4 (или похоже)
    crit_threshold = hh.THRESHOLDS["T5_dd_critical_pct"]
    loss = -(crit_threshold + 1) * 1000 / 100  # на 1% выше критического
    level, _ = hh.check_T5_total_dd(1000.0, loss)
    assert level == "CRITICAL", f"loss {loss} on $1000 base must trigger CRITICAL: {level}"


def test_T5_no_base_skips():
    level, _ = hh.check_T5_total_dd(0, -100)
    assert level is None


def test_T6_no_failures_ok():
    state = {"api_failures": []}
    level, _ = hh.check_T6_api_outage(state)
    assert level is None


def test_T6_critical_when_majority_fail():
    """Симулируем 5 запросов за 5 минут, 4 из них провальных."""
    now = time.time()
    state = {"api_failures": [
        {"ts": now - 30, "ok": False},
        {"ts": now - 60, "ok": False},
        {"ts": now - 90, "ok": False},
        {"ts": now - 120, "ok": False},
        {"ts": now - 150, "ok": True},
    ]}
    level, _ = hh.check_T6_api_outage(state)
    assert level in ("WARN", "CRITICAL"), f"4/5 failures must trigger: {level}"


def test_can_act_blocks_after_max_actions():
    """can_act проверяет rate-limits, не safe_mode (это разные слои).
    safe_mode проверяется отдельно через pause_check.is_safe_mode/can_act.
    Здесь проверяем rate-limit guard hedge_health.can_act."""
    import time as _t
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    # Имитируем что было очень много действий сегодня → global_kill
    state = {"actions_today": [
        {"date": today, "ts": _t.time() - 9999, "action": "pause"}
        for _ in range(hh.THRESHOLDS["guard_global_kill_actions"] + 1)
    ]}
    ok, reason = hh.can_act(state, "pause")
    assert ok is False, f"global_kill must block can_act: {ok} / {reason}"
    assert "global_kill" in reason or "max" in reason, f"unexpected reason: {reason}"


TESTS = [
    test_T1_critical_when_close_to_liq,
    test_T1_warn_at_intermediate,
    test_T1_ok_when_far,
    test_T1_no_pos_skips,
    test_T5_no_dd_when_positive,
    test_T5_critical_when_large_loss,
    test_T5_no_base_skips,
    test_T6_no_failures_ok,
    test_T6_critical_when_majority_fail,
    test_can_act_blocks_after_max_actions,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_hedge_health] {len(TESTS)} passed")
