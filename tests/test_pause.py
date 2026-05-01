"""tests/test_pause.py — pause_check unified API."""
from tests._bootstrap import cleanup_test_dir
import pause_check as pc


def test_initial_allowed():
    cleanup_test_dir()
    assert pc.can_enter(1)[0] is True
    assert pc.can_enter(2)[0] is True
    assert pc.can_act()[0] is True
    assert pc.is_safe_mode()[0] is False
    assert pc.is_paused_global()[0] is False


def test_pause_bot_isolates():
    cleanup_test_dir()
    pc.pause_bot(3, hours=1, reason="t3 trigger")
    assert pc.can_enter(3)[0] is False
    assert pc.can_enter(2)[0] is True
    assert pc.is_paused_bot(3)[0] is True
    assert pc.is_paused_bot(2)[0] is False


def test_pause_global_blocks_all_but_can_act():
    cleanup_test_dir()
    pc.pause_global(hours=2, reason="api outage")
    for n in (1, 2, 3, 4, 5, 6):
        assert pc.can_enter(n)[0] is False, f"bot{n} should be blocked"
    # can_act (для emergency rebalance) должен быть свободен
    assert pc.can_act()[0] is True


def test_safe_mode_blocks_everything():
    cleanup_test_dir()
    pc.enter_safe_mode("manual safe mode")
    assert pc.is_safe_mode()[0] is True
    for n in (1, 2):
        assert pc.can_enter(n)[0] is False
    assert pc.can_act()[0] is False  # safe_mode полностью замораживает


def test_resume_all_clears():
    cleanup_test_dir()
    pc.pause_global(hours=1, reason="x")
    pc.pause_bot(2, hours=1, reason="y")
    pc.enter_safe_mode("z")
    removed = pc.resume_all()
    assert "safe_mode" in removed
    assert "pause_global" in removed
    assert "pause_bot2" in removed
    # После resume — всё открыто
    assert pc.can_enter(2)[0] is True
    assert pc.is_safe_mode()[0] is False


def test_expired_pause_auto_removes():
    cleanup_test_dir()
    # Часы=0 → expires_at == now → следующий read должен авто-снять
    pc.pause_bot(4, hours=0, reason="instant expire")
    import time as _t; _t.sleep(0.05)
    allowed, _ = pc.can_enter(4)
    assert allowed is True, "expired pause must auto-remove"


def test_corrupted_pause_treated_active():
    """Битый JSON должен трактоваться как АКТИВНАЯ пауза (fail-safe)."""
    cleanup_test_dir()
    import os
    p = pc.pause_bot_path(5)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("{not valid json")
    # Fail-safe: в сомнении блокируем
    paused, _ = pc.is_paused_bot(5)
    assert paused is True, "corrupted file must fail-safe to ACTIVE"


TESTS = [
    test_initial_allowed,
    test_pause_bot_isolates,
    test_pause_global_blocks_all_but_can_act,
    test_safe_mode_blocks_everything,
    test_resume_all_clears,
    test_expired_pause_auto_removes,
    test_corrupted_pause_treated_active,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_pause] {len(TESTS)} passed")
