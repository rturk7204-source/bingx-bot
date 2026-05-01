#!/usr/bin/env python3
"""
arb_commander.py — Telegram long-polling daemon for ARB bot fleet.

Block 5 (Observability). Standalone daemon — does NOT require importing
any arb_bot. All data is read from state files on disk so it stays useful
even when API is jammed/blocked.

Commands:
  /status            — fleet overview (6 bots, paused flags, fleet P&L)
  /status N          — bot N detail (pair, basis, funding, age, P&L)
  /positions         — table of all open positions
  /pause [N|global] [hours]  — call arb_tools --pause
  /resume            — call arb_tools --resume
  /report            — daily P&L per bot + fleet total
  /health            — last hedge_health.json + watchdog_alerts.json
  /help              — list commands

Run:  python3 arb_commander.py
Stop: SIGINT/SIGTERM (cleans up gracefully)

systemd unit: see arb_commander.service
"""
from __future__ import annotations

import os
import sys
import json
import time
import signal
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

try:
    import env_loader  # noqa: F401  — auto-loads .env on VPS
except ImportError:
    pass  # env_loader is gitignored (secrets); fine for selftest in sandbox

import requests

import fleet_state as fs  # shared file-based snapshot

BOT_DIR = Path(os.getenv("BOT_DIR", "/root/bingx-bot"))
STATE_DIR = BOT_DIR / "state"
N_BOTS = fs.N_BOTS
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
TG_API = f"https://api.telegram.org/bot{TG_TOKEN}"

POLL_TIMEOUT = 25       # long-poll timeout (seconds)
LOOP_BACKOFF = 5        # sleep on error
ARB_TOOLS = str(BOT_DIR / "arb_tools.py")
PYTHON = sys.executable or "python3"

# ─────────────────────────────────────────────────────────────────────────────
# State readers (file-based — survives API outage)
# ─────────────────────────────────────────────────────────────────────────────


def _read_json(path: Path, default: Any = None) -> Any:
    """Robust JSON read. Never raises — returns default on any error."""
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


# Path helpers — use fleet_state but allow override for selftest
def _bot_dir() -> Path:
    return BOT_DIR


def state_path(n: int) -> Path:
    return fs.state_path(n, _bot_dir())


def pause_path(n: int) -> Path:
    return fs.pause_path(n, _bot_dir())


def pause_global_path() -> Path:
    return fs.pause_global_path(_bot_dir())


def safe_mode_path() -> Path:
    return fs.safe_mode_path(_bot_dir())


def hedge_health_path() -> Path:
    return fs.hedge_health_path(_bot_dir())


def watchdog_alerts_path() -> Path:
    return fs.watchdog_alerts_path(_bot_dir())


def read_pause(path: Path) -> dict | None:
    """Compatibility wrapper — returns dict with 'until' (datetime) for legacy callers."""
    p = fs.read_pause(path)
    if not p:
        return None
    # Convert iso back to datetime for fmt_*
    return {
        "until": datetime.fromisoformat(p["until_iso"]),
        "reason": p["reason"],
        "hours": p["hours"],
    }


def fleet_snapshot() -> list[dict]:
    """Compat wrapper around fs.fleet_snapshot — returns flat bots list."""
    snap = fs.fleet_snapshot(_bot_dir())
    out = []
    for b in snap["bots"]:
        # Re-attach datetime-shaped pause for legacy formatters
        b2 = dict(b)
        if b["pause"]:
            b2["pause"] = {
                "until": datetime.fromisoformat(b["pause"]["until_iso"]),
                "reason": b["pause"]["reason"],
                "hours": b["pause"]["hours"],
            }
        out.append(b2)
    return out


def age_hours(entry_time: str) -> float:
    return fs.age_hours(entry_time)


def funding_apy(rate_per_8h: float) -> float:
    return fs.funding_apy(rate_per_8h)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram I/O
# ─────────────────────────────────────────────────────────────────────────────


def tg_send(text: str, chat_id: str | None = None, parse_mode: str = "HTML") -> None:
    if not TG_TOKEN:
        print("[CMD] TELEGRAM_BOT_TOKEN missing — skip send")
        return
    chat = chat_id or TG_CHAT
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={
                "chat_id": chat,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        print(f"[CMD] send error: {e}")


def tg_get_updates(offset: int) -> list[dict]:
    if not TG_TOKEN:
        return []
    try:
        r = requests.get(
            f"{TG_API}/getUpdates",
            params={"offset": offset, "timeout": POLL_TIMEOUT, "allowed_updates": '["message"]'},
            timeout=POLL_TIMEOUT + 10,
        )
        return r.json().get("result", []) or []
    except Exception as e:
        print(f"[CMD] poll error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────────────────────────────────────


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_status_fleet() -> str:
    snap = fleet_snapshot()
    safe_mode = safe_mode_path().exists()
    pause_global = read_pause(pause_global_path())
    open_bots = [b for b in snap if b["open"]]
    total_capital = sum(b["spot_budget"] + b["perp_margin"] for b in open_bots)
    total_earned = sum(b["total_earned_usdt"] for b in snap)

    lines = ["<b>🤖 ARB Fleet Status</b>", ""]
    if safe_mode:
        lines.append("🔴 <b>SAFE MODE ACTIVE</b> — все входы блокированы")
    if pause_global:
        until = pause_global["until"].strftime("%d.%m %H:%M UTC")
        lines.append(f"⏸ Global pause до {until} ({html_escape(pause_global['reason'])})")
    if not (safe_mode or pause_global):
        lines.append("🟢 Fleet operational")
    lines.append("")
    lines.append(f"Active: <b>{len(open_bots)}/{N_BOTS}</b> | Capital: <b>${total_capital:.0f}</b> | Earned: <b>${total_earned:+.2f}</b>")
    lines.append("")

    for b in snap:
        n = b["n"]
        if b["open"]:
            age = age_hours(b["entry_time"])
            apy = funding_apy(b["entry_rate"])
            badge = "🟢"
            if b["pause"]:
                badge = "⏸"
            if b["bad_periods"] > 0:
                badge = "⚠️"
            sym = html_escape(b["symbol"])
            lines.append(
                f"{badge} <b>bot{n}</b> {sym} | ${b['spot_budget']:.0f} | "
                f"APY {apy:.1f}% | age {age:.1f}h"
            )
            if b["pause"]:
                lines.append(f"     ⏸ paused → {b['pause']['until'].strftime('%d.%m %H:%M')}")
        else:
            badge = "⏸" if b["pause"] else "⚪"
            lines.append(f"{badge} <b>bot{n}</b> idle")

    lines.append("")
    lines.append(f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</i>")
    return "\n".join(lines)


def fmt_status_bot(n: int) -> str:
    if not (1 <= n <= N_BOTS):
        return f"❌ bot{n}: вне диапазона 1..{N_BOTS}"
    snap = fleet_snapshot()
    b = snap[n - 1]
    pause = b["pause"]

    lines = [f"<b>🤖 bot{n} detail</b>", ""]
    if not b["open"]:
        lines.append("⚪ <b>idle</b> — нет открытой позиции")
        if pause:
            lines.append(f"⏸ paused до {pause['until'].strftime('%d.%m %H:%M UTC')}")
            lines.append(f"   reason: {html_escape(pause['reason'])}")
        return "\n".join(lines)

    age = age_hours(b["entry_time"])
    apy = funding_apy(b["entry_rate"])
    sym = html_escape(b["symbol"])
    lines += [
        f"🟢 <b>{sym}</b>",
        f"Entry: <b>${b['entry_price']:.6f}</b> @ {html_escape(b['entry_time'])}",
        f"Spot qty: <b>{b['spot_qty']:.4f}</b> | budget: <b>${b['spot_budget']:.0f}</b>",
        f"Perp margin: <b>${b['perp_margin']:.0f}</b> | leverage: <b>{b['leverage']}x</b>",
        f"Funding rate (entry): <b>{b['entry_rate']*100:.4f}%/8h</b> ≈ <b>{apy:.1f}% APY</b>",
        f"Age: <b>{age:.1f}h</b>",
        f"Earned: <b>${b['total_earned_usdt']:+.4f}</b>",
        f"Bad periods: <b>{b['bad_periods']}</b>",
    ]
    if b["liquidated"]:
        lines.append("🚨 <b>LIQUIDATED</b>")
    if pause:
        lines.append(f"⏸ paused до {pause['until'].strftime('%d.%m %H:%M UTC')} ({html_escape(pause['reason'])})")
    if b["last_check"]:
        lines.append(f"<i>last check: {html_escape(b['last_check'])}</i>")
    return "\n".join(lines)


def fmt_positions() -> str:
    snap = fleet_snapshot()
    open_bots = [b for b in snap if b["open"]]
    if not open_bots:
        return "📭 Нет открытых позиций"
    lines = ["<b>📊 Open positions</b>", ""]
    lines.append("<pre>")
    lines.append(f"{'bot':<5}{'pair':<14}{'$':>6}{'APY%':>7}{'age':>6}")
    for b in open_bots:
        age = age_hours(b["entry_time"])
        apy = funding_apy(b["entry_rate"])
        sym = b["symbol"][:13]
        lines.append(f"{b['n']:<5}{sym:<14}{b['spot_budget']:>6.0f}{apy:>7.1f}{age:>5.1f}h")
    lines.append("</pre>")
    total = sum(b["spot_budget"] + b["perp_margin"] for b in open_bots)
    earned = sum(b["total_earned_usdt"] for b in snap)
    lines.append(f"\nTotal capital: <b>${total:.0f}</b>")
    lines.append(f"Total earned: <b>${earned:+.2f}</b>")
    return "\n".join(lines)


def fmt_health() -> str:
    hh = _read_json(hedge_health_path(), default={})
    wd = _read_json(watchdog_alerts_path(), default={})
    lines = ["<b>🩺 Health</b>", ""]

    if hh:
        ts = hh.get("timestamp", hh.get("last_run", ""))
        lines.append(f"hedge_health: <i>{html_escape(str(ts))}</i>")
        triggers = hh.get("triggers", []) or []
        if triggers:
            lines.append(f"  ⚠️ triggers: {len(triggers)}")
            for t in triggers[:5]:
                lines.append(f"    • {html_escape(str(t))}")
        else:
            lines.append("  ✅ no triggers")
    else:
        lines.append("hedge_health: <i>нет данных</i>")

    lines.append("")
    if wd:
        ts = wd.get("timestamp", wd.get("last_run", ""))
        lines.append(f"watchdog: <i>{html_escape(str(ts))}</i>")
        alerts = wd.get("alerts", []) or []
        if alerts:
            lines.append(f"  ⚠️ alerts: {len(alerts)}")
            for a in alerts[:5]:
                lines.append(f"    • {html_escape(str(a))}")
        else:
            lines.append("  ✅ no alerts")
    else:
        lines.append("watchdog: <i>нет данных</i>")
    return "\n".join(lines)


def fmt_report() -> str:
    """Daily P&L report — funding accrued + total earned per bot."""
    snap = fleet_snapshot()
    open_bots = [b for b in snap if b["open"]]
    lines = ["<b>📈 Daily Report</b>", ""]

    fleet_earned = sum(b["total_earned_usdt"] for b in snap)
    fleet_capital = sum(b["spot_budget"] + b["perp_margin"] for b in open_bots)

    lines.append(f"Capital deployed: <b>${fleet_capital:.0f}</b>")
    lines.append(f"Total earned (lifetime): <b>${fleet_earned:+.2f}</b>")
    lines.append("")

    for b in snap:
        n = b["n"]
        if b["open"]:
            age = age_hours(b["entry_time"])
            apy = funding_apy(b["entry_rate"])
            sym = html_escape(b["symbol"])
            # Daily yield estimate: APY/365 * capital
            daily_est = (apy / 365 / 100) * (b["spot_budget"] + b["perp_margin"])
            lines.append(
                f"bot{n} {sym}: earned <b>${b['total_earned_usdt']:+.4f}</b> "
                f"| ~${daily_est:+.2f}/day | age {age:.1f}h"
            )
        else:
            if b["total_earned_usdt"] != 0:
                lines.append(f"bot{n} idle: earned <b>${b['total_earned_usdt']:+.4f}</b>")
            else:
                lines.append(f"bot{n} idle")

    return "\n".join(lines)


def cmd_pause_resume(args: list[str], resume: bool) -> str:
    """Run arb_tools.py with --pause or --resume; return stdout/stderr summary."""
    if resume:
        argv = [PYTHON, ARB_TOOLS, "--resume"]
    else:
        # /pause [global|N] [hours]
        scope = "global"
        hours = 4
        reason = "tg-manual"
        if len(args) >= 1 and args[0]:
            scope = args[0]
        if len(args) >= 2 and args[1]:
            try:
                hours = int(args[1])
            except ValueError:
                return f"❌ Invalid hours: {args[1]}"
        argv = [
            PYTHON, ARB_TOOLS,
            "--pause", scope,
            "--pause-hours", str(hours),
            "--pause-reason", reason,
        ]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        out = (r.stdout or "").strip() or (r.stderr or "").strip() or "(no output)"
        prefix = "✅" if r.returncode == 0 else "⚠️"
        return f"{prefix} {' '.join(argv[1:])}\n<pre>{html_escape(out[:1500])}</pre>"
    except subprocess.TimeoutExpired:
        return "❌ arb_tools timed out (>30s)"
    except Exception as e:
        return f"❌ exec error: {html_escape(str(e))}"


HELP = """<b>📋 ARB Commander</b>

/status — общий обзор fleet
/status N — детали bot N (1..6)
/positions — таблица открытых позиций
/report — daily P&amp;L
/health — hedge_health + watchdog
/pause [global|N] [hours] — пауза (default global, 4h)
/resume — снять safe-mode и все паузы
/help — эта справка

<i>Все данные читаются из state-файлов на диске
и работают даже когда API недоступен.</i>"""


def dispatch(text: str) -> str:
    parts = text.strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower()
    # strip @botname suffix if present
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    args = parts[1:]

    if cmd == "/start" or cmd == "/help":
        return HELP
    if cmd == "/status":
        if args:
            try:
                return fmt_status_bot(int(args[0]))
            except ValueError:
                return "❌ /status N — N должно быть числом 1..6"
        return fmt_status_fleet()
    if cmd == "/positions":
        return fmt_positions()
    if cmd == "/report":
        return fmt_report()
    if cmd == "/health":
        return fmt_health()
    if cmd == "/pause":
        return cmd_pause_resume(args, resume=False)
    if cmd == "/resume":
        return cmd_pause_resume([], resume=True)

    return f"❓ Неизвестная команда: {html_escape(cmd)}\n\n{HELP}"


# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────


_running = True


def _on_signal(signum, frame):
    global _running
    print(f"[CMD] received signal {signum}, shutting down")
    _running = False


def main() -> int:
    if not TG_TOKEN or not TG_CHAT:
        print("[CMD] missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID — exit", file=sys.stderr)
        return 2

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    print(f"[CMD] arb_commander started (BOT_DIR={BOT_DIR})")
    tg_send("🤖 <b>ARB Commander online</b>\n\nНапиши /help — список команд.")

    offset = 0
    while _running:
        try:
            updates = tg_get_updates(offset)
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = str(chat.get("id", ""))
                text = msg.get("text", "") or ""
                if chat_id != str(TG_CHAT):
                    print(f"[CMD] ignore message from chat {chat_id}")
                    continue
                if not text.startswith("/"):
                    continue
                print(f"[CMD] cmd: {text!r}")
                try:
                    reply = dispatch(text)
                    if reply:
                        tg_send(reply)
                except Exception as e:
                    tg_send(f"❌ Ошибка обработки: <code>{html_escape(str(e))}</code>")
                    print(f"[CMD] dispatch error: {e}")
        except Exception as e:
            print(f"[CMD] loop error: {e}")
            time.sleep(LOOP_BACKOFF)

    tg_send("⏹ <b>ARB Commander offline</b>")
    print("[CMD] exit")
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────


def _selftest() -> None:
    """Smoke test — exercise formatters with synthetic state files in /tmp."""
    import tempfile, shutil
    tmp = Path(tempfile.mkdtemp(prefix="arb_cmd_test_"))
    try:
        global BOT_DIR, STATE_DIR
        BOT_DIR_save, STATE_DIR_save = BOT_DIR, STATE_DIR
        BOT_DIR = tmp
        STATE_DIR = tmp / "state"
        STATE_DIR.mkdir(parents=True)

        # bot1: open position
        (tmp / "arb_state1.json").write_text(json.dumps({
            "position_open": True,
            "symbol": "TEST-USDT",
            "entry_time": (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M UTC"),
            "entry_price": 1.234,
            "entry_rate": 0.000414,
            "spot_qty": 64.5,
            "spot_budget": 80,
            "perp_margin": 80,
            "leverage": 3,
            "total_earned_usdt": 0.85,
            "bad_periods": 0,
        }))
        # bot2: idle
        (tmp / "arb_state2.json").write_text(json.dumps({"position_open": False}))
        # bot3-6: missing files

        # bot4: paused
        (STATE_DIR / "pause_bot4").write_text(json.dumps({
            "until": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            "reason": "manual-test",
            "hours": 2,
        }))

        # safe_mode flag
        (STATE_DIR / "safe_mode").write_text("ON")

        # hedge_health + watchdog
        (STATE_DIR / "hedge_health.json").write_text(json.dumps({
            "timestamp": "2026-05-01T14:00:00Z",
            "triggers": [],
        }))
        (STATE_DIR / "watchdog_alerts.json").write_text(json.dumps({
            "timestamp": "2026-05-01T14:05:00Z",
            "alerts": ["test-alert-1"],
        }))

        # Run formatters
        fleet = fmt_status_fleet()
        assert "SAFE MODE" in fleet, "safe_mode badge missing"
        assert "TEST-USDT" in fleet, "bot1 symbol missing"
        # bot4 is idle but paused — should show ⏸ badge on its line
        assert "⏸ <b>bot4</b>" in fleet, f"pause_bot4 badge missing in:\n{fleet}"
        print("✓ fmt_status_fleet")

        b1 = fmt_status_bot(1)
        assert "TEST-USDT" in b1
        assert "APY" in b1
        print("✓ fmt_status_bot(1)")

        b2 = fmt_status_bot(2)
        assert "idle" in b2
        print("✓ fmt_status_bot(2) idle")

        bx = fmt_status_bot(99)
        assert "вне диапазона" in bx
        print("✓ fmt_status_bot(99) out-of-range")

        pos = fmt_positions()
        assert "TEST-USDT" in pos
        print("✓ fmt_positions")

        rep = fmt_report()
        assert "Capital deployed" in rep
        print("✓ fmt_report")

        h = fmt_health()
        assert "test-alert-1" in h
        print("✓ fmt_health")

        # Dispatch
        assert "ARB Commander" in dispatch("/help")
        assert "Fleet" in dispatch("/status")
        assert "TEST-USDT" in dispatch("/status 1")
        assert "Неизвестная" in dispatch("/foobar")
        print("✓ dispatch")

        # Restore globals
        BOT_DIR, STATE_DIR = BOT_DIR_save, STATE_DIR_save
        print("\n[CMD] all self-tests passed ✓")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
    else:
        sys.exit(main())
