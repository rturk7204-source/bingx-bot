"""
fleet_state.py — single source of truth for ARB fleet state.

File-based reader. Used by:
  • arb_commander.py  (Telegram daemon)
  • dashboard_arb.py  (Flask dashboard, /arb/api/fleet_state endpoint)
  • any future tool that needs a fleet snapshot without hitting BingX API

Why file-based: при глушении интернета API может быть недоступен,
но боты пишут state на диск каждые 5 минут — это становится надёжным
источником правды для observability.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_BOT_DIR = Path(os.getenv("BOT_DIR", "/root/bingx-bot"))
N_BOTS = 6


def _read_json(path: Path, default: Any = None) -> Any:
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def state_path(n: int, bot_dir: Path = DEFAULT_BOT_DIR) -> Path:
    return bot_dir / f"arb_state{n}.json"


def pause_path(n: int, bot_dir: Path = DEFAULT_BOT_DIR) -> Path:
    return bot_dir / "state" / f"pause_bot{n}"


def pause_global_path(bot_dir: Path = DEFAULT_BOT_DIR) -> Path:
    return bot_dir / "state" / "pause_global"


def safe_mode_path(bot_dir: Path = DEFAULT_BOT_DIR) -> Path:
    return bot_dir / "state" / "safe_mode"


def hedge_health_path(bot_dir: Path = DEFAULT_BOT_DIR) -> Path:
    return bot_dir / "state" / "hedge_health.json"


def watchdog_alerts_path(bot_dir: Path = DEFAULT_BOT_DIR) -> Path:
    return bot_dir / "state" / "watchdog_alerts.json"


def read_pause(path: Path) -> dict | None:
    """Returns {until_iso, reason, hours} if active and not expired, else None."""
    raw = _read_json(path, default=None)
    if not raw:
        return None
    try:
        until_str = raw.get("until", "").replace("Z", "+00:00")
        until = datetime.fromisoformat(until_str)
        if until <= datetime.now(timezone.utc):
            return None
        return {
            "until_iso": until.isoformat(),
            "until_human": until.strftime("%d.%m %H:%M UTC"),
            "reason": raw.get("reason", ""),
            "hours": raw.get("hours", 0),
        }
    except Exception:
        return None


def age_hours(entry_time: str) -> float:
    """Parse entry_time string and return age in hours (UTC)."""
    if not entry_time:
        return 0.0
    try:
        et = datetime.strptime(entry_time, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - et).total_seconds() / 3600
    except Exception:
        return 0.0


def funding_apy(rate_per_8h: float) -> float:
    """Convert per-period (8h) rate to annualised %."""
    return rate_per_8h * 3 * 365 * 100


def fleet_snapshot(bot_dir: Path = DEFAULT_BOT_DIR) -> dict:
    """
    Returns full fleet state dict for dashboard / commander:
      {
        "timestamp": iso,
        "safe_mode": bool,
        "pause_global": pause-dict or None,
        "bots": [ {n, open, symbol, ...}, ... ],
        "totals": { active, capital, earned }
      }
    """
    bots = []
    for n in range(1, N_BOTS + 1):
        st = _read_json(state_path(n, bot_dir), default={"position_open": False})
        pause = read_pause(pause_path(n, bot_dir))
        is_open = bool(st.get("position_open"))
        bot_info = {
            "n": n,
            "open": is_open,
            "symbol": st.get("symbol", "—"),
            "entry_time": st.get("entry_time", ""),
            "entry_price": float(st.get("entry_price", 0) or 0),
            "entry_rate": float(st.get("entry_rate", 0) or 0),
            "spot_qty": float(st.get("spot_qty", 0) or 0),
            "spot_budget": float(st.get("spot_budget", 0) or 0),
            "perp_margin": float(st.get("perp_margin", 0) or 0),
            "leverage": int(st.get("leverage", 1) or 1),
            "total_earned_usdt": float(st.get("total_earned_usdt", 0) or 0),
            "bad_periods": int(st.get("bad_periods", 0) or 0),
            "last_check": st.get("last_check", ""),
            "liquidated": bool(st.get("liquidated", False)),
            "pause": pause,
            "age_hours": age_hours(st.get("entry_time", "")) if is_open else 0.0,
            "funding_apy_pct": funding_apy(float(st.get("entry_rate", 0) or 0)) if is_open else 0.0,
        }
        bots.append(bot_info)

    open_bots = [b for b in bots if b["open"]]
    totals = {
        "active": len(open_bots),
        "n_bots": N_BOTS,
        "capital": sum(b["spot_budget"] + b["perp_margin"] for b in open_bots),
        "earned": sum(b["total_earned_usdt"] for b in bots),
    }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "safe_mode": safe_mode_path(bot_dir).exists(),
        "pause_global": read_pause(pause_global_path(bot_dir)),
        "bots": bots,
        "totals": totals,
    }


def health_snapshot(bot_dir: Path = DEFAULT_BOT_DIR) -> dict:
    """Returns hedge_health + watchdog_alerts content for dashboard /health endpoint."""
    return {
        "hedge_health": _read_json(hedge_health_path(bot_dir), default={}),
        "watchdog": _read_json(watchdog_alerts_path(bot_dir), default={}),
    }


# ─── Self-test ───────────────────────────────────────────────────────────────


def _selftest() -> None:
    import tempfile, shutil
    from datetime import timedelta

    tmp = Path(tempfile.mkdtemp(prefix="fleet_state_t_"))
    try:
        (tmp / "state").mkdir()

        # bot1 open
        (tmp / "arb_state1.json").write_text(json.dumps({
            "position_open": True, "symbol": "FOO-USDT",
            "entry_time": (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M UTC"),
            "entry_rate": 0.0005, "spot_budget": 100, "perp_margin": 100, "leverage": 3,
            "total_earned_usdt": 1.5,
        }))
        # bot2 idle
        (tmp / "arb_state2.json").write_text(json.dumps({"position_open": False}))
        # safe_mode
        (tmp / "state" / "safe_mode").write_text("ON")
        # pause_bot3
        (tmp / "state" / "pause_bot3").write_text(json.dumps({
            "until": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
            "reason": "test", "hours": 2,
        }))

        snap = fleet_snapshot(tmp)
        assert snap["safe_mode"] is True
        assert snap["totals"]["active"] == 1
        assert snap["totals"]["capital"] == 200
        assert snap["totals"]["earned"] == 1.5
        assert snap["bots"][0]["open"] and snap["bots"][0]["symbol"] == "FOO-USDT"
        assert snap["bots"][0]["age_hours"] >= 2.9
        assert snap["bots"][0]["funding_apy_pct"] > 50
        assert snap["bots"][1]["open"] is False
        assert snap["bots"][2]["pause"] is not None
        assert snap["bots"][2]["pause"]["reason"] == "test"

        # Expired pause should be filtered
        (tmp / "state" / "pause_bot4").write_text(json.dumps({
            "until": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "reason": "expired", "hours": 4,
        }))
        snap2 = fleet_snapshot(tmp)
        assert snap2["bots"][3]["pause"] is None, "expired pause should be filtered"

        # Corrupted state file should not crash
        (tmp / "arb_state5.json").write_text("{not json")
        snap3 = fleet_snapshot(tmp)
        assert snap3["bots"][4]["open"] is False  # default

        print("✓ fleet_snapshot all checks pass")

        h = health_snapshot(tmp)
        assert h == {"hedge_health": {}, "watchdog": {}}
        print("✓ health_snapshot empty default")

        print("\n[FLEET] all self-tests passed ✓")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _selftest()
