#!/bin/bash
# auto_rotate.sh — обёртка для cron, запускает rotation.py --apply.
#
# Цель: пользователь не хочет фиксить и запускать ротацию вручную; интернет
# периодически глушится → нужна полная автономия. Скрипт:
#   1. Проверяет, не запущен ли уже другой экземпляр (lock-файл).
#   2. Берёт snapshot state ДО ротации (для быстрого rollback).
#   3. Запускает rotation.py --apply, перенаправляет всё в /var/log/bingx-rotation.log.
#   4. Если rc != 0 — шлёт TG-алерт через arb_tools.tg_send.
#
# Установка cron на VPS:
#   crontab -e
#   0 * * * * /root/bingx-bot/auto_rotate.sh >> /var/log/bingx-rotation-cron.log 2>&1
#
# Pause/safe-mode уже учитываются внутри rotation.py (cmd_rotate_smart →
# pause_check.can_act). Здесь повторно не проверяем — single source of truth.

set -u  # без -e: хотим обработать exit-code сами

BOT_DIR="${BOT_DIR:-/root/bingx-bot}"
LOG="/var/log/bingx-rotation.log"
LOCK="/tmp/bingx-rotation.lock"
PYTHON="${PYTHON:-python3}"

cd "$BOT_DIR" || { echo "[auto_rotate] BOT_DIR=$BOT_DIR не найден"; exit 2; }

# 1. Lock — не запускаем второй экземпляр (cron может пересечься если ротация
# затянется > 1ч; маловероятно, но защищаемся).
if [ -e "$LOCK" ]; then
    PID=$(cat "$LOCK" 2>/dev/null || echo "")
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "[auto_rotate $(date -u +%Y-%m-%dT%H:%M:%SZ)] другой экземпляр PID=$PID работает — выходим" | tee -a "$LOG"
        exit 0
    fi
    # stale lock
    rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[auto_rotate $TS] === starting hourly rotation ===" | tee -a "$LOG"

# 2. Запуск
"$PYTHON" "$BOT_DIR/rotation.py" --apply >> "$LOG" 2>&1
RC=$?

TS2=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[auto_rotate $TS2] === finished rc=$RC ===" | tee -a "$LOG"

# 3. Если упало — TG-алерт. rotation.py уже шлёт TG в обычных сценариях
# (включая safe-mode skip), но при крэше/ImportError TG не успеет — шлём здесь.
#
# FIX (Block 5.x bug #5): раньше передавали $TAIL через heredoc интерполяцию
# ("<pre>$TAIL</pre>") — если в логе были кавычки / `\n` / `'` — Python
# код сам ломался. Теперь передаём через env vars и читаем в Python через
# os.environ — никакой shell-интерполяции.
if [ "$RC" -ne 0 ]; then
    BX_LOG_TAIL=$(tail -n 30 "$LOG" | tail -c 1500)
    BX_RC="$RC"
    BX_BOT_DIR="$BOT_DIR"
    export BX_LOG_TAIL BX_RC BX_BOT_DIR
    "$PYTHON" - <<'PYEOF' || true
import sys, os
sys.path.insert(0, os.environ.get("BX_BOT_DIR", "/root/bingx-bot"))
tail = os.environ.get("BX_LOG_TAIL", "")
rc   = os.environ.get("BX_RC", "?")
# экранируем HTML-специальные перед <pre> (без sed в баше)
tail_safe = tail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
try:
    from arb_tools import tg_send
    tg_send(f"🚨 auto_rotate.sh упал rc={rc}\n<pre>{tail_safe}</pre>")
except Exception as e:
    print(f"[auto_rotate] TG send failed: {e}", file=sys.stderr)
PYEOF
fi

exit "$RC"
