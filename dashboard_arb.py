#!/usr/bin/env python3
# env_loader loaded by main dashboard.py
"""
dashboard_arb.py — Flask Blueprint с расширенными ARB-виджетами.

Routes:
  /arb                        — страница со всеми 4 виджетами
  /arb/api/graveyard          — JSON: список графвьярд-пар
  /arb/api/funding_history    — JSON: данные funding_log.csv
  /arb/api/rotation_log       — JSON: последние 5 событий ротации
  /arb/api/liq_distance       — JSON: запас до ликвидации по каждой позиции

Подключение в dashboard.py:
    from dashboard_arb import arb_bp
    app.register_blueprint(arb_bp)
"""
import csv
import json
from pathlib import Path
from functools import wraps
# datetime/timezone импортируются локально в функциях где нужны (Block 4 cleanup)

from flask import Blueprint, jsonify, render_template_string, session, redirect

HERE = Path(__file__).resolve().parent

arb_bp = Blueprint("arb", __name__, url_prefix="/arb")


# ─── Helpers ────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def _read_json(path: Path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return default if default is not None else {}


def _read_csv_tail(path: Path, n: int = 200) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open() as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        return rows[-n:]
    except Exception:
        return []


# ─── Data sources ───────────────────────────────────────────────────────────

def get_graveyard() -> list[dict]:
    """Read rotation_graveyard.json and return list of graveyard entries."""
    gv = _read_json(HERE / "rotation_graveyard.json", {})
    result = []
    for sym, info in gv.items():
        result.append({
            "symbol": sym,
            "ejected_at": info.get("ejected_at", ""),
            "reason": info.get("reason", ""),
        })
    # Sort by ejected_at desc (newest first)
    result.sort(key=lambda x: x["ejected_at"], reverse=True)
    return result


def get_funding_history(days: int = 14) -> dict:
    """
    Read funding_log.csv and aggregate by day × symbol.
    Returns: {dates: [...], series: {SYMBOL: [amount, ...], ...}}
    """
    rows = _read_csv_tail(HERE / "funding_history.csv", 5000)
    if not rows:
        return {"dates": [], "series": {}, "total": 0.0}

    # Group by date + symbol
    by_day_sym = {}  # (day, symbol) -> amount
    symbols = set()
    days_set = set()

    for row in rows:
        # funding_history.csv header: timestamp_utc,bot,label,symbol,entry_rate_pct,payment_number,amount_usdt,total_earned_usdt,spot_budget,leverage
        ts = row.get("timestamp_utc") or row.get("timestamp") or row.get("ts") or ""
        sym = row.get("symbol") or row.get("pair") or ""
        try:
            amt = float(row.get("amount_usdt") or row.get("earned") or row.get("amount") or 0)
        except Exception:
            amt = 0.0
        if not ts or not sym:
            continue
        # Normalize date → YYYY-MM-DD
        day = ts[:10] if len(ts) >= 10 else ts
        by_day_sym[(day, sym)] = by_day_sym.get((day, sym), 0) + amt
        symbols.add(sym)
        days_set.add(day)

    dates = sorted(days_set)[-days:]
    series = {}
    for sym in sorted(symbols):
        series[sym] = [round(by_day_sym.get((d, sym), 0), 4) for d in dates]

    total = round(sum(by_day_sym.values()), 4)
    return {"dates": dates, "series": series, "total": total}


def get_rotation_log(n: int = 10) -> list[dict]:
    """
    Parse last N rotation events from rotation.log.
    Looks for lines like: "🎯 Решение: X-USDT → Y-USDT"
    """
    log_path = HERE / "rotation.log"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(errors="ignore").splitlines()
    except Exception:
        return []

    events = []
    current_ts = None
    for line in lines:
        # Look for timestamp header (Smart Rotation Report)
        if "Smart Rotation Report" in line:
            # Extract timestamp  e.g. "(2026-04-23T14:07:08Z)"
            import re
            m = re.search(r"\((.*?)\)", line)
            if m:
                current_ts = m.group(1)
        elif "🎯 Решение:" in line or "Решение:" in line:
            text = line.split("Решение:", 1)[-1].strip()
            events.append({"ts": current_ts or "", "decision": text})
        elif "empty_slot" in line or "улучшение:" in line:
            if events:
                events[-1]["detail"] = line.strip()

    # Dedupe by (ts, decision) — TG fallback duplicates entries
    seen = set()
    uniq = []
    for e in events:
        key = (e.get("ts", ""), e.get("decision", ""))
        if key in seen:
            # если дубль несёт detail, а первый без detail — обогатим
            if e.get("detail"):
                for u in uniq:
                    if (u.get("ts"), u.get("decision")) == key and not u.get("detail"):
                        u["detail"] = e["detail"]
                        break
            continue
        seen.add(key)
        uniq.append(e)

    return uniq[-n:][::-1]  # newest first


def get_liq_distance() -> list[dict]:
    """Read liq_monitor_state.json + compute current distances via state files."""
    # Use saved snapshot from liq_monitor if recent, otherwise empty
    # Simpler: read state files for each bot, get entry, get mark from state, skip live API
    # For accuracy, we could import liq_monitor functions — but that needs API keys.
    # Here: read from latest liq_monitor.log (plain text parse)
    log_path = HERE / "liq_monitor.log"
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(errors="ignore").splitlines()
    except Exception:
        return []

    # Find last scan block
    last_block_idx = None
    for i in range(len(lines) - 1, -1, -1):
        if "liq_monitor scan" in lines[i]:
            last_block_idx = i
            break
    if last_block_idx is None:
        return []

    results = []
    for line in lines[last_block_idx + 1:last_block_idx + 12]:
        line = line.rstrip()
        if not line.startswith("  "):
            break
        # Format: "  arb_bot    RIVER-USDT      ✅  44.4%  mark=$5.979000    liq=$8.638000"
        parts = line.split()
        if len(parts) < 6:
            continue
        # status emoji + pct are 3rd+4th
        try:
            bot = parts[0]
            symbol = parts[1]
            emoji = parts[2]
            pct = float(parts[3].rstrip("%"))
            mark = parts[4].split("=")[-1] if "mark" in parts[4] else ""
            liq  = parts[5].split("=")[-1] if "liq"  in parts[5] else ""
            results.append({
                "bot": bot,
                "symbol": symbol,
                "status": emoji,
                "distance_pct": pct,
                "mark": mark,
                "liq": liq,
            })
        except Exception:
            continue

    return results


# ─── Routes ─────────────────────────────────────────────────────────────────


def get_summary() -> dict:
    """Сводка по ARB портфелю: баланс, equity, PnL, funding, APR."""
    import subprocess, csv as _csv, sys as _sys
    import re as _re, traceback
    from datetime import datetime, timezone

    result = {
        "spot_usdt": 0.0, "perp_equity": 0.0, "perp_avail": 0.0,
        "total_equity": 0.0, "capital_at_work": 0.0, "unreal_pnl": 0.0,
        "funding_today": 0.0, "funding_total": 0.0,
        "active_bots": 0, "avg_apr": 0.0,
        "goal": 3000.0, "progress_pct": 0.0,
        "positions": [],
    }

    # 1. Балансы через bingx_transfer
    try:
        _sys.path.insert(0, "/root/bingx-bot/patches")
        from bingx_transfer import get_wallet_balances
        bal = get_wallet_balances()
        result["spot_usdt"] = bal.get("spot", 0.0)
        result["perp_equity"] = bal.get("perp_equity", 0.0)
        result["perp_avail"] = bal.get("perp_avail", 0.0)
    except Exception as e:
        print(f"[get_summary] balance err: {e}", file=_sys.stderr, flush=True)

    # 2. Состояние ботов — парсим --status
    bots = [("arb_bot", 160), ("arb_bot2", 146), ("arb_bot3", 80),
            ("arb_bot4", 80), ("arb_bot5", 80), ("arb_bot6", 120)]
    total_apr_weighted = 0.0
    total_budget_active = 0.0
    total_unreal = 0.0
    total_earned = 0.0
    active = 0

    for bot_name, budget in bots:
        try:
            proc = subprocess.run(
                ["/usr/bin/python3", f"/root/bingx-bot/{bot_name}.py", "--status"],
                capture_output=True, text=True, timeout=20,
                cwd="/root/bingx-bot",
            )
            out = proc.stdout or ""
            if not out.strip():
                print(f"[get_summary] {bot_name}: empty stdout, stderr={proc.stderr[:200]}",
                      file=_sys.stderr, flush=True)

            symbol = ""
            rate_apy = 0.0
            spot_pnl = 0.0
            perp_pnl = 0.0
            earned = 0.0
            is_open = "ПОЗИЦИЯ ОТКРЫТА" in out

            m = _re.search(r"ARB\d*\s*BOT\s*[\u2014\-]\s*([\w-]+)", out)
            if m: symbol = m.group(1)

            m = _re.search(r"~([\d.]+)%\s*APY", out)
            if m: rate_apy = float(m.group(1))

            m = _re.search(r"Спот LONG[^$]*\$([+-]?[\d.]+)", out)
            if m: spot_pnl = float(m.group(1))

            m = _re.search(r"Перп PnL\s*:\s*\$([+-]?[\d.]+)", out)
            if m: perp_pnl = float(m.group(1))

            m = _re.search(r"Заработано:\s*\$([\d.]+)", out)
            if m: earned = float(m.group(1))

            result["positions"].append({
                "bot": bot_name, "symbol": symbol, "budget": budget,
                "apr": rate_apy, "is_open": is_open,
                "spot_pnl": spot_pnl, "perp_pnl": perp_pnl,
                "net_pnl": round(spot_pnl + perp_pnl, 4), "earned": earned,
            })

            if is_open:
                active += 1
                total_apr_weighted += rate_apy * budget
                total_budget_active += budget
                total_unreal += (spot_pnl + perp_pnl)
                total_earned += earned
        except Exception as e:
            print(f"[get_summary] {bot_name} err: {e}", file=_sys.stderr, flush=True)
            traceback.print_exc(file=_sys.stderr)

    result["active_bots"] = active
    result["capital_at_work"] = total_budget_active
    result["unreal_pnl"] = round(total_unreal, 2)
    result["funding_total"] = round(total_earned, 4)
    if total_budget_active > 0:
        result["avg_apr"] = round(total_apr_weighted / total_budget_active, 1)

    # 3. Funding за сегодня — из funding_history.csv
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        csv_path = Path("/root/bingx-bot/funding_history.csv")
        if csv_path.exists():
            with csv_path.open() as f:
                r = _csv.DictReader(f)
                for row in r:
                    if row.get("timestamp_utc", "").startswith(today):
                        result["funding_today"] += float(row.get("amount_usdt", 0) or 0)
        result["funding_today"] = round(result["funding_today"], 4)
    except Exception as e:
        print(f"[get_summary] funding err: {e}", file=_sys.stderr, flush=True)

    # 4. Total equity
    result["total_equity"] = round(
        result["spot_usdt"] + result["perp_equity"], 2
    )
    result["progress_pct"] = round(min(100.0, result["total_equity"] / result["goal"] * 100), 1)

    return result


@arb_bp.route("/api/summary")
@login_required
def api_summary():
    return jsonify(get_summary())


@arb_bp.route("/api/graveyard")
@login_required
def api_graveyard():
    return jsonify(get_graveyard())


@arb_bp.route("/api/funding_history")
@login_required
def api_funding_history():
    return jsonify(get_funding_history())


@arb_bp.route("/api/rotation_log")
@login_required
def api_rotation_log():
    return jsonify(get_rotation_log())


@arb_bp.route("/api/liq_distance")
@login_required
def api_liq_distance():
    return jsonify(get_liq_distance())


# ─── Block 5: Fleet State (file-based, API-independent) ────────────────────
# Survives BingX outage / internet jamming — reads only state files on disk.

@arb_bp.route("/api/fleet_state")
@login_required
def api_fleet_state():
    """Return file-based fleet snapshot. Fast (~1ms) and works offline."""
    try:
        from fleet_state import fleet_snapshot, health_snapshot
        snap = fleet_snapshot()
        snap["health"] = health_snapshot()
        return jsonify(snap)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


ARB_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>ARB Monitor — BingX Bot</title>
<meta http-equiv="refresh" content="60">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d1117; color:#e6edf3; font-family:'Segoe UI',sans-serif; padding:20px; }
h1 { color:#58a6ff; font-size:22px; margin-bottom:8px; }
h2 { color:#58a6ff; font-size:14px; margin:18px 0 8px 0; letter-spacing:0.5px; text-transform:uppercase; }
.subtitle { color:#8b949e; font-size:12px; margin-bottom:20px; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:15px; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:15px; }
.card-full { grid-column:1/-1; }
table { width:100%; border-collapse:collapse; font-size:12px; }
th { background:#21262d; padding:8px 10px; text-align:left; color:#8b949e; font-weight:500; border-bottom:1px solid #30363d; }
td { padding:8px 10px; border-bottom:1px solid #21262d; color:#e6edf3; }
tr:hover td { background:#1c2128; }
.green { color:#3fb950; }
.red { color:#f85149; }
.yellow { color:#e3b341; }
.blue { color:#58a6ff; }
.muted { color:#8b949e; font-size:11px; }
.empty { color:#8b949e; font-style:italic; padding:12px 10px; }
.back { color:#58a6ff; text-decoration:none; }
.back:hover { text-decoration:underline; }
.tag { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:500; }
.tag-weak { background:#3d2020; color:#f85149; }
.tag-api  { background:#3d2d10; color:#e3b341; }
.tag-vol  { background:#3d1420; color:#f85149; }
.bar-container { background:#21262d; height:6px; border-radius:3px; overflow:hidden; margin-top:4px; width:100px; }
.bar { height:100%; background:#3fb950; border-radius:3px; }
.bar-warn { background:#e3b341; }
.bar-crit { background:#f85149; }
canvas { max-height:260px; }

/* Phase 2+3 UI */
.global-actions { display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 20px; }
.btn-action {
    background:#21262d; border:1px solid #30363d; color:#e6edf3;
    border-radius:6px; padding:6px 12px; font-size:12px; cursor:pointer;
    font-family:inherit; transition:all .15s;
}
.btn-action:hover { background:#30363d; border-color:#58a6ff; }
.btn-action.danger { background:#f8514920; border-color:#f85149; color:#f85149; }
.btn-action.danger:hover { background:#f85149; color:white; }
.btn-action.warn { background:#d2a8ff20; border-color:#d2a8ff; color:#d2a8ff; }
.btn-action.warn:hover { background:#d2a8ff; color:#0d1117; }
.btn-action.ok { background:#3fb95020; border-color:#3fb950; color:#3fb950; }
.btn-action.ok:hover { background:#3fb950; color:#0d1117; }
.btn-action.mini { padding:3px 7px; font-size:10px; }
.btn-action:disabled { opacity:.4; cursor:wait; }

.pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px;
    background:#21262d; color:#8b949e; border:1px solid #30363d; }
.pill.paused { background:#f8514915; color:#f85149; border-color:#f85149; }
.pill.active { background:#3fb95015; color:#3fb950; border-color:#3fb950; }

/* Modal */
.modal-bg {
    position:fixed; inset:0; background:rgba(0,0,0,.75); z-index:1000;
    display:none; align-items:center; justify-content:center;
}
.modal-bg.show { display:flex; }
.modal {
    background:#161b22; border:1px solid #30363d; border-radius:12px;
    padding:24px; max-width:500px; width:92%;
}
.modal h2 { color:#e6edf3; font-size:18px; margin-bottom:12px; }
.modal p { color:#8b949e; font-size:14px; line-height:1.5; margin-bottom:8px; }
.modal .detail {
    background:#0d1117; border:1px solid #30363d; border-radius:6px;
    padding:10px 14px; margin:14px 0; font-family:monospace; font-size:13px;
    color:#58a6ff;
}
.modal .warn-text { color:#f85149; font-size:13px; margin-top:10px; }
.modal .btn-row { display:flex; gap:10px; justify-content:flex-end; margin-top:20px; }
.modal pre { max-height:260px; overflow:auto; background:#0d1117;
    padding:10px; border-radius:6px; font-size:11px; color:#8b949e;
    white-space:pre-wrap; border:1px solid #21262d; }

.log-table { width:100%; font-size:11px; margin-top:10px; }
.log-table td { padding:3px 6px; border-bottom:1px solid #21262d; }
.log-table .ok { color:#3fb950; }
.log-table .err { color:#f85149; }

.toast {
    position:fixed; bottom:24px; right:24px; z-index:2000;
    background:#161b22; border:1px solid #30363d; border-radius:8px;
    padding:12px 16px; font-size:13px; max-width:400px;
    display:none; box-shadow:0 4px 12px rgba(0,0,0,.5);
}
.toast.show { display:block; }
.toast.ok { border-color:#3fb950; color:#3fb950; }
.toast.err { border-color:#f85149; color:#f85149; }

</style>
</head>
<body>

<a href="/smc" class="back">→ SMC (архив)</a>
<h1>ARB Monitor</h1>
<p class="subtitle">Graveyard · Funding history · Rotation · Liquidation distance</p>

<!-- Phase 5: Global action bar -->
<div class="global-actions">
  <span id="rotation-pill" class="pill active">● Ротация активна</span>
  <button class="btn-action warn" onclick="confirmAction('pause-rotation','Отключить автоматическую ротацию?','Крон 4h будет закомментирован. Боты продолжат работать, но не будут переключать пары.','POST','/arb/api/action/pause-rotation')">⏸ Pause rotation</button>
  <button class="btn-action ok" onclick="confirmAction('resume-rotation','Включить ротацию обратно?','Крон 4h будет раскомментирован.','POST','/arb/api/action/resume-rotation')">▶️ Resume rotation</button>
  <button class="btn-action warn" onclick="confirmAction('force-rotate','Принудительная ротация всех ботов?','Запустится arb_tools.py --rotate-smart --apply. Плохие позиции будут закрыты и заменены на лучшие из top-5. Займёт ~30-60 сек.','POST','/arb/api/action/force-rotate')">🔁 Force rotate all</button>
  <button class="btn-action danger" onclick="confirmAction('PANIC','⚠️ ЗАКРЫТЬ ВСЕ ПОЗИЦИИ?','Все 6 ботов закроют spot + perp. Деньги вернутся на кошельки. Это КРАЙНЯЯ мера и вернёт вас в начало.','POST','/arb/api/action/panic')">🚨 PANIC</button>
  <button class="btn-action" onclick="toggleLog()">📋 Action log</button>
</div>
<div id="action-log-panel" style="display:none; background:#0d1117; border:1px solid #30363d; border-radius:8px; padding:12px; margin-bottom:20px;">
  <table class="log-table" id="action-log-tbl"><tbody></tbody></table>
</div>



<div id="summary-cards" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(160px, 1fr)); gap:12px; margin-bottom:20px;">
  <div class="card"><div class="muted">Баланс (spot+perp)</div><div id="sm-total" style="font-size:22px; font-weight:bold; color:#58a6ff;">—</div></div>
  <div class="card"><div class="muted">SPOT USDT</div><div id="sm-spot" style="font-size:22px; font-weight:bold;">—</div></div>
  <div class="card"><div class="muted">PERP equity</div><div id="sm-perp" style="font-size:22px; font-weight:bold;">—</div></div>
  <div class="card"><div class="muted">Капитал в игре</div><div id="sm-capital" style="font-size:22px; font-weight:bold; color:#e3b341;">—</div></div>
  <div class="card"><div class="muted">Нереал. PnL</div><div id="sm-unreal" style="font-size:22px; font-weight:bold;">—</div></div>
  <div class="card"><div class="muted">Funding сегодня</div><div id="sm-fund-today" style="font-size:22px; font-weight:bold; color:#3fb950;">—</div></div>
  <div class="card"><div class="muted">Funding всего</div><div id="sm-fund-total" style="font-size:22px; font-weight:bold; color:#3fb950;">—</div></div>
  <div class="card"><div class="muted">Активных ботов</div><div id="sm-active" style="font-size:22px; font-weight:bold;">—</div></div>
  <div class="card"><div class="muted">Средний APR</div><div id="sm-apr" style="font-size:22px; font-weight:bold; color:#d2a8ff;">—</div></div>
  <div class="card card-progress" style="grid-column:span 2;">
    <div class="muted">Прогресс до $3000</div>
    <div id="sm-progress-text" style="font-size:18px; font-weight:bold; margin:4px 0;">—</div>
    <div class="bar-container" style="width:100%; height:10px;"><div id="sm-progress-bar" class="bar" style="width:0%;"></div></div>
  </div>
</div>

<h2 style="margin-top:6px;">Позиции</h2>
<div class="card" style="margin-bottom:20px;">
  <table>
    <thead><tr><th>Бот</th><th>Символ</th><th>Бюджет</th><th>APR</th><th>Спот PnL</th><th>Перп PnL</th><th>Нетто</th><th>Заработано</th><th>Действия</th></tr></thead>
    <tbody id="sm-positions"><tr><td colspan="9" class="empty">Загрузка…</td></tr></tbody>
  </table>
</div>

<div class="grid">

  <div class="card">
    <h2>💀 Graveyard</h2>
    <table id="graveyard-table">
      <thead><tr><th>Symbol</th><th>Ejected</th><th>Reason</th><th></th></tr></thead>
      <tbody><tr><td colspan="4" class="empty">Загрузка...</td></tr></tbody>
    </table>
  </div>

  <div class="card">
    <h2>📉 Liquidation distance</h2>
    <table id="liq-table">
      <thead><tr><th>Bot</th><th>Symbol</th><th>Distance</th><th>Mark / Liq</th></tr></thead>
      <tbody><tr><td colspan="4" class="empty">Загрузка...</td></tr></tbody>
    </table>
  </div>

  <div class="card card-full">
    <h2>💰 Funding earned by day</h2>
    <canvas id="funding-chart"></canvas>
    <p class="muted" id="funding-total"></p>
  </div>

  <div class="card card-full">
    <h2>🔄 Последние ротации</h2>
    <table id="rotation-table">
      <thead><tr><th>Время (UTC)</th><th>Решение</th><th>Детали</th></tr></thead>
      <tbody><tr><td colspan="3" class="empty">Загрузка...</td></tr></tbody>
    </table>
  </div>

</div>

<script>
async function loadGraveyard() {
  const r = await fetch("/arb/api/graveyard");
  const data = await r.json();
  const tbody = document.querySelector("#graveyard-table tbody");
  if (!data.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">Пусто</td></tr>'; return; }
  tbody.innerHTML = data.map(r => {
    const tagClass = r.reason.includes("weak") ? "tag-weak" :
                     r.reason.includes("vol") ? "tag-vol" : "tag-api";
    const when = r.ejected_at ? r.ejected_at.slice(0,16).replace("T"," ") : "—";
    return `<tr><td class="blue">${r.symbol}</td><td class="muted">${when}</td>
            <td><span class="tag ${tagClass}">${r.reason}</span></td>
            <td><button class="btn-action mini ok" onclick="confirmAction('resurrect-' + '${r.symbol}', 'Воскресить ${r.symbol}?', 'Удалит ${r.symbol} из graveyard.json. Пара снова станет доступна для автоматической ротации.', 'POST', '/arb/api/action/resurrect/${r.symbol}', '${r.symbol}')">Вернуть</button></td></tr>`;
  }).join("");
}

async function loadLiq() {
  const r = await fetch("/arb/api/liq_distance");
  const data = await r.json();
  const tbody = document.querySelector("#liq-table tbody");
  if (!data.length) { tbody.innerHTML = '<tr><td colspan="4" class="empty">Нет данных (запусти liq_monitor)</td></tr>'; return; }
  tbody.innerHTML = data.map(r => {
    const pctClass = r.distance_pct < 15 ? "red" : r.distance_pct < 25 ? "yellow" : "green";
    const barClass = r.distance_pct < 15 ? "bar-crit" : r.distance_pct < 25 ? "bar-warn" : "";
    const width = Math.min(100, r.distance_pct);
    return `<tr>
      <td class="muted">${r.bot}</td>
      <td class="blue">${r.symbol}</td>
      <td class="${pctClass}">${r.distance_pct.toFixed(1)}%
        <div class="bar-container"><div class="bar ${barClass}" style="width:${width}%"></div></div>
      </td>
      <td class="muted">${r.mark} / ${r.liq}</td>
    </tr>`;
  }).join("");
}

async function loadRotation() {
  const r = await fetch("/arb/api/rotation_log");
  const data = await r.json();
  const tbody = document.querySelector("#rotation-table tbody");
  if (!data.length) { tbody.innerHTML = '<tr><td colspan="3" class="empty">Нет ротаций в логе</td></tr>'; return; }
  tbody.innerHTML = data.map(r => {
    const t = r.ts ? r.ts.slice(0,16).replace("T"," ") : "—";
    return `<tr><td class="muted">${t}</td><td class="blue">${r.decision}</td>
            <td class="muted">${r.detail || ""}</td></tr>`;
  }).join("");
}

async function loadFunding() {
  const r = await fetch("/arb/api/funding_history");
  const data = await r.json();
  document.getElementById("funding-total").textContent =
    "Всего за период: $" + (data.total || 0).toFixed(4);
  const palette = ["#58a6ff","#e3b341","#a371f7","#f778ba","#56d364","#f0883e","#3fb950","#f85149"];
  const datasets = Object.entries(data.series || {}).map(([sym, vals], i) => ({
    label: sym, data: vals,
    backgroundColor: palette[i % palette.length],
    stack: "funding",
  }));
  const ctx = document.getElementById("funding-chart").getContext("2d");
  new Chart(ctx, {
    type: "bar",
    data: { labels: data.dates || [], datasets },
    options: {
      plugins: { legend: { labels:{ color:"#e6edf3", font:{size:11} } } },
      scales: {
        x: { stacked:true, ticks:{ color:"#8b949e" }, grid:{ color:"#21262d" } },
        y: { stacked:true, ticks:{ color:"#8b949e" }, grid:{ color:"#21262d" } },
      },
      responsive: true,
      maintainAspectRatio: false,
    }
  });
}

loadGraveyard();
loadLiq();
loadRotation();
loadFunding();
</script>

<script>
async function loadSummary() {
  try {
    const r = await fetch("/arb/api/summary");
    if (!r.ok) return;
    const d = await r.json();
    const fmt = (v, dec=2) => (v ?? 0).toFixed(dec);
    const clr = (v) => v >= 0 ? "#3fb950" : "#f85149";

    document.getElementById("sm-total").textContent = "$" + fmt(d.total_equity);
    document.getElementById("sm-spot").textContent = "$" + fmt(d.spot_usdt);
    document.getElementById("sm-perp").textContent = "$" + fmt(d.perp_equity);
    document.getElementById("sm-capital").textContent = "$" + fmt(d.capital_at_work, 0);

    const unrealEl = document.getElementById("sm-unreal");
    unrealEl.textContent = (d.unreal_pnl >= 0 ? "+" : "") + "$" + fmt(d.unreal_pnl);
    unrealEl.style.color = clr(d.unreal_pnl);

    document.getElementById("sm-fund-today").textContent = "$" + fmt(d.funding_today, 4);
    document.getElementById("sm-fund-total").textContent = "$" + fmt(d.funding_total, 4);
    document.getElementById("sm-active").textContent = d.active_bots + "/6";
    document.getElementById("sm-apr").textContent = fmt(d.avg_apr, 1) + "%";

    document.getElementById("sm-progress-text").textContent =
      "$" + fmt(d.total_equity, 0) + " / $" + fmt(d.goal, 0) + " (" + d.progress_pct + "%)";
    const bar = document.getElementById("sm-progress-bar");
    bar.style.width = d.progress_pct + "%";
    bar.className = "bar" + (d.progress_pct >= 66 ? "" : d.progress_pct >= 33 ? " bar-warn" : " bar-crit");

    // Positions table
    const tbody = document.getElementById("sm-positions");
    if (d.positions && d.positions.length) {
      tbody.innerHTML = d.positions.map(p => {
        const netCls = p.net_pnl >= 0 ? "green" : "red";
        const sign = p.net_pnl >= 0 ? "+" : "";
        return `<tr>
          <td>${p.bot}</td>
          <td class="blue">${p.symbol || "—"}</td>
          <td>$${p.budget}</td>
          <td class="yellow">${p.apr.toFixed(1)}%</td>
          <td class="${p.spot_pnl >= 0 ? 'green' : 'red'}">${(p.spot_pnl>=0?'+':'')}$${p.spot_pnl.toFixed(2)}</td>
          <td class="${p.perp_pnl >= 0 ? 'green' : 'red'}">${(p.perp_pnl>=0?'+':'')}$${p.perp_pnl.toFixed(2)}</td>
          <td class="${netCls}">${sign}$${p.net_pnl.toFixed(2)}</td>
          <td class="green">$${p.earned.toFixed(4)}</td><td style="white-space:nowrap">${p.is_open ? `<button class="btn-action mini warn" onclick="confirmAction('topup-' + '${p.bot}', 'Auto-topup маржи?', 'Проверит ВСЕ боты и дольёт маржу тем, у кого низкий margin ratio. Безопасно.', 'POST', '/arb/api/action/topup/${p.bot}', '${p.bot} (${p.symbol})')">💰</button> <button class="btn-action mini" onclick="confirmAction('rebalance-' + '${p.bot}', 'Global rebalance?', 'Проверит drift spot/perp у ВСЕХ ботов и подравняет где >порога. Безопасно.', 'POST', '/arb/api/action/rebalance/${p.bot}', '${p.bot} (${p.symbol})')">⚖️</button> <button class="btn-action mini danger" onclick="confirmAction('exit-' + '${p.bot}', 'ЗАКРЫТЬ позицию ${p.bot}?', 'Закроет spot+perp ${p.symbol}. Funding earned ($${p.earned.toFixed(4)}) зафиксируется. Необратимо.', 'POST', '/arb/api/action/exit/${p.bot}', '${p.bot} → ${p.symbol}')">🚪</button>` : `<span style="color:#8b949e; font-size:11px">закрыт</span>`}</td>
        </tr>`;
      }).join("");
    } else {
      tbody.innerHTML = '<tr><td colspan="9" class="empty">Нет данных</td></tr>';
    }
  } catch (e) {
    console.error("summary err", e);
  }
}
loadSummary();
setInterval(loadSummary, 30000);

// ═══════════════════════════════════════════════════════════════════
// Phase 2+3+4+5: Actions, modal, toast, log
// ═══════════════════════════════════════════════════════════════════
let _pendingAction = null;

function showToast(msg, type='ok') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  setTimeout(() => t.classList.remove('show'), 5000);
}

function confirmAction(actionId, title, msg, method, url, detailStr) {
  _pendingAction = { method, url, actionId };
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-msg').textContent = msg;
  document.getElementById('modal-detail').textContent = detailStr || '';
  document.getElementById('modal-detail').style.display = detailStr ? 'block' : 'none';
  document.getElementById('modal-output').style.display = 'none';
  document.getElementById('modal-confirm').disabled = false;
  document.getElementById('modal-confirm').textContent = 'Подтвердить';

  const warn = document.getElementById('modal-warn');
  if (actionId.includes('exit') || actionId.includes('rotate') || actionId === 'PANIC') {
    warn.style.display = 'block';
    warn.textContent = '⚠️ Действие необратимо. Проверьте символ перед подтверждением.';
  } else {
    warn.style.display = 'none';
  }

  const confirmBtn = document.getElementById('modal-confirm');
  confirmBtn.className = 'btn-action ' + (actionId === 'PANIC' || actionId.includes('exit') ? 'danger' : 'warn');

  document.getElementById('action-modal').classList.add('show');
}

function closeModal() {
  document.getElementById('action-modal').classList.remove('show');
  _pendingAction = null;
}

async function executeAction() {
  if (!_pendingAction) return;
  const { method, url, actionId } = _pendingAction;
  const btn = document.getElementById('modal-confirm');
  btn.disabled = true;
  btn.textContent = 'Выполняется…';

  try {
    const r = await fetch(url, { method, headers: {'Content-Type':'application/json'} });
    const data = await r.json();
    const out = document.getElementById('modal-output');
    const pre = document.getElementById('modal-output-pre');
    out.style.display = 'block';
    pre.textContent = (data.stdout || '') + (data.stderr ? '\\n--- STDERR ---\\n' + data.stderr : '') + '\\n\\n' + JSON.stringify(data, null, 2);
    btn.textContent = data.ok ? '✅ Готово' : '❌ Ошибка';
    btn.className = 'btn-action ' + (data.ok ? 'ok' : 'danger');
    showToast((data.ok ? '✅ ' : '❌ ') + actionId + ': ' + (data.ok ? 'успех' : 'ошибка'), data.ok ? 'ok' : 'err');
    // обновляем данные через 2с
    setTimeout(() => { loadSummary(); loadGraveyard(); loadRotationStatus(); }, 2000);
  } catch (e) {
    showToast('❌ Сетевая ошибка: ' + e.message, 'err');
    btn.textContent = '❌ Ошибка сети';
    btn.disabled = false;
  }
}

async function loadRotationStatus() {
  try {
    const r = await fetch('/arb/api/action/rotation-status');
    const d = await r.json();
    const pill = document.getElementById('rotation-pill');
    if (!pill) return;
    if (d.paused) {
      pill.className = 'pill paused';
      pill.textContent = '⏸ Ротация на паузе';
    } else {
      pill.className = 'pill active';
      pill.textContent = '● Ротация активна';
    }
  } catch (e) {}
}

async function toggleLog() {
  const panel = document.getElementById('action-log-panel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    try {
      const r = await fetch('/arb/api/action/log');
      const d = await r.json();
      const tbody = document.querySelector('#action-log-tbl tbody');
      tbody.innerHTML = (d.entries || []).map(e => `
        <tr>
          <td>${e.ts.substring(11,19)}</td>
          <td>${e.ip}</td>
          <td><b>${e.action}</b></td>
          <td>${e.detail}</td>
          <td class="${e.result==='ok'?'ok':'err'}">${e.result}</td>
        </tr>`).join('') || '<tr><td colspan="5" style="color:#8b949e">Нет записей</td></tr>';
    } catch (e) {}
  } else {
    panel.style.display = 'none';
  }
}

loadRotationStatus();
setInterval(loadRotationStatus, 30000);
// ═══════════════════════════════════════════════════════════════════

</script>

<!-- Phase 2: Action Modal -->
<div id="action-modal" class="modal-bg" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <h2 id="modal-title">Подтверждение</h2>
    <p id="modal-msg">Вы уверены?</p>
    <div class="detail" id="modal-detail"></div>
    <p class="warn-text" id="modal-warn" style="display:none"></p>
    <div id="modal-output" style="display:none">
      <p style="color:#3fb950">Результат:</p>
      <pre id="modal-output-pre"></pre>
    </div>
    <div class="btn-row">
      <button class="btn-action" onclick="closeModal()" id="modal-cancel">Отмена</button>
      <button class="btn-action danger" onclick="executeAction()" id="modal-confirm">Подтвердить</button>
    </div>
  </div>
</div>

<!-- Toast notifications -->
<div id="toast" class="toast"></div>

</body>
</html>
"""


# ─── Block 5: Fleet Dashboard (mobile-friendly, file-based) ────────────────
FLEET_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARB Fleet — BingX Bot</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { background:#0d1117; color:#e6edf3; font-family:-apple-system,'Segoe UI',sans-serif; padding:12px; }
h1 { color:#58a6ff; font-size:20px; margin-bottom:6px; }
.sub { color:#8b949e; font-size:12px; margin-bottom:16px; }
.banner { padding:10px 12px; border-radius:8px; margin-bottom:14px; font-weight:500; font-size:14px; }
.banner.safe { background:#3a1414; border:1px solid #f85149; color:#f85149; }
.banner.pause { background:#3a2914; border:1px solid #e3b341; color:#e3b341; }
.banner.ok { background:#0f2a14; border:1px solid #3fb950; color:#3fb950; }
.totals { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; margin-bottom:16px; }
.totals .box { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:10px; text-align:center; }
.totals .label { color:#8b949e; font-size:11px; text-transform:uppercase; letter-spacing:0.5px; }
.totals .val { font-size:18px; font-weight:600; margin-top:4px; }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:10px; }
.card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:14px; position:relative; }
.card.open { border-left:3px solid #3fb950; }
.card.idle { border-left:3px solid #6e7681; opacity:0.7; }
.card.paused { border-left:3px solid #e3b341; }
.card.warn { border-left:3px solid #d29922; background:#1c1610; }
.card.danger { border-left:3px solid #f85149; background:#1c1010; }
.card-head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
.bot-name { font-size:14px; font-weight:600; color:#58a6ff; }
.badge { font-size:11px; padding:2px 8px; border-radius:10px; }
.b-active { background:#0f2a14; color:#3fb950; }
.b-idle   { background:#21262d; color:#8b949e; }
.b-paused { background:#3a2914; color:#e3b341; }
.b-danger { background:#3a1414; color:#f85149; }
.symbol { font-size:16px; font-weight:600; margin-bottom:8px; }
.row { display:flex; justify-content:space-between; font-size:12px; margin:3px 0; }
.row .k { color:#8b949e; }
.row .v { color:#e6edf3; font-weight:500; }
.green { color:#3fb950; }
.red   { color:#f85149; }
.yellow{ color:#e3b341; }
.muted { color:#8b949e; font-size:11px; margin-top:6px; }
.refresh { color:#58a6ff; font-size:11px; margin-top:14px; }
.actions { margin-top:10px; display:flex; gap:6px; }
.btn { padding:5px 10px; border-radius:5px; border:1px solid #30363d; background:#21262d; color:#e6edf3; font-size:11px; cursor:pointer; }
.btn:hover { background:#30363d; }
.btn.danger { border-color:#f85149; color:#f85149; }
.back { color:#58a6ff; text-decoration:none; font-size:12px; }
@media (max-width:600px) { body{padding:8px;} .totals{grid-template-columns:1fr 1fr;} .totals .box:last-child{grid-column:1/-1;} }
</style>
</head>
<body>
<a href="/" class="back">← Dashboard</a>
<h1>🤖 ARB Fleet</h1>
<div class="sub" id="updated">loading…</div>

<div id="banner"></div>

<div class="totals">
  <div class="box"><div class="label">Active</div><div class="val" id="t-active">—</div></div>
  <div class="box"><div class="label">Capital</div><div class="val" id="t-capital">—</div></div>
  <div class="box"><div class="label">Earned</div><div class="val" id="t-earned">—</div></div>
</div>

<div class="cards" id="cards"></div>
<div class="refresh">⭯ Auto-refresh каждые 30 сек</div>

<script>
function fmtMoney(v) {
  const sign = v >= 0 ? '+' : '';
  return sign + '$' + (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));
}
function fmtAge(h) {
  if (h < 1) return Math.round(h*60) + 'm';
  if (h < 24) return h.toFixed(1) + 'h';
  return (h/24).toFixed(1) + 'd';
}
async function load() {
  try {
    const r = await fetch('/arb/api/fleet_state', {credentials:'same-origin'});
    const data = await r.json();
    if (data.error) { document.getElementById('cards').innerHTML = '<div style="color:#f85149">Error: '+data.error+'</div>'; return; }

    document.getElementById('updated').textContent = 'updated ' + new Date(data.timestamp).toLocaleString();

    const banner = document.getElementById('banner');
    if (data.safe_mode) {
      banner.className = 'banner safe';
      banner.innerHTML = '🔴 SAFE MODE — все входы блокированы';
    } else if (data.pause_global) {
      banner.className = 'banner pause';
      banner.innerHTML = '⏸ Global pause до ' + data.pause_global.until_human + ' — ' + (data.pause_global.reason || '');
    } else {
      banner.className = 'banner ok';
      banner.innerHTML = '🟢 Fleet operational';
    }

    document.getElementById('t-active').textContent = data.totals.active + '/' + data.totals.n_bots;
    document.getElementById('t-capital').textContent = '$' + data.totals.capital.toFixed(0);
    const earnedEl = document.getElementById('t-earned');
    earnedEl.textContent = fmtMoney(data.totals.earned);
    earnedEl.className = 'val ' + (data.totals.earned >= 0 ? 'green' : 'red');

    const cards = document.getElementById('cards');
    cards.innerHTML = '';
    for (const b of data.bots) {
      const div = document.createElement('div');
      let cls = 'card ';
      let badge = '';
      if (b.liquidated) { cls += 'danger'; badge = '<span class="badge b-danger">LIQ</span>'; }
      else if (!b.open && b.pause) { cls += 'paused'; badge = '<span class="badge b-paused">PAUSED</span>'; }
      else if (!b.open) { cls += 'idle'; badge = '<span class="badge b-idle">idle</span>'; }
      else if (b.pause) { cls += 'paused'; badge = '<span class="badge b-paused">PAUSED</span>'; }
      else if (b.bad_periods > 0) { cls += 'warn'; badge = '<span class="badge b-paused">⚠ ' + b.bad_periods + '</span>'; }
      else { cls += 'open'; badge = '<span class="badge b-active">active</span>'; }
      div.className = cls;

      let body = '<div class="card-head"><span class="bot-name">bot' + b.n + '</span>' + badge + '</div>';
      if (b.open) {
        body += '<div class="symbol">' + (b.symbol || '—') + '</div>';
        body += '<div class="row"><span class="k">Capital</span><span class="v">$' + (b.spot_budget + b.perp_margin).toFixed(0) + '</span></div>';
        body += '<div class="row"><span class="k">Funding APY</span><span class="v ' + (b.funding_apy_pct >= 0 ? 'green' : 'red') + '">' + b.funding_apy_pct.toFixed(1) + '%</span></div>';
        body += '<div class="row"><span class="k">Age</span><span class="v">' + fmtAge(b.age_hours) + '</span></div>';
        body += '<div class="row"><span class="k">Earned</span><span class="v ' + (b.total_earned_usdt >= 0 ? 'green' : 'red') + '">' + fmtMoney(b.total_earned_usdt) + '</span></div>';
        body += '<div class="row"><span class="k">Leverage</span><span class="v">' + b.leverage + 'x</span></div>';
        if (b.bad_periods > 0) body += '<div class="muted yellow">⚠ bad_periods: ' + b.bad_periods + '</div>';
      } else {
        body += '<div class="muted">no open position</div>';
      }
      if (b.pause) {
        body += '<div class="muted yellow">⏸ до ' + b.pause.until_human + ' (' + (b.pause.reason || '') + ')</div>';
      }
      if (b.last_check) body += '<div class="muted">last check: ' + new Date(b.last_check).toLocaleTimeString() + '</div>';
      div.innerHTML = body;
      cards.appendChild(div);
    }
  } catch (e) {
    document.getElementById('cards').innerHTML = '<div style="color:#f85149">Fetch error: '+e.message+'</div>';
  }
}
load();
setInterval(load, 30000);
</script>
</body>
</html>
"""


@arb_bp.route("/fleet")
@login_required
def arb_fleet_page():
    """Block 5: mobile-friendly fleet dashboard. Reads file state — works offline."""
    return render_template_string(FLEET_HTML)


@arb_bp.route("/")
@login_required
def arb_page():
    return render_template_string(ARB_HTML)


# ═══════════════════════════════════════════════════════════════════
# PHASE 1: Action endpoints (exit/rebalance/topup/rotate/panic/pause)
# ═══════════════════════════════════════════════════════════════════
import subprocess as _ap_sub
import json as _ap_json
from pathlib import Path as _ap_Path
from datetime import datetime as _ap_dt, timezone as _ap_tz
from flask import request as _ap_req, jsonify as _ap_jsonify

_AP_LOG = _ap_Path("/root/bingx-bot/dashboard_actions.log")
_AP_BOT_SCRIPTS = {
    "arb_bot":  "/root/bingx-bot/arb_bot.py",
    "arb_bot2": "/root/bingx-bot/arb_bot2.py",
    "arb_bot3": "/root/bingx-bot/arb_bot3.py",
    "arb_bot4": "/root/bingx-bot/arb_bot4.py",
    "arb_bot5": "/root/bingx-bot/arb_bot5.py",
    "arb_bot6": "/root/bingx-bot/arb_bot6.py",
}
_AP_ROTATE_FLAG = _ap_Path("/root/bingx-bot/ROTATION_PAUSED.flag")
_AP_GRAVEYARD = _ap_Path("/root/bingx-bot/rotation_graveyard.json")


def _ap_log(action: str, detail: str, result: str = "ok", ip: str = "?"):
    try:
        ts = _ap_dt.now(_ap_tz.utc).isoformat()
        _AP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _AP_LOG.open("a") as f:
            f.write(f"{ts}\t{ip}\t{action}\t{detail}\t{result}\n")
    except Exception:
        pass


def _ap_run(cmd: list, timeout: int = 120) -> dict:
    try:
        p = _ap_sub.run(cmd, capture_output=True, text=True,
                        timeout=timeout, cwd="/root/bingx-bot")
        return {
            "ok": p.returncode == 0,
            "rc": p.returncode,
            "stdout": (p.stdout or "")[-4000:],
            "stderr": (p.stderr or "")[-2000:],
        }
    except _ap_sub.TimeoutExpired:
        return {"ok": False, "rc": -1, "stdout": "", "stderr": f"TIMEOUT {timeout}s"}
    except Exception as e:
        return {"ok": False, "rc": -2, "stdout": "", "stderr": str(e)}


def _ap_ip():
    try:
        return _ap_req.headers.get("X-Forwarded-For", _ap_req.remote_addr or "?")
    except Exception:
        return "?"


@arb_bp.route("/api/action/exit/<bot>", methods=["POST"])
def api_action_exit(bot):
    """Закрыть позицию одного бота."""
    script = _AP_BOT_SCRIPTS.get(bot)
    if not script:
        return _ap_jsonify({"ok": False, "err": f"unknown bot: {bot}"}), 400
    r = _ap_run(["/usr/bin/python3", script, "--exit"], timeout=90)
    _ap_log("exit", bot, "ok" if r["ok"] else "err", _ap_ip())
    return _ap_jsonify(r), 200 if r["ok"] else 500


@arb_bp.route("/api/action/rebalance/<bot>", methods=["POST"])
def api_action_rebalance(bot):
    """Запустить rebalance (глобально). arb_tools.py --rebalance обрабатывает все."""
    r = _ap_run(["/usr/bin/python3", "/root/bingx-bot/arb_tools.py", "--rebalance"], timeout=120)
    _ap_log("rebalance", bot + " (global)", "ok" if r["ok"] else "err", _ap_ip())
    return _ap_jsonify(r), 200 if r["ok"] else 500


@arb_bp.route("/api/action/topup/<bot>", methods=["POST"])
def api_action_topup(bot):
    """Запустить auto-topup (глобально для всех ботов с низкой маржой).
    arb_tools.py --topup сам определит кому нужно доливать.
    Параметр bot остаётся для логирования."""
    r = _ap_run(["/usr/bin/python3", "/root/bingx-bot/arb_tools.py", "--topup"], timeout=120)
    _ap_log("topup", bot + " (global)", "ok" if r["ok"] else "err", _ap_ip())
    return _ap_jsonify(r), 200 if r["ok"] else 500


@arb_bp.route("/api/action/force-rotate", methods=["POST"])
def api_action_force_rotate():
    """Принудительный --rotate-smart --apply для ВСЕХ botов."""
    r = _ap_run(["/usr/bin/python3", "/root/bingx-bot/arb_tools.py",
                 "--rotate-smart", "--apply"], timeout=180)
    _ap_log("force-rotate", "all", "ok" if r["ok"] else "err", _ap_ip())
    return _ap_jsonify(r), 200 if r["ok"] else 500


@arb_bp.route("/api/action/panic", methods=["POST"])
def api_action_panic():
    """PANIC: закрыть ВСЁ."""
    r = _ap_run(["/usr/bin/python3", "/root/bingx-bot/arb_tools.py",
                 "--panic"], timeout=180)
    _ap_log("PANIC", "all", "ok" if r["ok"] else "err", _ap_ip())
    return _ap_jsonify(r), 200 if r["ok"] else 500


@arb_bp.route("/api/action/pause-rotation", methods=["POST"])
def api_action_pause_rotation():
    """Отключить крон rotate — комментирует строку с arb_tools.py --rotate-smart."""
    try:
        out = _ap_sub.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new = []
        changed = 0
        for line in out.splitlines():
            if "rotate-smart" in line and not line.lstrip().startswith("#"):
                new.append("# PAUSED " + line)
                changed += 1
            else:
                new.append(line)
        newtext = "\n".join(new) + "\n"
        p = _ap_sub.run(["crontab", "-"], input=newtext, text=True,
                        capture_output=True)
        _AP_ROTATE_FLAG.write_text(_ap_dt.now(_ap_tz.utc).isoformat())
        _ap_log("pause-rotation", f"lines={changed}", "ok" if p.returncode == 0 else "err", _ap_ip())
        return _ap_jsonify({"ok": p.returncode == 0, "changed": changed,
                            "stderr": p.stderr}), 200 if p.returncode == 0 else 500
    except Exception as e:
        _ap_log("pause-rotation", "error", str(e), _ap_ip())
        return _ap_jsonify({"ok": False, "err": str(e)}), 500


@arb_bp.route("/api/action/resume-rotation", methods=["POST"])
def api_action_resume_rotation():
    """Включить крон обратно — раскомментирует строки с '# PAUSED '."""
    try:
        out = _ap_sub.run(["crontab", "-l"], capture_output=True, text=True).stdout
        new = []
        changed = 0
        for line in out.splitlines():
            if line.startswith("# PAUSED ") and "rotate-smart" in line:
                new.append(line[len("# PAUSED "):])
                changed += 1
            else:
                new.append(line)
        newtext = "\n".join(new) + "\n"
        p = _ap_sub.run(["crontab", "-"], input=newtext, text=True,
                        capture_output=True)
        if _AP_ROTATE_FLAG.exists():
            _AP_ROTATE_FLAG.unlink()
        _ap_log("resume-rotation", f"lines={changed}", "ok" if p.returncode == 0 else "err", _ap_ip())
        return _ap_jsonify({"ok": p.returncode == 0, "changed": changed,
                            "stderr": p.stderr}), 200 if p.returncode == 0 else 500
    except Exception as e:
        _ap_log("resume-rotation", "error", str(e), _ap_ip())
        return _ap_jsonify({"ok": False, "err": str(e)}), 500


@arb_bp.route("/api/action/rotation-status", methods=["GET"])
def api_action_rotation_status():
    """Показывает paused=True/False."""
    paused = _AP_ROTATE_FLAG.exists()
    since = None
    if paused:
        try:
            since = _AP_ROTATE_FLAG.read_text().strip()
        except Exception:
            pass
    return _ap_jsonify({"paused": paused, "since": since})


@arb_bp.route("/api/action/resurrect/<symbol>", methods=["POST"])
def api_action_resurrect(symbol):
    """Убирает символ из rotation_graveyard.json."""
    try:
        if not _AP_GRAVEYARD.exists():
            return _ap_jsonify({"ok": False, "err": "graveyard not found"}), 404
        data = _ap_json.loads(_AP_GRAVEYARD.read_text())
        # структура: может быть dict {symbol: {...}} или list [{"symbol":..}]
        removed = False
        if isinstance(data, dict):
            if symbol in data:
                del data[symbol]
                removed = True
        elif isinstance(data, list):
            before = len(data)
            data = [x for x in data if (x.get("symbol") if isinstance(x, dict) else x) != symbol]
            removed = len(data) < before
        if removed:
            _AP_GRAVEYARD.write_text(_ap_json.dumps(data, indent=2, ensure_ascii=False))
        _ap_log("resurrect", symbol, "ok" if removed else "not-found", _ap_ip())
        return _ap_jsonify({"ok": removed, "symbol": symbol})
    except Exception as e:
        _ap_log("resurrect", symbol, str(e), _ap_ip())
        return _ap_jsonify({"ok": False, "err": str(e)}), 500


@arb_bp.route("/api/action/log", methods=["GET"])
def api_action_log():
    """Последние 50 записей действий."""
    try:
        if not _AP_LOG.exists():
            return _ap_jsonify({"entries": []})
        lines = _AP_LOG.read_text().splitlines()[-50:]
        entries = []
        for line in lines:
            parts = line.split("\t")
            if len(parts) >= 5:
                entries.append({
                    "ts": parts[0], "ip": parts[1], "action": parts[2],
                    "detail": parts[3], "result": parts[4],
                })
        return _ap_jsonify({"entries": entries[::-1]})
    except Exception as e:
        return _ap_jsonify({"entries": [], "err": str(e)})

# ═══════════════════════════════════════════════════════════════════

