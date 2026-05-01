#!/usr/bin/env python3
"""
watchdog.py — Block 6: инфраструктурный мониторинг.

Запускается каждые 10 минут через cron. Проверяет:
  W1. Heartbeat: критические cron-jobs запускались в ожидаемом окне
  W2. Stale locks: arb_botN.lock старше 30 минут → kill PID + unlock
  W3. State integrity: все state-файлы парсятся как валидный JSON
  W4. Disk space: <1GB warn, <500MB critical
  W5. Zombie processes: pgrep arb_bot > 12 → alert (нормально 0-6)
  W6. hedge_health работает: lag последнего checkpoint <15 минут

Все алерты — в Telegram. Auto-actions:
  - W2 stale lock → kill -9 PID + unlink (всегда)
  - W3 corrupted state → safe_io уже восстановит из .bak; watchdog только алертит
  - W4 disk critical → принудительная ротация старых backups + alert
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BOT_DIR = "/root/bingx-bot"
STATE_DIR = f"{BOT_DIR}/state"
LOG_DIR = f"{BOT_DIR}/logs"
LOG_FILE = f"{LOG_DIR}/watchdog.log"
ALERT_THROTTLE_FILE = f"{STATE_DIR}/watchdog_alerts.json"
ALERT_THROTTLE_SEC = 1800  # один и тот же алерт не чаще раза в 30 мин

# Heartbeat-окна: имя_задачи → (log_path, max_age_sec, описание)
# Если log_path не обновлялся дольше max_age_sec — алерт.
# Большинство старых cron'ов пишут в /root/bingx-bot/ (НЕ в logs/),
# новые модули (Block 1/2/6) — в logs/.
HEARTBEATS = {
    # === New (logs/) ===
    "hedge_health":  (f"{LOG_DIR}/hedge_health.log", 900, "Block 2 monitoring */5min"),
    # === Legacy (BOT_DIR root) ===
    "auto_enter":    ("/var/log/bingx-auto-enter.log", 1800, "auto_enter.sh every 15min"),
    "arb_monitor":   (f"{BOT_DIR}/arb_monitor.log", 2400, "bot1 monitor every 30min"),
    "rotate_smart":  (f"{BOT_DIR}/rotation.log", 4 * 3600 + 1800, "rotate-smart every 4h"),
    "topup":         (f"{BOT_DIR}/arb_topup.log", 4500, "topup hourly"),
    "sync":          (f"{BOT_DIR}/arb_compound.log", 2400, "sync 15,45 min"),
    "rebalance":     (f"{BOT_DIR}/arb_rebalance.log", 2 * 3600 + 1800, "rebalance every 2h"),
    "liq_monitor":   (f"{BOT_DIR}/liq_monitor.log", 1200, "liq_monitor every 10min"),
    "dead_man":      (f"{BOT_DIR}/dead_man.out", 1800, "dead_man every 20min"),
    # funding_log запускается каждые 30м, но ПИШЕТ только на funding cycle (каждые 4ч):
    # 00:30, 04:30, 08:30, 12:30, 16:30, 20:30 UTC. Порог = 4ч3а0м с запасом.
    "funding_log":   (f"{BOT_DIR}/funding_log.out", 4 * 3600 + 1800, "funding_log on cycle (4h)"),
}

# State-файлы для integrity check
STATE_FILES = [
    f"{STATE_DIR}/hedge_health.json",
    f"{STATE_DIR}/pause.json",
    f"{STATE_DIR}/protection.json",
    f"{BOT_DIR}/trades.json",
    f"{BOT_DIR}/balance_history.json",
]
# arb_bot{N}_state.json добавятся динамически

LOCK_GLOB = f"{BOT_DIR}/*.lock"
STALE_LOCK_SEC = 1800  # 30 минут
DISK_WARN_MB = 1024
DISK_CRIT_MB = 512
ZOMBIE_THRESHOLD = 12  # pgrep arb_bot не должно быть больше


# ────────────────────── infra helpers ──────────────────────

def log(msg: str, level: str = "INFO"):
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [WD] {level} {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def get_telegram_creds():
    env_file = f"{BOT_DIR}/.env"
    creds = {}
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    token = creds.get("TELEGRAM_BOT_TOKEN") or creds.get("TELEGRAM_TOKEN")
    chat_id = creds.get("TELEGRAM_CHAT_ID")
    return token, chat_id


def _load_throttle():
    try:
        with open(ALERT_THROTTLE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_throttle(d):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(ALERT_THROTTLE_FILE, "w") as f:
            json.dump(d, f)
    except Exception as e:
        log(f"throttle save err: {e}", "WARN")


def alert(key: str, msg: str, force: bool = False):
    """Шлёт TG-алерт с throttle по key."""
    throttle = _load_throttle()
    now = time.time()
    if not force and key in throttle and now - throttle[key] < ALERT_THROTTLE_SEC:
        log(f"alert {key} throttled", "DEBUG")
        return
    throttle[key] = now
    _save_throttle(throttle)

    log(f"ALERT [{key}] {msg}", "ALERT")
    token, chat_id = get_telegram_creds()
    if not token or not chat_id:
        log("no telegram creds, alert local only", "WARN")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log(f"tg send err: {e}", "WARN")


# ────────────────────── checks ──────────────────────

def check_heartbeats():
    """W1: каждый critical cron-job писал в свой лог недавно.

    Стратегия:
      - файл отсутствует → только log WARN (НЕ TG): это нормально в первые минуты после деплоя
      - файл старше max_age → TG alert (реальный инцидент)
      - файл свежий → OK
    """
    for name, (path, max_age, desc) in HEARTBEATS.items():
        if not os.path.exists(path):
            log(f"W1 {name}: log not found ({path}) — first deploy?", "WARN")
            continue
        age = time.time() - os.path.getmtime(path)
        if age > max_age:
            alert(f"hb_stale_{name}",
                  f"🚨 <b>Watchdog W1: {name} silent</b>\n"
                  f"Последняя запись: {age/60:.0f} мин назад\n"
                  f"Порог: {max_age/60:.0f} мин\n"
                  f"({desc})")
            log(f"W1 {name}: age={age/60:.1f}m > {max_age/60:.0f}m STALE", "WARN")
        else:
            log(f"W1 {name}: age={age/60:.1f}m OK")


def check_stale_locks():
    """W2: lock-файлы старше STALE_LOCK_SEC → kill PID + unlink."""
    locks = list(Path(BOT_DIR).glob("*.lock"))
    for lock_path in locks:
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age < STALE_LOCK_SEC:
                continue

            # Читаем PID
            pid = None
            try:
                content = lock_path.read_text().strip()
                # ищем первое число
                m = re.search(r"\d+", content)
                if m:
                    pid = int(m.group())
            except Exception:
                pass

            killed = False
            if pid:
                # проверяем что процесс существует
                try:
                    os.kill(pid, 0)  # signal 0 = ping
                    # жив — убиваем
                    os.kill(pid, signal.SIGKILL)
                    killed = True
                    log(f"W2 killed PID {pid} for stale lock {lock_path.name}", "WARN")
                except ProcessLookupError:
                    log(f"W2 lock {lock_path.name} PID {pid} already dead", "INFO")
                except Exception as e:
                    log(f"W2 kill error PID {pid}: {e}", "WARN")

            try:
                lock_path.unlink()
            except Exception as e:
                log(f"W2 unlink err {lock_path}: {e}", "WARN")
                continue

            alert(
                f"stale_lock_{lock_path.name}",
                f"🔓 <b>Watchdog W2: stale lock cleaned</b>\n"
                f"File: {lock_path.name}\n"
                f"Age: {age/60:.0f} мин\n"
                f"PID: {pid} ({'killed' if killed else 'not found'})\n"
                f"Lock removed.",
                force=True,
            )
        except Exception as e:
            log(f"W2 outer err on {lock_path}: {e}", "WARN")


def check_state_integrity():
    """W3: все JSON state-файлы парсятся."""
    files = list(STATE_FILES)
    # arb_botN_state.json динамически
    for n in range(1, 7):
        files.append(f"{BOT_DIR}/arb_bot{n}_state.json")

    bad = []
    for p in files:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                json.load(f)
        except Exception as e:
            bad.append((p, str(e)[:80]))

    if bad:
        lines = "\n".join(f"  • {os.path.basename(p)}: {e}" for p, e in bad)
        alert(
            "state_corrupt",
            f"💾 <b>Watchdog W3: corrupted state</b>\n"
            f"Files:\n{lines}\n\n"
            f"safe_io должен был восстановить из .bak — проверь логи.",
        )
        log(f"W3 corrupted: {[p for p, _ in bad]}", "ERROR")
    else:
        log(f"W3 integrity OK ({len(files)} files checked)")


def check_disk_space():
    """W4: free disk < threshold."""
    try:
        st = os.statvfs(BOT_DIR)
        free_mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
    except Exception as e:
        log(f"W4 statvfs err: {e}", "WARN")
        return

    if free_mb < DISK_CRIT_MB:
        alert(
            "disk_crit",
            f"💀 <b>Watchdog W4: disk CRITICAL</b>\n"
            f"Free: {free_mb:.0f} MB (порог {DISK_CRIT_MB})\n"
            f"Запускаю агрессивную очистку backups.",
        )
        log(f"W4 disk CRITICAL {free_mb:.0f}MB", "ERROR")
        # агрессивная ротация — оставляем 1 день бэкапов
        try:
            subprocess.run(
                ["find", "/root/bingx-state-backups", "-mtime", "+1", "-delete"],
                check=False, timeout=30,
            )
        except Exception:
            pass
    elif free_mb < DISK_WARN_MB:
        alert(
            "disk_warn",
            f"📉 <b>Watchdog W4: disk low</b>\n"
            f"Free: {free_mb:.0f} MB (порог {DISK_WARN_MB})",
        )
        log(f"W4 disk warn {free_mb:.0f}MB", "WARN")
    else:
        log(f"W4 disk OK {free_mb:.0f}MB")


def check_zombies():
    """W5: pgrep arb_bot > ZOMBIE_THRESHOLD."""
    try:
        out = subprocess.run(
            ["pgrep", "-fa", "arb_bot"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        n = len(lines)
        if n > ZOMBIE_THRESHOLD:
            preview = "\n".join(lines[:10])
            alert(
                "zombies",
                f"🧟 <b>Watchdog W5: too many arb_bot procs</b>\n"
                f"Count: {n} (порог {ZOMBIE_THRESHOLD})\n"
                f"<pre>{preview[:500]}</pre>",
            )
            log(f"W5 zombies: {n} processes", "WARN")
        else:
            log(f"W5 procs={n} OK")
    except Exception as e:
        log(f"W5 err: {e}", "WARN")


# ────────────────────── main ──────────────────────

def run():
    log("watchdog start")
    try:
        check_heartbeats()
    except Exception as e:
        log(f"check_heartbeats crashed: {e}", "ERROR")
    try:
        check_stale_locks()
    except Exception as e:
        log(f"check_stale_locks crashed: {e}", "ERROR")
    try:
        check_state_integrity()
    except Exception as e:
        log(f"check_state_integrity crashed: {e}", "ERROR")
    try:
        check_disk_space()
    except Exception as e:
        log(f"check_disk_space crashed: {e}", "ERROR")
    try:
        check_zombies()
    except Exception as e:
        log(f"check_zombies crashed: {e}", "ERROR")
    log("watchdog done")


if __name__ == "__main__":
    run()
