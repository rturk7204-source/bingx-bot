#!/bin/bash
# auto_state_backup.sh — обёртка для cron, бэкапит state hourly.
#
# Block 4 даёт state_backup.py с командами local/remote/restore. Block 6
# гарантирует RPO ≤ 1ч через автозапуск раз в час в :05 (между funding-таиками
# на :00 и rotation cron на :00).
#
# Что делает:
#   1. Lock-файл (защита от двойного запуска при долгом git push).
#   2. state_backup.py local (всегда быстрый, ~3KB tar.gz).
#   3. state_backup.py remote (push в bingx-bot-state приватный репо).
#   4. Если remote упал (нет интернета / GitHub down) — TG-алерт, но cron не падает.
#
# Установка:
#   crontab -e
#   5 * * * * /root/bingx-bot/auto_state_backup.sh >> /var/log/bingx-state-backup.log 2>&1
#
# Логика выбора времени:
#   00:00 — funding payment, боты собирают
#   00:00 — auto_rotate.sh (rotation cron)
#   00:05 — auto_state_backup.sh (после ротации, чтобы зафиксить новое состояние)
set -u

BOT_DIR="${BOT_DIR:-/root/bingx-bot}"
LOG="/var/log/bingx-state-backup.log"
LOCK="/tmp/bingx-state-backup.lock"
PYTHON="${PYTHON:-python3}"

cd "$BOT_DIR" || { echo "[auto_state_backup] BOT_DIR=$BOT_DIR не найден"; exit 2; }

# 1. Lock
if [ -e "$LOCK" ]; then
    PID=$(cat "$LOCK" 2>/dev/null || echo "")
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "[auto_state_backup $(date -u +%Y-%m-%dT%H:%M:%SZ)] другой экземпляр PID=$PID — выходим" | tee -a "$LOG"
        exit 0
    fi
    rm -f "$LOCK"
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[auto_state_backup $TS] === starting ===" | tee -a "$LOG"

# 2. Local backup (всегда, не должен падать)
"$PYTHON" "$BOT_DIR/state_backup.py" local >> "$LOG" 2>&1
RC_LOCAL=$?
if [ "$RC_LOCAL" -ne 0 ]; then
    echo "[auto_state_backup $TS] LOCAL FAILED rc=$RC_LOCAL" | tee -a "$LOG"
fi

# 3. Remote backup (может упасть — нет интернета)
"$PYTHON" "$BOT_DIR/state_backup.py" remote >> "$LOG" 2>&1
RC_REMOTE=$?

TS2=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[auto_state_backup $TS2] === finished local=$RC_LOCAL remote=$RC_REMOTE ===" | tee -a "$LOG"

# 4. TG-алерт только если ОБА упали (если remote упал, но local прошёл — RPO ≤1ч
# держится локально, не паникуем). Если local упал — это серьёзно (диск?).
if [ "$RC_LOCAL" -ne 0 ] && [ "$RC_REMOTE" -ne 0 ]; then
    BX_LOG_TAIL=$(tail -n 30 "$LOG" | tail -c 1500)
    BX_BOT_DIR="$BOT_DIR"
    export BX_LOG_TAIL BX_BOT_DIR
    "$PYTHON" - <<'PYEOF' || true
import sys, os
sys.path.insert(0, os.environ.get("BX_BOT_DIR", "/root/bingx-bot"))
tail = os.environ.get("BX_LOG_TAIL", "")
tail_safe = tail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
try:
    from arb_tools import tg_send
    tg_send(f"🚨 auto_state_backup.sh: BOTH local+remote упали\n<pre>{tail_safe}</pre>")
except Exception as e:
    print(f"[auto_state_backup] TG send failed: {e}", file=sys.stderr)
PYEOF
elif [ "$RC_LOCAL" -ne 0 ]; then
    # local упал — это подозрительно (диск/permissions). Алерт.
    BX_LOG_TAIL=$(tail -n 30 "$LOG" | tail -c 1000)
    BX_BOT_DIR="$BOT_DIR"
    export BX_LOG_TAIL BX_BOT_DIR
    "$PYTHON" - <<'PYEOF' || true
import sys, os
sys.path.insert(0, os.environ.get("BX_BOT_DIR", "/root/bingx-bot"))
tail = os.environ.get("BX_LOG_TAIL", "")
tail_safe = tail.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
try:
    from arb_tools import tg_send
    tg_send(f"⚠️ auto_state_backup.sh: LOCAL backup упал (диск?)\n<pre>{tail_safe}</pre>")
except Exception as e:
    print(f"[auto_state_backup] TG send failed: {e}", file=sys.stderr)
PYEOF
fi

# Cron не должен падать ни при каких обстоятельствах — exit 0
exit 0
