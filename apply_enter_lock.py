#!/usr/bin/env python3
"""
apply_enter_lock.py — добавляет fcntl.flock в cmd_enter() всех 6 arb_bot*.py.

Защищает от двойного одновременного --enter (например: cron + ручной запуск).
Lock-файл: /tmp/arb_bot{N}.enter.lock
Если lock уже взят — моментальный выход с ошибкой.

Идемпотентно: проверяет marker `# ENTER_LOCK_V1`.
"""
import os, re, shutil, time, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

TARGETS = [
    "arb_bot.py",
    "arb_bot2.py",
    "arb_bot3.py",
    "arb_bot4.py",
    "arb_bot5.py",
    "arb_bot6.py",
]

STAMP = time.strftime("%Y%m%d_%H%M%S")
MARKER = "# ENTER_LOCK_V1"

# Snippet to inject at start of cmd_enter()
LOCK_SNIPPET = '''    # {marker}  Prevent concurrent --enter (cron + manual)
    import fcntl
    _lock_path = "/tmp/{lockname}.enter.lock"
    _lock_fh = open(_lock_path, "w")
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error(f"ENTER уже выполняется другим процессом (lock: {{_lock_path}}). Выход.")
        return
    _lock_fh.write(str(os.getpid()) + "\\n"); _lock_fh.flush()
'''


def lockname_for(filename: str) -> str:
    """arb_bot.py -> arb_bot, arb_bot2.py -> arb_bot2, etc."""
    return filename.replace(".py", "")


def patch(path: Path) -> str:
    if not path.exists():
        return "missing"
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        return "already"

    # Find `def cmd_enter():` line
    m = re.search(r'^def cmd_enter\(\):\s*\n', text, re.MULTILINE)
    if not m:
        return "no_cmd_enter"

    # Find insertion point: after possible docstring, after first line of function body
    # Strategy: insert right after `def cmd_enter():\n`, before first body line
    insert_pos = m.end()

    snippet = LOCK_SNIPPET.format(
        marker=MARKER,
        lockname=lockname_for(path.name),
    )

    # Backup
    bak = path.with_suffix(path.suffix + f".pre_enterlock_{STAMP}")
    shutil.copy2(path, bak)

    new_text = text[:insert_pos] + snippet + text[insert_pos:]
    path.write_text(new_text, encoding="utf-8")
    return "added"


def main():
    os.chdir(HERE)
    print(f"Injecting enter-lock into {len(TARGETS)} bots...\n")

    results = []
    for name in TARGETS:
        p = HERE / name
        res = patch(p)
        results.append((name, res))
        print(f"  {name:15s} -> {res}")

    print("\nSyntax check...")
    import ast
    all_ok = True
    for name, res in results:
        if res != "added":
            continue
        try:
            ast.parse((HERE / name).read_text(encoding="utf-8"))
            print(f"  {name:15s} syntax OK")
        except SyntaxError as e:
            print(f"  {name:15s} SYNTAX ERROR line {e.lineno}: {e.msg}")
            all_ok = False

    if not all_ok:
        print("\n⚠️ Syntax errors! Restore from .pre_enterlock_* backups!")
        sys.exit(2)

    # Smoke test: import each patched bot
    print("\nImport smoke test...")
    sys.path.insert(0, str(HERE))
    for name, res in results:
        if res != "added":
            continue
        mod_name = name.replace(".py", "")
        try:
            if mod_name in sys.modules:
                del sys.modules[mod_name]
            __import__(mod_name)
            print(f"  {mod_name:15s} import OK")
        except Exception as e:
            print(f"  {mod_name:15s} IMPORT ERROR: {e}")
            all_ok = False

    if all_ok:
        print("\n✅ Enter-lock patch applied successfully to all bots.")
    else:
        sys.exit(3)


if __name__ == "__main__":
    main()
