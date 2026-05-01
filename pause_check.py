#!/usr/bin/env python3
"""
Block 2: общий pause-guard, импортируется в начале cmd_enter() всех ботов.
Возвращает (blocked: bool, reason: str).

Иерархия проверок:
  1. safe_mode    → блок ВСЕГО (не enter, не exit, не rotation)
  2. pause_global → блок только новых entries
  3. pause_botN   → блок entries для конкретного бота

Срок паузы (until ISO) уважается. Просроченные паузы автоматически удаляются.
"""

import os, json
from datetime import datetime, timezone

BOT_DIR = "/root/bingx-bot"
STATE_DIR = f"{BOT_DIR}/state"
SAFE_MODE_FILE = f"{STATE_DIR}/safe_mode"
PAUSE_GLOBAL = f"{STATE_DIR}/pause_global"


def _check_pause_file(path):
    """Returns (active: bool, reason: str). Удаляет файл если истёк."""
    if not os.path.exists(path):
        return False, ""
    try:
        with open(path) as f:
            info = json.load(f)
        until_iso = info.get("until")
        if until_iso:
            until = datetime.fromisoformat(until_iso.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= until:
                # Просрочено — удаляем
                os.remove(path)
                return False, ""
        return True, info.get("reason", "no reason")
    except Exception as e:
        return True, f"corrupted pause file: {e}"


def is_safe_mode():
    """Returns (active: bool, reason: str). Safe-mode НЕ имеет TTL — снимается только cmd_resume."""
    if not os.path.exists(SAFE_MODE_FILE):
        return False, ""
    try:
        with open(SAFE_MODE_FILE) as f:
            info = json.load(f)
        return True, info.get("reason", "no reason")
    except Exception as e:
        return True, f"safe-mode file corrupted: {e}"


def can_enter(bot_n):
    """
    Главный guard для cmd_enter(). 
    Returns (allowed: bool, reason: str if blocked).
    
    Usage в начале cmd_enter():
        from pause_check import can_enter
        ok, reason = can_enter(BOT_N)
        if not ok:
            log.warning(f"[PAUSE-GUARD] entry blocked: {reason}")
            return
    """
    # 1. Safe-mode — блокирует всё
    safe, reason = is_safe_mode()
    if safe:
        return False, f"SAFE-MODE: {reason}"

    # 2. Global pause
    paused, reason = _check_pause_file(PAUSE_GLOBAL)
    if paused:
        return False, f"GLOBAL PAUSE: {reason}"

    # 3. Bot-specific pause
    paused, reason = _check_pause_file(f"{STATE_DIR}/pause_bot{bot_n}")
    if paused:
        return False, f"BOT{bot_n} PAUSE: {reason}"

    return True, ""


def can_act():
    """Для rotation и других глобальных действий (не входы): только safe-mode блокирует.
    
    Usage в rotation.py:
        from pause_check import can_act
        ok, reason = can_act()
        if not ok:
            log.warning(f"[SAFE-MODE] rotation skipped: {reason}")
            return False, ["SAFE-MODE: " + reason]
    """
    safe, reason = is_safe_mode()
    if safe:
        return False, f"SAFE-MODE: {reason}"
    return True, ""


if __name__ == "__main__":
    # Самопроверка для отладки
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        safe, r = is_safe_mode()
        print(f"safe_mode: {'ACTIVE' if safe else 'clear'} ({r})")
        for n in range(1, 7):
            ok, reason = can_enter(n)
            print(f"bot{n}: {'BLOCKED' if not ok else 'ok'} ({reason or '-'})")
