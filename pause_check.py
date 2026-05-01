#!/usr/bin/env python3
"""
pause_check.py — single source of truth for pause/safe-mode state.

Block 4: unified API. Всех читателей и писателей переводим сюда.
До Block 4 логика дублировалась в rotation.py, hedge_health.py, arb_tools.py.

Иерархия (используется в can_enter):
  1. safe_mode    → блок ВСЕГО (не enter, не rotation, requires manual --resume)
  2. pause_global → блок только новых entries (TTL)
  3. pause_botN   → блок entries для конкретного бота (TTL)

Все TTL-паузы хранятся как JSON: {"until": ISO, "reason": str, ...}.
Просроченные паузы автоматически удаляются при чтении.
safe_mode не имеет TTL — снимается только cmd_resume.

Public API:
  • Reads:   is_safe_mode(), is_paused_global(), is_paused_bot(n),
             can_enter(n), can_act(), get_pause_info(path)
  • Writes:  pause_global(hours, reason), pause_bot(n, hours, reason),
             enter_safe_mode(reason), clear_safe_mode(), resume_all()
  • Paths:   safe_mode_path(), pause_global_path(), pause_bot_path(n)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple

# ─── Constants & paths ──────────────────────────────────────────────────────

BOT_DIR = os.getenv("BOT_DIR", "/root/bingx-bot")
STATE_DIR = f"{BOT_DIR}/state"
SAFE_MODE_FILE = f"{STATE_DIR}/safe_mode"
PAUSE_GLOBAL = f"{STATE_DIR}/pause_global"
PAUSE_BOT_FMT = f"{STATE_DIR}/pause_bot{{}}"
N_BOTS = 6


def _bot_dir() -> str:
    """Live override — picks up BOT_DIR env at call time (used in tests)."""
    return os.getenv("BOT_DIR", BOT_DIR)


def _state_dir() -> str:
    return f"{_bot_dir()}/state"


def safe_mode_path() -> str:
    return f"{_state_dir()}/safe_mode"


def pause_global_path() -> str:
    return f"{_state_dir()}/pause_global"


def pause_bot_path(n: int) -> str:
    return f"{_state_dir()}/pause_bot{n}"


def _ensure_state_dir() -> None:
    os.makedirs(_state_dir(), exist_ok=True)


# ─── Read helpers ───────────────────────────────────────────────────────────


def _parse_until(raw: dict) -> datetime | None:
    """Parse 'until' field. Returns datetime or None on missing/invalid."""
    until_str = raw.get("until")
    if not until_str:
        return None
    try:
        return datetime.fromisoformat(str(until_str).replace("Z", "+00:00"))
    except Exception:
        return None


def get_pause_info(path: str) -> dict | None:
    """
    Read pause file. Returns dict with {until, reason, hours, ...} if active.
    Returns None if file missing OR pause expired (and removes expired file).
    On corrupted file: returns dict with reason='corrupted' (treat as paused).
    """
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            raw = json.load(f)
    except Exception as e:
        # Corrupted — treat as active pause to be safe
        return {"until": None, "reason": f"corrupted: {e}", "corrupted": True}

    until = _parse_until(raw)
    if until is not None and datetime.now(timezone.utc) >= until:
        # Expired — remove
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    info = dict(raw)
    if until is not None:
        info["until_dt"] = until
    return info


def is_safe_mode() -> Tuple[bool, str]:
    """
    Returns (active, reason). Safe-mode НЕ имеет TTL.
    Compatible with old callers that did `if is_safe_mode():` because
    a non-empty tuple is always truthy. New callers should unpack.
    """
    path = safe_mode_path()
    if not os.path.exists(path):
        return False, ""
    try:
        with open(path) as f:
            info = json.load(f)
        return True, info.get("reason", "no reason")
    except Exception as e:
        return True, f"safe-mode file corrupted: {e}"


def is_paused_global() -> Tuple[bool, str]:
    """Returns (active, reason). Auto-clears expired."""
    info = get_pause_info(pause_global_path())
    if info is None:
        return False, ""
    return True, info.get("reason", "no reason")


def is_paused_bot(n: int) -> Tuple[bool, str]:
    """Returns (active, reason) for bot-specific pause."""
    info = get_pause_info(pause_bot_path(n))
    if info is None:
        return False, ""
    return True, info.get("reason", "no reason")


def can_enter(bot_n: int) -> Tuple[bool, str]:
    """
    Главный guard для cmd_enter() и rotation pre-flight.
    Returns (allowed, reason).
    """
    safe, reason = is_safe_mode()
    if safe:
        return False, f"SAFE-MODE: {reason}"

    paused, reason = is_paused_global()
    if paused:
        return False, f"GLOBAL PAUSE: {reason}"

    paused, reason = is_paused_bot(bot_n)
    if paused:
        return False, f"BOT{bot_n} PAUSE: {reason}"

    return True, ""


def can_act() -> Tuple[bool, str]:
    """
    Для rotation и других глобальных действий (не входы): только safe-mode блокирует.
    """
    safe, reason = is_safe_mode()
    if safe:
        return False, f"SAFE-MODE: {reason}"
    return True, ""


# ─── Write helpers ──────────────────────────────────────────────────────────


def pause_global(hours: float = 4, reason: str = "manual") -> dict:
    """Create/refresh pause_global file. Returns the written payload."""
    _ensure_state_dir()
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {
        "until": until.isoformat(),
        "reason": reason,
        "hours": hours,
        "scope": "new_entries",
        "set_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(pause_global_path(), "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def pause_bot(n: int, hours: float = 4, reason: str = "manual") -> dict:
    """Create/refresh pause_botN file. Returns the written payload."""
    _ensure_state_dir()
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {
        "until": until.isoformat(),
        "reason": reason,
        "hours": hours,
        "bot": n,
        "scope": "new_entries",
        "set_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(pause_bot_path(n), "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def enter_safe_mode(reason: str) -> dict:
    """Trip safe-mode. Persists until clear_safe_mode() / resume_all()."""
    _ensure_state_dir()
    payload = {
        "reason": reason,
        "entered_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(safe_mode_path(), "w") as f:
        json.dump(payload, f, indent=2)
    return payload


def clear_safe_mode() -> bool:
    """Remove safe_mode file. Returns True if was active."""
    path = safe_mode_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def clear_pause_global() -> bool:
    path = pause_global_path()
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def clear_pause_bot(n: int) -> bool:
    path = pause_bot_path(n)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def resume_all() -> list[str]:
    """
    Clear safe-mode + pause_global + all pause_botN files.
    Returns list of removed file labels (for log/notification).
    """
    removed = []
    if clear_safe_mode():
        removed.append("safe_mode")
    if clear_pause_global():
        removed.append("pause_global")
    for n in range(1, N_BOTS + 1):
        if clear_pause_bot(n):
            removed.append(f"pause_bot{n}")
    return removed


# ─── CLI / debug ────────────────────────────────────────────────────────────


def _cli_status() -> None:
    """Print current pause state (used by `python3 pause_check.py status`)."""
    safe, r = is_safe_mode()
    print(f"safe_mode: {'ACTIVE' if safe else 'clear'} ({r or '-'})")
    pg, r = is_paused_global()
    print(f"pause_global: {'ACTIVE' if pg else 'clear'} ({r or '-'})")
    for n in range(1, N_BOTS + 1):
        ok, reason = can_enter(n)
        flag = "BLOCKED" if not ok else "ok"
        print(f"bot{n}: {flag} ({reason or '-'})")


# ─── Self-test ──────────────────────────────────────────────────────────────


def _selftest() -> None:
    import tempfile, shutil
    tmp = tempfile.mkdtemp(prefix="pause_t_")
    try:
        os.environ["BOT_DIR"] = tmp
        os.makedirs(f"{tmp}/state")

        # 1. Initially: nothing active, all bots can enter
        assert is_safe_mode() == (False, "")
        for n in range(1, N_BOTS + 1):
            ok, _ = can_enter(n)
            assert ok, f"bot{n} should be allowed"
        print("✓ initial state: all bots allowed")

        # 2. Pause bot3 for 2h → bot3 blocked, others ok
        pause_bot(3, hours=2, reason="test")
        ok, r = can_enter(3)
        assert not ok and "BOT3 PAUSE" in r, f"bot3 should be blocked: {r}"
        ok, _ = can_enter(2)
        assert ok, "bot2 should still be allowed"
        print("✓ pause_bot(3): bot3 blocked, bot2 ok")

        # 3. Global pause → all bots blocked, but can_act ok
        pause_global(hours=1, reason="global-test")
        for n in range(1, N_BOTS + 1):
            ok, r = can_enter(n)
            assert not ok and "GLOBAL PAUSE" in r, f"bot{n} expected GLOBAL PAUSE, got: {r}"
        ok, _ = can_act()
        assert ok, "can_act should be true (only safe_mode blocks rotation)"
        print("✓ pause_global: all bots blocked, can_act ok")

        # 4. Safe-mode → blocks even rotation
        enter_safe_mode("test-trip")
        for n in range(1, N_BOTS + 1):
            ok, r = can_enter(n)
            assert not ok and "SAFE-MODE" in r, f"safe-mode priority broken: {r}"
        ok, r = can_act()
        assert not ok and "SAFE-MODE" in r, "can_act should block under safe-mode"
        print("✓ safe_mode: blocks everything, including rotation")

        # 5. resume_all → everything clear
        removed = resume_all()
        assert "safe_mode" in removed
        assert "pause_global" in removed
        assert "pause_bot3" in removed
        for n in range(1, N_BOTS + 1):
            ok, _ = can_enter(n)
            assert ok, f"bot{n} should be allowed after resume_all"
        print(f"✓ resume_all cleared: {removed}")

        # 6. Expired pause auto-removes on read
        pause_bot(4, hours=1, reason="expire-me")
        # Manually rewrite with past 'until'
        with open(pause_bot_path(4), "w") as f:
            json.dump({
                "until": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                "reason": "expired",
            }, f)
        ok, _ = can_enter(4)
        assert ok, "expired pause should be auto-cleared"
        assert not os.path.exists(pause_bot_path(4)), "expired file should be deleted"
        print("✓ expired pause auto-removed")

        # 7. Corrupted pause file → treat as active (fail-safe)
        with open(pause_bot_path(5), "w") as f:
            f.write("{not json")
        ok, r = can_enter(5)
        assert not ok, "corrupted pause should block"
        assert "corrupted" in r.lower(), f"reason should mention corrupted: {r}"
        print("✓ corrupted pause treated as active (fail-safe)")

        # Cleanup last corrupted file (resume_all skips invalid JSON files but removes)
        os.remove(pause_bot_path(5))
        print("\n[PAUSE] all self-tests passed ✓")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        os.environ.pop("BOT_DIR", None)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        _cli_status()
    elif len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
    else:
        # Backward compat: legacy pause_check.py self-test ran by default
        _selftest()
