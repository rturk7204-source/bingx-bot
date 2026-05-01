#!/usr/bin/env python3
"""
state_backup.py — Block 4: бэкап + DR-восстановление state-файлов.

Режимы:
  1. local   — каждый час: tar.gz в /root/bingx-state-backups/, ротация 48ч
  2. remote  — раз в сутки: git push в private repo bingx-bot-state
  3. restore — восстановление из архива (по умолчанию — latest)
  4. list    — показать содержимое архива (без распаковки)

Что бэкапим (только данные, НЕ код и НЕ секреты):
  - state/*.json  (hedge_health, pause_*, safe_mode, watchdog_alerts и т.п.)
  - arb_bot*_state.json   (per-bot state)
  - trades.json, balance_history.json, blacklist.json
  - rl_states.json, pairs_state.json, oi_history.json
  - feature_importance_history.json

Что НЕ бэкапим:
  - .env (секреты!)
  - models/ (большие, отдельный backup_models.sh)
  - *.lock, *.tmp, *.bak.*

DR-сценарий (Block 4.4):
  python3 state_backup.py restore             # auto-pick latest local|remote
  python3 state_backup.py restore --archive X # явный путь
"""
import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

BOT_DIR = os.getenv("BOT_DIR", "/root/bingx-bot")
LOCAL_BACKUP_DIR = "/root/bingx-state-backups"
REMOTE_REPO_DIR = "/root/bingx-bot-state"
REMOTE_REPO_URL_FILE = f"{BOT_DIR}/.state_backup_repo"  # хранит git URL

LOCAL_KEEP_HOURS = 48

# Что собираем
INCLUDE_FILES = [
    "trades.json",
    "balance_history.json",
    "blacklist.json",
    "rl_states.json",
    "pairs_state.json",
    "oi_history.json",
    "feature_importance_history.json",
    "lifetime_pnl.json",  # Block 5.x: накопленный PnL через ротации
]
INCLUDE_GLOBS = [
    "state/*",          # все файлы state/: hedge_health.json, pause_*, safe_mode и т.п.
    "arb_bot*_state.json",
    "arb_state*.json",  # legacy naming
]


def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [BACKUP] {msg}"
    print(line)
    log_dir = f"{BOT_DIR}/logs"
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(f"{log_dir}/state_backup.log", "a") as f:
            f.write(line + "\n")
    except OSError:
        pass  # не падаем если лог-каталог недоступен (тесты)


def collect_files() -> list:
    """Возвращает список абсолютных путей файлов для бэкапа."""
    paths = []
    seen = set()
    for fname in INCLUDE_FILES:
        p = Path(BOT_DIR) / fname
        if p.exists() and str(p) not in seen:
            paths.append(p)
            seen.add(str(p))
    for pattern in INCLUDE_GLOBS:
        for p in Path(BOT_DIR).glob(pattern):
            if (p.is_file() and ".bak." not in p.name
                    and not p.name.endswith(".tmp")
                    and not p.name.endswith(".lock")
                    and str(p) not in seen):
                paths.append(p)
                seen.add(str(p))
    return paths


def make_tarball(out_path: str) -> int:
    """Создаёт tar.gz, возвращает размер в байтах."""
    files = collect_files()
    with tarfile.open(out_path, "w:gz") as tar:
        for f in files:
            arcname = str(f.relative_to(BOT_DIR))
            tar.add(str(f), arcname=arcname)
    return os.path.getsize(out_path)


# ────────────────── local backup ──────────────────

def cmd_local():
    os.makedirs(LOCAL_BACKUP_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out = os.path.join(LOCAL_BACKUP_DIR, f"state_{ts}.tar.gz")
    size = make_tarball(out)
    log(f"local: {out} ({size/1024:.1f} KB, {len(collect_files())} files)")

    # Ротация: удаляем файлы старше LOCAL_KEEP_HOURS
    cutoff = time.time() - LOCAL_KEEP_HOURS * 3600
    removed = 0
    for f in Path(LOCAL_BACKUP_DIR).glob("state_*.tar.gz"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
            removed += 1
    if removed:
        log(f"rotated {removed} old local backups")


# ────────────────── remote backup ──────────────────

def get_remote_url() -> str:
    if not os.path.exists(REMOTE_REPO_URL_FILE):
        return ""
    return open(REMOTE_REPO_URL_FILE).read().strip()


def cmd_remote():
    """Пушит state в private GitHub repo. Требует:
       - .state_backup_repo с git@github.com:owner/bingx-bot-state.git
       - SSH-ключ настроен (ssh-agent или /root/.ssh/id_*)
    """
    url = get_remote_url()
    if not url:
        log("remote: no URL configured (skip). "
            f"Создай {REMOTE_REPO_URL_FILE} с git URL приватного state-repo.")
        return

    # 1. Клонируем если нет
    if not os.path.exists(REMOTE_REPO_DIR):
        log(f"remote: cloning {url} -> {REMOTE_REPO_DIR}")
        r = subprocess.run(
            ["git", "clone", url, REMOTE_REPO_DIR],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            log(f"remote: clone FAILED: {r.stderr[:300]}")
            return

    # 2. Pull для свежести
    subprocess.run(["git", "-C", REMOTE_REPO_DIR, "pull", "--ff-only"],
                   capture_output=True, timeout=60)

    # 3. Делаем tarball и копируем в repo
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out_path = os.path.join(REMOTE_REPO_DIR, f"state_{ts}.tar.gz")
    size = make_tarball(out_path)

    # 4. Также копируем "latest" как удобный pointer
    latest = os.path.join(REMOTE_REPO_DIR, "state_latest.tar.gz")
    shutil.copy2(out_path, latest)

    # 5. Ротация в repo: оставляем последние 14 дней + latest
    cutoff = time.time() - 14 * 24 * 3600
    for f in Path(REMOTE_REPO_DIR).glob("state_*.tar.gz"):
        if f.name == "state_latest.tar.gz":
            continue
        if f.stat().st_mtime < cutoff:
            f.unlink()

    # 6. Commit + push
    subprocess.run(["git", "-C", REMOTE_REPO_DIR, "add", "-A"],
                   capture_output=True, timeout=30)
    msg = f"backup {ts} ({size/1024:.0f}KB)"
    r = subprocess.run(
        ["git", "-C", REMOTE_REPO_DIR, "commit", "-m", msg],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0 and "nothing to commit" not in r.stdout + r.stderr:
        log(f"remote: commit warn: {r.stderr[:200]}")
    r = subprocess.run(
        ["git", "-C", REMOTE_REPO_DIR, "push", "origin", "HEAD"],
        capture_output=True, text=True, timeout=120,
    )
    if r.returncode == 0:
        log(f"remote: pushed {msg}")
    else:
        log(f"remote: push FAILED: {r.stderr[:300]}")


# ────────────────── restore / list ──────────────────

def find_latest_archive() -> str:
    """Ищет latest архив в порядке: REMOTE/state_latest -> local newest -> remote newest."""
    candidates = []
    # 1. remote latest pointer
    p = Path(REMOTE_REPO_DIR) / "state_latest.tar.gz"
    if p.exists():
        return str(p)
    # 2. local newest
    if Path(LOCAL_BACKUP_DIR).exists():
        for f in Path(LOCAL_BACKUP_DIR).glob("state_*.tar.gz"):
            candidates.append(f)
    # 3. remote newest (без latest)
    if Path(REMOTE_REPO_DIR).exists():
        for f in Path(REMOTE_REPO_DIR).glob("state_*.tar.gz"):
            if f.name != "state_latest.tar.gz":
                candidates.append(f)
    if not candidates:
        return ""
    candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return str(candidates[0])


def cmd_restore(archive_path: str = "", target_dir: str = "", dry_run: bool = False):
    """Восстановление из tarball. По умолчанию latest, target = BOT_DIR."""
    target_dir = target_dir or BOT_DIR
    if not archive_path:
        archive_path = find_latest_archive()
        if not archive_path:
            log("restore: no archives found in local/remote — aborting")
            sys.exit(1)
        log(f"restore: auto-picked latest: {archive_path}")

    if not os.path.exists(archive_path):
        log(f"restore: archive not found: {archive_path}")
        sys.exit(1)

    # Гарантируем существование state/ в target
    os.makedirs(os.path.join(target_dir, "state"), exist_ok=True)

    log(f"restore: {archive_path} -> {target_dir} (dry_run={dry_run})")
    with tarfile.open(archive_path) as tar:
        members = tar.getmembers()
        for m in members:
            log(f"  - {m.name} ({m.size}B)")
        if not dry_run:
            tar.extractall(target_dir)
            log(f"restore: extracted {len(members)} files OK")
        else:
            log(f"restore: dry run, {len(members)} files would be extracted")


def cmd_list(archive_path: str = ""):
    """Показывает содержимое архива (без распаковки)."""
    if not archive_path:
        archive_path = find_latest_archive()
        if not archive_path:
            log("list: no archives found")
            sys.exit(1)
    if not os.path.exists(archive_path):
        log(f"list: archive not found: {archive_path}")
        sys.exit(1)
    print(f"== {archive_path} ==")
    with tarfile.open(archive_path) as tar:
        members = tar.getmembers()
        total = 0
        for m in members:
            total += m.size
            print(f"  {m.size:>10}B  {m.name}")
        print(f"== total: {len(members)} files, {total/1024:.1f} KB ==")


# ────────────────── CLI ──────────────────

def main():
    p = argparse.ArgumentParser(description="state_backup — DR for bingx-bot")
    p.add_argument("cmd", choices=["local", "remote", "restore", "list"])
    p.add_argument("--archive", default="", help="path to .tar.gz (restore/list); empty = latest")
    p.add_argument("--target", default="", help="restore target dir (default BOT_DIR)")
    p.add_argument("--dry-run", action="store_true", help="preview restore without extracting")
    args = p.parse_args()

    if args.cmd == "local":
        cmd_local()
    elif args.cmd == "remote":
        cmd_remote()
    elif args.cmd == "restore":
        cmd_restore(args.archive, args.target, args.dry_run)
    elif args.cmd == "list":
        cmd_list(args.archive)


if __name__ == "__main__":
    main()
