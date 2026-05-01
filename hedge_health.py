#!/usr/bin/env python3
"""
Block 2: Capital Protection 2.0 — hedge-health-check
====================================================
Per-position и global-portfolio мониторинг с auto-actions.
Запускается по cron каждые 5 минут.

6 conditional triggers:
  T1: Liq distance     — позиция близко к ликвидации
  T2: Hedge mismatch   — qty perp != qty spot (delta drift)
  T3: Funding flip     — funding отрицательный 3 cycles подряд
  T4: Basis blow-up    — perp price разъехался со спот > X%
  T5: Total DD         — общий unrealized < -X% от рабочей базы
  T6: API outage       — биржа недоступна > N мин

Auto-actions включены для T1, T3, T4 (per-bot --exit), T5 (global pause new entries),
T6 (safe-mode — никаких новых действий до --resume).

Safety guards:
  - Триггер должен сработать 2 запуска подряд (10 мин стабильного нарушения)
  - max 2 auto-exits в сутки
  - 30 мин cooldown между actions
  - 3+ actions/день → полный safe-mode на 24ч

Выход из safe-mode: ручной — `python3 arb_tools.py --resume`
"""

import env_loader  # noqa: F401  (auto-loads .env)
import os, sys, time, json, hmac, hashlib, subprocess, requests, logging
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

BOT_DIR = "/root/bingx-bot"
STATE_DIR = f"{BOT_DIR}/state"
LOGS_DIR = f"{BOT_DIR}/logs"
os.makedirs(STATE_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

BASE_URL = "https://open-api.bingx.com"
AK = os.getenv("BINGX_API_KEY", "")
SK = os.getenv("BINGX_SECRET_KEY", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# ── State / log paths ─────────────────────────────────────────────────
HH_STATE = f"{STATE_DIR}/hedge_health.json"
HH_LOG = f"{LOGS_DIR}/hedge_health.log"
HH_ACTIONS_LOG = f"{LOGS_DIR}/hedge_health_actions.log"
PAUSE_GLOBAL = f"{STATE_DIR}/pause_global"
PAUSE_BOT_FMT = f"{STATE_DIR}/pause_bot{{}}"
SAFE_MODE_FILE = f"{STATE_DIR}/safe_mode"

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [HH] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(HH_LOG), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("hedge_health")

# ── Thresholds ───────────────────────────────────────────────────────
THRESHOLDS = {
    # T1: Liquidation distance (% от mark price)
    "T1_liq_warn_pct": 20.0,
    "T1_liq_critical_pct": 10.0,

    # T2: Hedge qty mismatch (drift fraction)
    "T2_drift_warn": 0.05,
    "T2_drift_critical": 0.15,

    # T3: Funding sign flip (consecutive negative cycles, 8h каждый)
    "T3_neg_warn_cycles": 1,
    "T3_neg_critical_cycles": 3,

    # T4: Basis blow-up (|perp − spot|/spot)
    "T4_basis_warn_pct": 1.0,
    "T4_basis_critical_pct": 2.0,

    # T5: Total unrealized drawdown vs working base
    "T5_dd_warn_pct": 5.0,
    "T5_dd_critical_pct": 10.0,

    # T6: API outage (минут подряд с >50% failed checks)
    "T6_outage_warn_min": 5,
    "T6_outage_critical_min": 15,

    # === Safety guards ===
    "guard_confirm_runs": 2,            # сколько запусков подряд должен висеть триггер
    "guard_max_exits_per_day": 2,
    "guard_min_cooldown_minutes": 30,
    "guard_global_kill_actions": 3,      # 3+ действий за сутки → safe-mode
    "guard_safe_mode_hours": 24,         # для T6 auto-clear не делаем (mode B = ручной)

    # === Working base ($1010 + $248 + cumulative future topups) ===
    # Динамически: capital_baseline + cumulative_topups + realized + funding − fees.
    # Проще: используем PEAK_EQUITY (lifetime max) и считаем DD от него.
    "working_base_min": 1010.0,          # старт 28.04.2026
    "working_base_extra_topups": 248.0,  # внеочередная доливка 30.04
}

# ── API helpers (clones from arb_tools) ──────────────────────────────
def _sign(p):
    return hmac.new(SK.encode(), urlencode(list(p.items())).encode(), hashlib.sha256).hexdigest()

def _ts():
    return int(time.time() * 1000)

def _get(path, p=None, timeout=10):
    p = dict(p or {})
    p["timestamp"] = _ts()
    p["signature"] = _sign(p)
    try:
        return requests.get(f"{BASE_URL}{path}", params=p,
                            headers={"X-BX-APIKEY": AK}, timeout=timeout).json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}

def tg_send(text, level="INFO"):
    """level: INFO / WARN / CRITICAL / RECOVERY"""
    if not TG_TOKEN or not TG_CHAT:
        log.warning(f"TG not configured: {text[:80]}")
        return
    prefix = {
        "INFO": "ℹ️",
        "WARN": "⚠️",
        "CRITICAL": "🛑",
        "RECOVERY": "✅",
    }.get(level, "")
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": f"{prefix} <b>HEDGE-HEALTH</b>\n{text}",
                  "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.error(f"TG error: {e}")

# ── State ─────────────────────────────────────────────────────────────
def load_hh_state():
    if not os.path.exists(HH_STATE):
        return {
            "trigger_runs": {},      # {trigger_key: consecutive_runs_active}
            "last_alert": {},        # {trigger_key: epoch}
            "actions_today": [],     # [{ts, trigger, bot, action, ok}]
            "peak_equity": 0.0,
            "api_failures": [],      # rolling window
            "last_check": 0,
        }
    try:
        with open(HH_STATE) as f:
            return json.load(f)
    except Exception:
        return {"trigger_runs": {}, "last_alert": {}, "actions_today": [],
                "peak_equity": 0.0, "api_failures": [], "last_check": 0}

def save_hh_state(s):
    with open(HH_STATE, "w") as f:
        json.dump(s, f, indent=2, default=str)

def is_safe_mode():
    return os.path.exists(SAFE_MODE_FILE)

def enter_safe_mode(reason):
    with open(SAFE_MODE_FILE, "w") as f:
        json.dump({
            "entered_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        }, f)
    log.error(f"SAFE-MODE entered: {reason}")
    tg_send(
        f"<b>SAFE-MODE ACTIVATED</b>\n"
        f"Причина: {reason}\n"
        f"Все действия приостановлены.\n"
        f"Выход: <code>python3 arb_tools.py --resume</code>",
        level="CRITICAL",
    )

def write_action(rec):
    with open(HH_ACTIONS_LOG, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")

# ── Bot state loaders ────────────────────────────────────────────────
def state_path(n):
    return f"{BOT_DIR}/arb_state.json" if n == 1 else f"{BOT_DIR}/arb_state{n}.json"

def load_bot_state(n):
    p = state_path(n)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None

def get_perp_position(symbol):
    d = _get("/openApi/swap/v2/user/positions", {"symbol": symbol})
    if d.get("code") != 0:
        return None
    for p in d.get("data", []):
        if abs(float(p.get("positionAmt", 0))) > 0:
            return p
    return None

def get_mark_price(symbol):
    """Defensive: data может быть dict или list[dict]."""
    d = _get("/openApi/swap/v2/quote/premiumIndex", {"symbol": symbol})
    if d.get("code") != 0:
        return 0.0
    data = d.get("data", {})
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict):
            try:
                v = float(item.get("markPrice", 0) or 0)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
    return 0.0

def get_spot_price(symbol):
    """Defensive: BingX spot ticker возвращает разные формы. Ищем первый числовой 'price'."""
    d = _get("/openApi/spot/v1/ticker/price", {"symbol": symbol})
    if d.get("code") != 0:
        return 0.0
    data = d.get("data", {})
    candidates = data if isinstance(data, list) else [data]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        # вариант 1: прямой price
        p = item.get("price") or item.get("trades", [{}])[0].get("price", 0) if isinstance(item.get("trades"), list) else item.get("price")
        try:
            v = float(p or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return 0.0

def get_spot_balance(asset):
    d = _get("/openApi/spot/v1/account/balance")
    if d.get("code") != 0:
        return 0.0
    for b in d.get("data", {}).get("balances", []):
        if b.get("asset") == asset:
            return float(b.get("free", 0)) + float(b.get("locked", 0))
    return 0.0

def get_perp_balance():
    """BingX swap balance: data.balance может быть dict (один USDT) или list — поддерживаем оба."""
    d = _get("/openApi/swap/v2/user/balance")
    if d.get("code") != 0:
        return {"balance": 0, "available": 0, "unrealized": 0}
    bal_data = d.get("data", {}).get("balance", {})
    items = bal_data if isinstance(bal_data, list) else [bal_data]
    for b in items:
        if not isinstance(b, dict):
            continue
        if b.get("asset") == "USDT" or len(items) == 1:
            return {
                "balance": float(b.get("balance", 0) or 0),
                "available": float(b.get("availableMargin", 0) or 0),
                "unrealized": float(b.get("unrealizedProfit", 0) or 0),
            }
    return {"balance": 0, "available": 0, "unrealized": 0}

def get_funding_history(symbol, limit=5):
    """Возвращает список последних funding rates (newest first)."""
    d = _get("/openApi/swap/v2/quote/fundingRate", {"symbol": symbol, "limit": limit})
    if d.get("code") != 0:
        return []
    data = d.get("data", [])
    if not isinstance(data, list):
        data = [data] if isinstance(data, dict) else []
    out = []
    for r in data:
        if isinstance(r, dict):
            try:
                out.append(float(r.get("fundingRate", 0) or 0))
            except (TypeError, ValueError):
                continue
    return out

# ── Trigger checks ───────────────────────────────────────────────────
def check_T1_liq_distance(n, state, perp_pos, mark_price):
    """Returns (level, msg). level: None / WARN / CRITICAL"""
    if not perp_pos:
        return None, ""
    liq = float(perp_pos.get("liquidationPrice", 0))
    if liq <= 0 or mark_price <= 0:
        return None, "no liq/mark"
    # SHORT: liq > mark; distance = (liq - mark) / mark
    distance_pct = abs(liq - mark_price) / mark_price * 100
    sym = state.get("symbol", "?")
    if distance_pct < THRESHOLDS["T1_liq_critical_pct"]:
        return "CRITICAL", f"bot{n} {sym}: liq=${liq:.4f} mark=${mark_price:.4f} dist={distance_pct:.2f}%"
    if distance_pct < THRESHOLDS["T1_liq_warn_pct"]:
        return "WARN", f"bot{n} {sym}: liq=${liq:.4f} mark=${mark_price:.4f} dist={distance_pct:.2f}%"
    return None, f"bot{n} {sym}: dist={distance_pct:.1f}% OK"

def check_T2_hedge_drift(n, state, perp_pos):
    if not perp_pos:
        return None, ""
    sym = state.get("symbol", "")
    base = sym.replace("-USDT", "")
    spot_qty = get_spot_balance(base)
    perp_qty = abs(float(perp_pos.get("positionAmt", 0)))
    if spot_qty <= 0 or perp_qty <= 0:
        return None, f"bot{n} {sym}: spot={spot_qty} perp={perp_qty} skip"
    drift = abs(spot_qty - perp_qty) / max(spot_qty, perp_qty)
    if drift >= THRESHOLDS["T2_drift_critical"]:
        return "CRITICAL", f"bot{n} {sym}: spot={spot_qty:.2f} perp={perp_qty:.2f} drift={drift*100:.1f}%"
    if drift >= THRESHOLDS["T2_drift_warn"]:
        return "WARN", f"bot{n} {sym}: spot={spot_qty:.2f} perp={perp_qty:.2f} drift={drift*100:.1f}%"
    return None, f"bot{n} {sym}: drift={drift*100:.2f}% OK"

def check_T3_funding_flip(n, state):
    sym = state.get("symbol", "")
    rates = get_funding_history(sym, limit=THRESHOLDS["T3_neg_critical_cycles"] + 1)
    if not rates:
        return None, f"bot{n} {sym}: no funding history"
    # Сколько последних подряд отрицательных (для SHORT-позиции отрицательный = мы платим)
    neg_streak = 0
    for r in rates:
        if r < 0:
            neg_streak += 1
        else:
            break
    if neg_streak >= THRESHOLDS["T3_neg_critical_cycles"]:
        return "CRITICAL", f"bot{n} {sym}: {neg_streak} neg cycles, last rate={rates[0]*100:.4f}%"
    if neg_streak >= THRESHOLDS["T3_neg_warn_cycles"]:
        return "WARN", f"bot{n} {sym}: {neg_streak} neg cycle, rate={rates[0]*100:.4f}%"
    return None, f"bot{n} {sym}: rate={rates[0]*100:+.4f}% OK"

def check_T4_basis(n, state, mark_price):
    sym = state.get("symbol", "")
    spot = get_spot_price(sym)
    if spot <= 0 or mark_price <= 0:
        return None, f"bot{n} {sym}: prices missing"
    basis_pct = abs(spot - mark_price) / spot * 100
    if basis_pct >= THRESHOLDS["T4_basis_critical_pct"]:
        return "CRITICAL", f"bot{n} {sym}: spot=${spot:.4f} perp=${mark_price:.4f} basis={basis_pct:.2f}%"
    if basis_pct >= THRESHOLDS["T4_basis_warn_pct"]:
        return "WARN", f"bot{n} {sym}: spot=${spot:.4f} perp=${mark_price:.4f} basis={basis_pct:.2f}%"
    return None, f"bot{n} {sym}: basis={basis_pct:.2f}% OK"

def check_T5_total_dd(working_base, total_unrealized):
    if working_base <= 0:
        return None, "no working base"
    dd_pct = abs(min(0, total_unrealized)) / working_base * 100
    if dd_pct >= THRESHOLDS["T5_dd_critical_pct"]:
        return "CRITICAL", f"unrealized=${total_unrealized:.2f} dd={dd_pct:.2f}% of base ${working_base:.0f}"
    if dd_pct >= THRESHOLDS["T5_dd_warn_pct"]:
        return "WARN", f"unrealized=${total_unrealized:.2f} dd={dd_pct:.2f}% of base ${working_base:.0f}"
    return None, f"dd={dd_pct:.2f}% OK"

def check_T6_api_outage(state):
    """Скользящее окно failures. Если >50% запросов за последние N минут провалились — outage."""
    now = time.time()
    failures = state.get("api_failures", [])
    # cleanup older than 20 minutes
    failures = [f for f in failures if now - f["ts"] < 1200]
    state["api_failures"] = failures

    # Считаем за окна 5 и 15 минут
    def fail_rate(window_sec):
        recent = [f for f in failures if now - f["ts"] < window_sec]
        if not recent:
            return 0, 0
        fails = sum(1 for f in recent if not f["ok"])
        return fails, len(recent)

    f15, t15 = fail_rate(THRESHOLDS["T6_outage_critical_min"] * 60)
    f5, t5 = fail_rate(THRESHOLDS["T6_outage_warn_min"] * 60)

    if t15 >= 5 and f15 / t15 > 0.5:
        return "CRITICAL", f"{f15}/{t15} API failures in last {THRESHOLDS['T6_outage_critical_min']}min"
    if t5 >= 3 and f5 / t5 > 0.5:
        return "WARN", f"{f5}/{t5} API failures in last {THRESHOLDS['T6_outage_warn_min']}min"
    return None, f"API ok ({sum(1 for f in failures if not f['ok'])}/{len(failures)} fails recent)"

# ── Action engine ────────────────────────────────────────────────────
def can_act(state, action_type):
    """Проверка safety guards перед выполнением действия."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_actions = [a for a in state.get("actions_today", []) if a.get("date") == today]

    if action_type == "exit":
        exits_today = [a for a in today_actions if a.get("action") == "exit"]
        if len(exits_today) >= THRESHOLDS["guard_max_exits_per_day"]:
            return False, f"max_exits_per_day reached ({len(exits_today)})"

    # Cooldown
    if today_actions:
        last_ts = max(a.get("ts", 0) for a in today_actions)
        elapsed_min = (time.time() - last_ts) / 60
        if elapsed_min < THRESHOLDS["guard_min_cooldown_minutes"]:
            return False, f"cooldown active ({elapsed_min:.1f}/{THRESHOLDS['guard_min_cooldown_minutes']}min)"

    # Global kill?
    if len(today_actions) >= THRESHOLDS["guard_global_kill_actions"]:
        return False, f"global_kill threshold reached ({len(today_actions)} actions today)"

    return True, "ok"

def record_action(state, trigger, bot, action, reason, ok):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rec = {
        "date": today,
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "trigger": trigger,
        "bot": bot,
        "action": action,
        "reason": reason,
        "ok": ok,
    }
    state.setdefault("actions_today", []).append(rec)
    # cleanup older than 7 days
    cutoff = time.time() - 7 * 86400
    state["actions_today"] = [a for a in state["actions_today"] if a.get("ts", 0) > cutoff]
    write_action(rec)

    # Проверим global kill
    today_count = sum(1 for a in state["actions_today"] if a.get("date") == today)
    if today_count >= THRESHOLDS["guard_global_kill_actions"] and not is_safe_mode():
        enter_safe_mode(f"global_kill: {today_count} actions today")

def action_exit_bot(n, state, trigger, reason):
    """Закрывает позицию бота через subprocess arb_botN.py --exit"""
    can, why = can_act(state, "exit")
    if not can:
        log.warning(f"[ACTION-SKIP] bot{n} exit: {why}")
        tg_send(f"Хотел закрыть bot{n} ({trigger}: {reason}), но safety guard: {why}", level="WARN")
        record_action(state, trigger, n, "exit_blocked", reason + f" | guard:{why}", False)
        return False

    bot_file = "arb_bot.py" if n == 1 else f"arb_bot{n}.py"
    try:
        log.error(f"[ACTION] bot{n} --exit ({trigger}: {reason})")
        r = subprocess.run(
            ["python3", os.path.join(BOT_DIR, bot_file), "--exit"],
            capture_output=True, text=True, cwd=BOT_DIR, timeout=180,
        )
        ok = r.returncode == 0
        record_action(state, trigger, n, "exit", reason, ok)
        # Pause бот на 24ч
        with open(PAUSE_BOT_FMT.format(n), "w") as f:
            json.dump({
                "until": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
                "reason": f"{trigger}: {reason}",
            }, f)
        tg_send(
            f"AUTO-EXIT bot{n} выполнен\n"
            f"Trigger: {trigger}\nReason: {reason}\n"
            f"Result: {'OK' if ok else 'FAIL'}\n"
            f"Bot paused 24h.",
            level="CRITICAL",
        )
        return ok
    except Exception as e:
        log.error(f"[ACTION-ERR] bot{n} exit: {e}")
        record_action(state, trigger, n, "exit_error", f"{reason} | err={e}", False)
        return False

def action_global_pause_entries(state, trigger, reason, hours=4):
    with open(PAUSE_GLOBAL, "w") as f:
        json.dump({
            "until": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(),
            "reason": f"{trigger}: {reason}",
            "scope": "new_entries",
        }, f)
    record_action(state, trigger, "global", "pause_entries", reason, True)
    tg_send(
        f"GLOBAL PAUSE NEW ENTRIES ({hours}h)\n"
        f"Trigger: {trigger}\nReason: {reason}",
        level="CRITICAL",
    )

# ── Trigger management ──────────────────────────────────────────────
def confirmed(state, trigger_key, level):
    """Возвращает True если триггер активен N запусков подряд."""
    runs = state.setdefault("trigger_runs", {})
    if level in ("WARN", "CRITICAL"):
        runs[trigger_key] = runs.get(trigger_key, 0) + 1
    else:
        runs.pop(trigger_key, None)
        return False
    return runs.get(trigger_key, 0) >= THRESHOLDS["guard_confirm_runs"]

def maybe_alert(state, trigger_key, level, msg, cooldown_sec=3600):
    """Отправляет alert если не спамил раньше cooldown_sec."""
    last = state.setdefault("last_alert", {})
    now = time.time()
    if now - last.get(trigger_key, 0) < cooldown_sec:
        return False
    last[trigger_key] = now
    tg_send(msg, level=level)
    return True

# ── Main check loop ──────────────────────────────────────────────────
def run_check():
    state = load_hh_state()
    state["last_check"] = time.time()

    # Гасим если safe-mode (только мониторим, без действий)
    in_safe = is_safe_mode()

    # ── собираем portfolio data
    perp_bal = get_perp_balance()
    api_ok = perp_bal["balance"] > 0 or perp_bal["available"] > 0  # если 0/0 — скорее всего outage
    state["api_failures"].append({"ts": time.time(), "ok": api_ok})

    # working base
    base = THRESHOLDS["working_base_min"] + THRESHOLDS["working_base_extra_topups"]

    total_unrealized = perp_bal.get("unrealized", 0)
    log.info(
        f"check: safe_mode={in_safe} perp_bal=${perp_bal['balance']:.2f} "
        f"avail=${perp_bal['available']:.2f} unreal=${total_unrealized:+.2f} base=${base:.0f}"
    )

    # ── per-bot triggers
    for n in range(1, 7):
        s = load_bot_state(n)
        if not s or not s.get("position_open"):
            continue
        sym = s.get("symbol", "?")
        perp_pos = get_perp_position(sym)
        if not perp_pos:
            log.info(f"bot{n} {sym}: position_open=True но perp_pos нет — skip checks")
            continue
        mark = get_mark_price(sym)

        # T1: Liq distance
        lvl, msg = check_T1_liq_distance(n, s, perp_pos, mark)
        log.info(f"T1 {msg}")
        if lvl:
            key = f"T1_bot{n}"
            confirmed_now = confirmed(state, key, lvl)
            maybe_alert(state, key, lvl, f"T1 LIQ-DISTANCE\n{msg}")
            if lvl == "CRITICAL" and confirmed_now and not in_safe:
                action_exit_bot(n, state, "T1", msg)

        # T2: Hedge drift (alert only — auto-realign in Block 3)
        lvl, msg = check_T2_hedge_drift(n, s, perp_pos)
        log.info(f"T2 {msg}")
        if lvl:
            key = f"T2_bot{n}"
            confirmed(state, key, lvl)
            maybe_alert(state, key, lvl, f"T2 HEDGE-DRIFT\n{msg}", cooldown_sec=21600)  # 6h

        # T3: Funding sign flip
        lvl, msg = check_T3_funding_flip(n, s)
        log.info(f"T3 {msg}")
        if lvl:
            key = f"T3_bot{n}"
            confirmed_now = confirmed(state, key, lvl)
            maybe_alert(state, key, lvl, f"T3 FUNDING-FLIP\n{msg}")
            if lvl == "CRITICAL" and confirmed_now and not in_safe:
                action_exit_bot(n, state, "T3", msg)

        # T4: Basis blow-up
        lvl, msg = check_T4_basis(n, s, mark)
        log.info(f"T4 {msg}")
        if lvl:
            key = f"T4_bot{n}"
            confirmed_now = confirmed(state, key, lvl)
            maybe_alert(state, key, lvl, f"T4 BASIS-BLOW\n{msg}")
            if lvl == "CRITICAL" and confirmed_now and not in_safe:
                action_exit_bot(n, state, "T4", msg)

    # ── global triggers
    # T5
    lvl, msg = check_T5_total_dd(base, total_unrealized)
    log.info(f"T5 {msg}")
    if lvl:
        key = "T5_global"
        confirmed_now = confirmed(state, key, lvl)
        maybe_alert(state, key, lvl, f"T5 TOTAL-DRAWDOWN\n{msg}")
        if lvl == "CRITICAL" and confirmed_now and not in_safe:
            action_global_pause_entries(state, "T5", msg, hours=4)

    # T6
    lvl, msg = check_T6_api_outage(state)
    log.info(f"T6 {msg}")
    if lvl:
        key = "T6_global"
        confirmed_now = confirmed(state, key, lvl)
        maybe_alert(state, key, lvl, f"T6 API-OUTAGE\n{msg}")
        if lvl == "CRITICAL" and confirmed_now and not in_safe:
            enter_safe_mode(f"T6: {msg}")

    save_hh_state(state)


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        # Просто проверка без действий
        log.info("DRY-RUN mode")
    try:
        run_check()
    except Exception as e:
        log.exception(f"check failed: {e}")
        sys.exit(1)
