#!/usr/bin/env python3
import env_loader  # noqa: F401  (auto-loads .env)
"""
Smart Rotation Engine — P2-G + P2-H + P3-I + graveyard/cooldown.

This module is imported by arb_tools.py and exposes two commands:
  --rotate-smart          dry-run: analyze and report, no trades executed
  --rotate-smart --apply  live: execute the best rotation decision

It does NOT modify arb_bot*.py constants. It calls existing
  python3 arb_botN.py --exit
  python3 arb_botN.py --enter
as subprocesses, after patching SYMBOL on disk for auto-redeploy.
"""
import os, sys, json, time, subprocess, re
from datetime import datetime, timezone, timedelta

# Block 3: v2 scoring helpers
try:
    from rotation_v2_score import (
        composite_score, adaptive_kelly_size,
        can_rotate_by_age, should_rotate_by_score,
        MIN_HOLD_HOURS as V2_MIN_HOLD_HOURS,
        ROTATION_SCORE_IMPROVEMENT as V2_SCORE_IMPROVEMENT,
    )
    V2_SCORING_AVAILABLE = True
except ImportError:
    V2_SCORING_AVAILABLE = False
    V2_MIN_HOLD_HOURS = 8.0
    V2_SCORE_IMPROVEMENT = 1.20

BOT_DIR = "/root/bingx-bot"

# ══ Config ════════════════════════════════════════════════════════════════
BOTS = [
    {"name": "arb_bot",  "file": "arb_bot.py",  "state": "arb_state.json",  "default_notional": 160, "label": "Якорь"},
    {"name": "arb_bot2", "file": "arb_bot2.py", "state": "arb_state2.json", "default_notional": 146, "label": "Клон-2"},
    {"name": "arb_bot3", "file": "arb_bot3.py", "state": "arb_state3.json", "default_notional": 80,  "label": "Клон-3"},
    {"name": "arb_bot4", "file": "arb_bot4.py", "state": "arb_state4.json", "default_notional": 80,  "label": "Клон-4"},
    {"name": "arb_bot5", "file": "arb_bot5.py", "state": "arb_state5.json", "default_notional": 80,  "label": "Клон-5"},
    {"name": "arb_bot6", "file": "arb_bot6.py", "state": "arb_state6.json", "default_notional": 120, "label": "Клон-6"},
]

# P2-G: Multi-hour funding filter
STABILITY_LOOKBACK   = 6         # проверяем последние 6 funding-периодов (48ч)
STABILITY_MIN_GOOD   = 5         # минимум 5 из 6 должны быть >= MIN_ACCEPTABLE_RATE
STABILITY_MIN_RATE   = 0.00015   # 0.015%/8ч — половина entry threshold
STABILITY_MIN_AVG    = 0.00025   # среднее по 6 периодам >= 0.025%/8ч
STABILITY_NO_FLIPS   = True      # ни одной отрицательной ставки в окне

# P2-H: Kelly-lite sizing
MIN_POSITION_USD     = 80.0
MAX_POSITION_PCT     = 0.30      # не более 30% от капитала на одну пару
MIN_APR_FLOOR_PCT    = 40.0      # пары с APR < 40% не рассматриваем (снижено с 50%)

# P3-I: Rotation decision thresholds
RATE_EXIT            = -0.00010  # ставка < −0.01% — плохая пара
RATE_UNDERPERFORM    = 0.00015   # ставка < 0.015% — слабая пара
ROTATION_IMPROVEMENT = 1.7       # новая пара должна быть на 70% лучше по APR (защита от шума)
MIN_HOLD_HOURS       = 6.0       # не ротировать пару, открытую менее N часов (защита от over-trading)
FILL_EMPTY_ANTIFLAP_MIN = 30     # FIX (Block 5.x bug #4): не делать fill_empty на этом боте
                                  # если он уже делал fill_empty <30мин назад.
                                  # Защита от петли hedge_health→exit→fill_empty→hedge_health
GRAVEYARD_COOLDOWN_H = 48        # default fallback cooldown (legacy entries)
GRAVEYARD_FILE       = f"{BOT_DIR}/rotation_graveyard.json"

# Block 6 (A): Adaptive cooldown по причине exit.
# Раньше все пары лежали 48ч, что было жёстко для weak_apr (хорошие
# пары простаивают) и мало для api_vol_lock (биржа ловит их снова).
# Reasons mapped к фиксированным бакетам через substring match (case-insensitive)—
# реальные reasons от rotation/hedge_health разные: "weak_apr", "weak",
# "low rate", "basis_high", "hedge_health: T4 basis...", "manual", etc.
GRAVEYARD_COOLDOWN_RULES = [
    # (substring_lowercased, hours)
    ("api_vol_lock",      24 * 7),  # ловили биржевой 109400 — неделя
    ("basis",             6),       # basis spike часто временный — 6ч
    ("negative",          12),      # negative funding — 12ч
    ("weak",              24),      # weak APR — возможно восстановится за 24ч
    ("underperform",      24),
    ("low_apr",           24),
    ("manual",            48),      # выйти руками = 48ч (старый default)
    ("liquidation",       48),      # была близкая ликвидация — даём время
    ("dd",                48),      # drawdown — то же
]
GRAVEYARD_DEFAULT_H = 48           # если reason не распознан

# ══ Block 7 (D): Smart funding-based exit ═════════════════════════════════
# ИДЕЯ: даже если пара ещё не "weak" по верхним порогам, она может уже жрать
# капитал (отрицательный funding) или давать столь низкий APR, что простой
# капитала на ней дороже потенциального дохода. Делаем агрессивный exit:
#   1) funding_rate < SMART_EXIT_NEGATIVE_RATE → forced_exit (negative_funding)
#   2) funding_rate < SMART_EXIT_FLOOR AND age >= SMART_EXIT_MIN_HOLD_H → forced_exit (low_apr)
# Это работает в дополнение к Priority 2 (rotate weak/bad — нужен candidate);
# forced_exit срабатывает даже когда candidates нет — освобождает капитал.
SMART_EXIT_NEGATIVE_RATE = 0.0       # < 0  → exit (платим за позицию)
SMART_EXIT_FLOOR         = 0.00010   # 0.01%/8ч ≈ 11% APR — слишком мало
SMART_EXIT_MIN_HOLD_H    = 24.0      # не выгонять low_apr пары моложе суток
                                      # (даём шанс восстановиться, плюс защита от over-trading)

# ══ Block 8: Tiered D-thresholds (агрессивная ротация по возрасту) ════════════
# ИДЕЯ Block 7 была давать один floor (0.0001 ≈ 11% APR), но это
# слишком слабо — застяли по неделе на парах с 30% APR. Block 8 вводит
# tiered пороги: чем старше позиция, тем выше ожидание APR (пары или
# выросли, или ротируем).
#
# Тирхи:
#   < 6h:   grace (только negative funding)
#   6-24h:  floor 0.0001 + требуется bad_periods >= 2
#   24-48h: floor 0.00025 (~27% APR)
#   48-96h: floor 0.00040 (~44% APR)
#   >96h:   floor 0.00055 (~60% APR)
#
# ЗАЩИТА ОТ КАСКАДА: max 3 forced_exit за один cron (выбираются худшие).
TIERED_EXIT_GRACE_AGE_H        = 6.0       # < 6h — не выходим (кроме negative funding)
TIERED_EXIT_TIERS = (
    # (max_age_h, rate_floor, label)
    (24.0,  0.00010, "young"),     # 6-24h:  требуется bad_periods >= TIERED_EXIT_YOUNG_REQUIRES_BAD
    (48.0,  0.00025, "mid"),       # 24-48h: ~27% APR
    (96.0,  0.00040, "mature"),    # 48-96h: ~44% APR
    (1e9,   0.00055, "old"),       # >96h:   ~60% APR
)
TIERED_EXIT_MAX_PER_CRON       = 3         # max forced_exit за один cron
TIERED_EXIT_YOUNG_REQUIRES_BAD = 2         # в 6-24h нужны 2+ bad_periods

# ══ Block 8: Smart top-up (авто-доливка в зрелые пары) ═════════════════
# ИДЕЯ: наращиваем капитал в стабильных высокодоходных парах (усреднение).
# Безопасность — цап 25% от total на одну пару (защита от концентрации риска).
# Плавность — траншами по $40 раз в 24ч (не одним куском).
TOP_UP_MIN_AGE_H        = 72.0       # min возраст позиции
TOP_UP_MIN_PAYMENTS     = 15         # min полученных выплат
TOP_UP_MAX_BAD_PERIODS  = 0          # ноль срывов
TOP_UP_MIN_RATE         = 0.0004     # ~44% APR (всё ещё выгодна)
TOP_UP_CAP_PCT          = 0.25       # max 25% от total capital на одну пару
TOP_UP_TRANCHE_USD      = 40.0       # размер одной доливки
TOP_UP_COOLDOWN_H       = 24.0       # min интервал между доливками
TOP_UP_LOG_FILE         = f"{BOT_DIR}/top_up_log.json"  # история доливок

# ══ Block 7 (E): Dynamic Kelly cap ════════════════════════════════════════
# ИДЕЯ: Kelly даёт base size по APR/stability, но не знает что:
#   - этот символ недавно вылетел из-за низкого APR / negative funding
#   - у этого символа отрицательный lifetime PnL (за все ротации)
# Поэтому базовый размер режется penalty-факторами (cumulative).
# Новые символы (нет графьярд-истории, нет lifetime PnL записей) — НЕ режутся:
# диагностика 01.05 показала что bot1 FIGHTID и bot5 IDOL платят БОЛЬШЕ всех.
KELLY_PENALTY_GRAVEYARD_REASONS = ("weak", "underperform", "low_apr", "negative")
KELLY_PENALTY_GRAVEYARD_DAYS    = 7
KELLY_PENALTY_GRAVEYARD_FACTOR  = 0.5    # пара недавно показала плохой funding → ½ size
KELLY_PENALTY_NEGATIVE_LIFETIME_FACTOR = 0.7  # lifetime PnL ≤ 0 → 0.7× size


def cooldown_for_reason(reason: str) -> int:
    """Вернуть hours cooldown для этой причины. Substring match, case-insensitive.

    Первое совпадение выигрывает — поэтому более специфичные правила
    раньше (api_vol_lock перед weak и т.д.).
    """
    if not reason:
        return GRAVEYARD_DEFAULT_H
    r = str(reason).lower()
    for needle, hours in GRAVEYARD_COOLDOWN_RULES:
        if needle in r:
            return hours
    return GRAVEYARD_DEFAULT_H

# Permanent blacklist — символы, на которые НИКОГДА не входим
# (постоянная блокировка, в отличие от graveyard с 48ч cooldown)
PERMANENT_BLACKLIST  = {
    "OWL-USDT",      # 2026-04-25: BingX заблокировал API на перпе (109400),
                     # 10 неудачных попыток входа, dust 1.80 шт остался на споте
}
HISTORY_FILE         = f"{BOT_DIR}/rotation_history.json"

# P2-F (reuse existing constant from bots)
MAX_SLIPPAGE_ENTER   = 0.005     # 0.5%

# Pairs hardcoded to never touch (anchor strategy)
# You can remove from this list if you want arb_bot (RIVER) to rotate too.
ANCHOR_BOTS = set()  # 01.05 откат: RIVER нестабилен (1/6 периодов)


# ══ API helpers (lightweight, no auth needed for public endpoints) ════════
import requests
BASE = "https://open-api.bingx.com"

def api_get_public(path, params=None):
    try:
        r = requests.get(f"{BASE}{path}", params=params or {}, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path) as f: return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ══ State readers ═════════════════════════════════════════════════════════
def get_active_positions():
    """Returns list of dicts describing every bot's current position."""
    positions = []
    for bot in BOTS:
        st = load_json(os.path.join(BOT_DIR, bot["state"]), default={})
        sym = st.get("symbol", "—")
        positions.append({
            **bot,
            "symbol": sym,
            "open":   st.get("position_open", False),
            "spot_budget": float(st.get("spot_budget", bot["default_notional"])),
            "entry_rate":  float(st.get("entry_rate", 0) or 0),
            "earned":      float(st.get("total_earned_usdt", 0) or 0),
            "entry_time":  st.get("entry_time", ""),
        })
    return positions


# ══ P2-G: Stability filter ════════════════════════════════════════════════
def check_funding_stability(symbol):
    """
    Returns dict(ok, reason, history, avg, positive_count).
    """
    d = api_get_public("/openApi/swap/v2/quote/fundingRate",
                       {"symbol": symbol, "limit": STABILITY_LOOKBACK})
    hist_raw = d.get("data", []) if isinstance(d, dict) else []
    if len(hist_raw) < STABILITY_LOOKBACK:
        return {"ok": False, "reason": f"только {len(hist_raw)}/{STABILITY_LOOKBACK} периодов",
                "history": [], "avg": 0, "positive_count": 0}
    rates = [float(h["fundingRate"]) for h in hist_raw]
    pos   = sum(1 for r in rates if r >= STABILITY_MIN_RATE)
    flips = sum(1 for r in rates if r < 0)
    avg   = sum(rates) / len(rates)

    if STABILITY_NO_FLIPS and flips > 0:
        return {"ok": False, "reason": f"{flips} отрицательных периодов",
                "history": rates, "avg": avg, "positive_count": pos}
    if pos < STABILITY_MIN_GOOD:
        return {"ok": False, "reason": f"только {pos}/{STABILITY_LOOKBACK} периодов >= {STABILITY_MIN_RATE*100:.3f}%",
                "history": rates, "avg": avg, "positive_count": pos}
    if avg < STABILITY_MIN_AVG:
        return {"ok": False, "reason": f"среднее {avg*100:.3f}% < {STABILITY_MIN_AVG*100:.3f}%",
                "history": rates, "avg": avg, "positive_count": pos}

    return {"ok": True, "reason": "stable", "history": rates, "avg": avg, "positive_count": pos}


# ══ Slippage check (re-used logic) ════════════════════════════════════════
def check_slippage_buy(symbol, usdt_amount):
    d = api_get_public("/openApi/spot/v1/market/depth",
                       {"symbol": symbol, "limit": 20})
    ob = d.get("data", {}) if isinstance(d, dict) else {}
    asks = ob.get("asks", [])
    levels = sorted([(float(p), float(q)) for p, q in asks], key=lambda x: x[0])
    if not levels:
        return {"ok": False, "slippage_pct": 99, "reason": "empty book"}
    best = levels[0][0]
    filled_tokens = 0.0
    filled_usdt = 0.0
    remaining = float(usdt_amount)
    for price, qty in levels:
        level_usdt = price * qty
        if level_usdt >= remaining:
            filled_tokens += remaining / price
            filled_usdt   += remaining
            remaining = 0
            break
        filled_tokens += qty
        filled_usdt   += level_usdt
        remaining     -= level_usdt
    if remaining > 0:
        return {"ok": False, "slippage_pct": 99, "reason": f"тонкая книга: {remaining:.2f} USDT не покрыты"}
    avg = filled_usdt / filled_tokens
    slip = (avg - best) / best
    return {"ok": True, "slippage_pct": slip, "best_price": best, "avg_price": avg, "reason": "ok"}


# ══ Graveyard (cool-down for recently ejected pairs) ══════════════════════
def load_graveyard():
    """Читает graveyard и выбрасывает истёкшие записи.

    Block 6 (A): использует rec['cooldown_h'] если есть, иначе падает на
    GRAVEYARD_COOLDOWN_H (48). Обратная совместимость со старыми
    записями из живого файла.
    """
    gy = load_json(GRAVEYARD_FILE, default={})
    now = datetime.now(timezone.utc)
    active = {}
    for sym, rec in gy.items():
        try:
            ejected_at = datetime.fromisoformat(rec["ejected_at"])
        except (KeyError, ValueError, TypeError):
            # битая запись — выкидываем
            continue
        cooldown_h = int(rec.get("cooldown_h", GRAVEYARD_COOLDOWN_H))
        if now - ejected_at < timedelta(hours=cooldown_h):
            active[sym] = rec
    if len(active) != len(gy):
        save_json(GRAVEYARD_FILE, active)
    return active


def add_to_graveyard(symbol, reason, cooldown_h=None):
    """Добавить пару в graveyard. cooldown_h вычисляется из reason если не задан."""
    if cooldown_h is None:
        cooldown_h = cooldown_for_reason(reason)
    gy = load_graveyard()
    gy[symbol] = {
        "ejected_at": datetime.now(timezone.utc).isoformat(),
        "reason":     reason,
        "cooldown_h": int(cooldown_h),
    }
    save_json(GRAVEYARD_FILE, gy)


# ══ Candidate search ══════════════════════════════════════════════════════
def find_candidates(excluded_symbols, min_notional=80):
    """Returns ranked list of candidate pairs that pass all filters."""
    d = api_get_public("/openApi/swap/v2/quote/premiumIndex")
    contracts = d.get("data", []) if isinstance(d, dict) else []

    # SAFETY: filter by apiStateBuy AND apiStateSell, not just status.
    # A symbol can have status=1 but apiStateBuy=False (API-locked by exchange),
    # which caused GENIUS-USDT failure on 2026-04-23.
    d2 = api_get_public("/openApi/spot/v1/common/symbols")
    spot_api_tradeable = set()
    for s in (d2.get("data", {}).get("symbols", []) if isinstance(d2, dict) else []):
        if (s.get("status") == 1
            and s.get("apiStateBuy") is True
            and s.get("apiStateSell") is True):
            spot_api_tradeable.add(s["symbol"])

    gy = load_graveyard()

    # Stage 1: raw top by current rate
    raw = []
    for c in contracts:
        sym = c.get("symbol", "")
        rate = float(c.get("lastFundingRate", 0) or 0)
        if sym in excluded_symbols or sym in gy or sym in PERMANENT_BLACKLIST: continue
        if sym not in spot_api_tradeable: continue  # filters out API-locked pairs
        if rate < 0.00030: continue   # MIN_RATE floor
        # Block 8.7: учитываем фактический интервал выплат (4h или 8h)
        interval_h = int(c.get("fundingIntervalHours", 8) or 8)
        payouts_per_day = 24 / interval_h if interval_h > 0 else 3
        raw.append({"symbol": sym, "current_rate": rate, "payouts_per_day": payouts_per_day})
    raw.sort(key=lambda x: -x["current_rate"])

    # Stage 2: deep check on top 15 candidates (stability + slippage)
    passed = []
    for c in raw[:15]:
        sym = c["symbol"]
        stab = check_funding_stability(sym)
        if not stab["ok"]:
            c["rejected_by"] = f"stability: {stab['reason']}"
            continue
        slip = check_slippage_buy(sym, min_notional)
        if not slip["ok"]:
            c["rejected_by"] = f"slippage: {slip['reason']}"
            continue
        if slip["slippage_pct"] > MAX_SLIPPAGE_ENTER:
            c["rejected_by"] = f"slippage: {slip['slippage_pct']*100:.3f}%"
            continue
        c["stability"] = stab
        c["slippage"]  = slip["slippage_pct"]
        # Block 8.7: APR на основе фактического интервала (4h=6 выплат, 8h=3 выплаты)
        c["apr_pct"]   = stab["avg"] * c.get("payouts_per_day", 3) * 365 * 100
        passed.append(c)
    return passed


# ══ P2-H: Kelly-lite sizing ═══════════════════════════════════════════════
def kelly_lite_size(candidate_apr, total_capital, all_candidate_aprs):
    """
    weight = apr / sum_apr, clamped by MIN/MAX
    """
    if candidate_apr < MIN_APR_FLOOR_PCT: return 0
    sum_apr = sum(a for a in all_candidate_aprs if a >= MIN_APR_FLOOR_PCT)
    if sum_apr <= 0: return 0
    weight    = candidate_apr / sum_apr
    size_usd  = weight * total_capital
    max_size  = total_capital * MAX_POSITION_PCT
    size_usd  = min(size_usd, max_size)
    if size_usd < MIN_POSITION_USD: return 0  # not worth the commissions
    return round(size_usd, 2)


# ══ Block 7 (D): Smart funding-based exit ═════════════════════════════════
def should_force_exit(current_rate, age_hours,
                      negative_threshold=None,
                      floor_threshold=None,
                      min_hold_h=None):
    """Нужно ли форсировать exit из этой позиции без candidate?

    Стратегия из Block 7 (D):
      1) current_rate < negative_threshold (обычно 0) → (True, 'negative_funding')
         Отрицательный funding — мы ПЛАТИМ за позицию, немедленный выход.
         min_hold_h НЕ применяется: кровотечение останавливаем сразу.
      2) current_rate < floor_threshold AND age >= min_hold_h → (True, 'low_apr')
         APR ниже порога выгоды, но даём позиции 24ч чтобы восстановиться.
      3) Иначе (False, '').

    Параметры:
      current_rate: float — текущий funding rate (доли, не %)
      age_hours:    float — возраст позиции в часах

    Возвращает: (should_exit: bool, reason: str)
    """
    if negative_threshold is None:
        negative_threshold = SMART_EXIT_NEGATIVE_RATE
    if floor_threshold is None:
        floor_threshold = SMART_EXIT_FLOOR
    if min_hold_h is None:
        min_hold_h = SMART_EXIT_MIN_HOLD_H

    # Priority 1: отрицательный funding — выходим немедленно (игнорируем min_hold)
    if current_rate < negative_threshold:
        return True, "negative_funding"

    # Priority 2: low APR — выходим только если позиция пожила достаточно
    if current_rate < floor_threshold and age_hours >= min_hold_h:
        return True, "low_apr"

    return False, ""


# ══ Block 8: Tiered D-thresholds ══════════════════════════════════════════
def should_force_exit_tiered(current_rate, age_hours, bad_periods=0,
                              tiers=None,
                              grace_age_h=None,
                              negative_threshold=None,
                              young_requires_bad=None):
    """Tiered версия should_force_exit — порог зависит от возраста позиции.

    Логика:
      1) current_rate < negative_threshold (обычно 0) → (True, 'negative_funding')
         Срабатывает в любом возрасте, ВКЛЮЧАЯ grace period (платим — выходим).
      2) age < grace_age_h → (False, '')  — кроме negative funding не трогаем.
      3) Определяем tier по возрасту: первый tier где age <= max_age_h.
      4) В "young" (6-24h) дополнительно требуется bad_periods >= young_requires_bad.
      5) current_rate < tier_floor → (True, 'tier_<name>')
      6) Иначе (False, '').

    Параметры:
      current_rate: float — текущий funding rate (доли)
      age_hours:    float — возраст позиции в часах
      bad_periods:  int   — счётчик плохих периодов (для young tier)
      tiers: tuple of (max_age_h, floor_rate, name) — sorted ascending by age

    Возвращает: (should_exit: bool, reason: str)
    """
    if tiers is None:
        tiers = TIERED_EXIT_TIERS
    if grace_age_h is None:
        grace_age_h = TIERED_EXIT_GRACE_AGE_H
    if negative_threshold is None:
        negative_threshold = SMART_EXIT_NEGATIVE_RATE
    if young_requires_bad is None:
        young_requires_bad = TIERED_EXIT_YOUNG_REQUIRES_BAD

    # Priority 1: отрицательный funding — выходим всегда (даже в grace)
    if current_rate < negative_threshold:
        return True, "negative_funding"

    # Grace period — даём позиции прижиться
    if age_hours < grace_age_h:
        return False, ""

    # Подбираем tier
    tier_floor = None
    tier_name = ""
    for max_age_h, floor_rate, name in tiers:
        if age_hours <= max_age_h:
            tier_floor = floor_rate
            tier_name = name
            break
    if tier_floor is None:
        # за пределами всех tiers — берём последний
        max_age_h, tier_floor, tier_name = tiers[-1]

    # Young tier требует подтверждения через bad_periods
    if tier_name == "young" and bad_periods < young_requires_bad:
        return False, ""

    if current_rate < tier_floor:
        return True, f"tier_{tier_name}"

    return False, ""


# ══ Block 7 (E): Dynamic Kelly cap helpers ════════════════════════════════
def _load_recent_graveyard_history(days=None, history_file=None,
                                    bad_reasons=None):
    """Читает rotation_history.json и возвращает dict {symbol: latest_bad_reason}
    для всех символов, вылетевших из-за bad_reasons в последние N дней.

    Используется apply_dynamic_kelly_penalty() чтобы резать size для символов
    с недавней негативной историей.

    При отсутствии файла или ошибке чтения — возвращает {} (fail-open: лучше
    войти полным размером, чем блокировать ротацию из-за битого history.json).
    """
    if days is None:
        days = KELLY_PENALTY_GRAVEYARD_DAYS
    if history_file is None:
        history_file = HISTORY_FILE
    if bad_reasons is None:
        bad_reasons = KELLY_PENALTY_GRAVEYARD_REASONS

    try:
        hist = load_json(history_file, default=[])
    except Exception:
        return {}
    if not isinstance(hist, list):
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    bad_lower = tuple(r.lower() for r in bad_reasons)
    result = {}

    for entry in hist:
        if not isinstance(entry, dict):
            continue
        decision = entry.get("decision") or {}
        if not isinstance(decision, dict):
            continue
        symbol = decision.get("eject_symbol")
        reason = (decision.get("eject_reason") or "").lower()
        ts_str = entry.get("timestamp", "")
        if not symbol or symbol == "—":
            continue
        # фильтр по bad_reasons (substring match)
        if not any(needle in reason for needle in bad_lower):
            continue
        # фильтр по времени
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError, TypeError):
            continue
        if ts < cutoff:
            continue
        # последняя запись выигрывает (history отсортирована хронологически)
        result[symbol] = reason
    return result


def _load_lifetime_pnl_for_symbol(symbol, lifetime_pnl_path=None):
    """Сумма earned по этому символу по всем ботам (из lifetime_pnl.json).

    Возвращает float (может быть отрицательным, нулём, положительным).
    При отсутствии записей — возвращает None («нет истории» ≠ «заработал 0»).

    fail-safe: при любой ошибке возвращает None.
    """
    if lifetime_pnl_path is None:
        lifetime_pnl_path = os.path.join(BOT_DIR, "lifetime_pnl.json")
    if not os.path.exists(lifetime_pnl_path):
        return None
    try:
        data = load_json(lifetime_pnl_path, default={})
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    bots = data.get("bots", {})
    if not isinstance(bots, dict):
        return None
    total = 0.0
    found = False
    for bot_name, bot_rec in bots.items():
        if not isinstance(bot_rec, dict):
            continue
        history = bot_rec.get("history", [])
        if not isinstance(history, list):
            continue
        for h in history:
            if not isinstance(h, dict):
                continue
            if h.get("symbol") == symbol:
                try:
                    total += float(h.get("earned", 0) or 0)
                    found = True
                except (TypeError, ValueError):
                    continue
    return total if found else None


def apply_dynamic_kelly_penalty(symbol, base_size, graveyard_history=None,
                                 lifetime_pnl_path=None):
    """Block 7 (E): режет base_size по истории этого символа.

    Пенальти cumulative (могут накладываться друг на друга):
      - символ в graveyard за weak/underperform/low_apr/negative в последние 7 дней
        → ×0.5 (KELLY_PENALTY_GRAVEYARD_FACTOR)
      - lifetime PnL для символа < 0
        → ×0.7 (KELLY_PENALTY_NEGATIVE_LIFETIME_FACTOR)

    НОВЫЕ символы (нет graveyard, нет lifetime) — НЕ режутся.
    Выбор обоснован диагностикой 01.05.2026: bot1/bot5 (FIGHTID/IDOL,
    возраст 1-2ч) платят ЛУЧШЕ всех старых позиций.

    Параметры:
      symbol: str — тикер (напр. "FIGHTID-USDT")
      base_size: float — размер от adaptive_kelly_size() / kelly_lite_size()
      graveyard_history: dict {symbol: reason} — результат _load_recent_graveyard_history
        (передаём один раз на ротацию, чтобы не читать файл N раз)
      lifetime_pnl_path: путь к lifetime_pnl.json (для тестов)

    Возвращает: (final_size: float, notes: list[str])
      notes — human-readable причины пенальти для логирования.
    """
    if base_size <= 0:
        return base_size, []
    if graveyard_history is None:
        graveyard_history = {}

    notes = []
    factor = 1.0

    # Penalty 1: недавняя плохая graveyard-история
    gy_reason = graveyard_history.get(symbol)
    if gy_reason:
        factor *= KELLY_PENALTY_GRAVEYARD_FACTOR
        notes.append(
            f"recent_graveyard:{gy_reason[:20]} ×{KELLY_PENALTY_GRAVEYARD_FACTOR}"
        )

    # Penalty 2: отрицательный lifetime PnL
    lifetime = _load_lifetime_pnl_for_symbol(symbol, lifetime_pnl_path)
    if lifetime is not None and lifetime < 0:
        factor *= KELLY_PENALTY_NEGATIVE_LIFETIME_FACTOR
        notes.append(
            f"negative_lifetime:${lifetime:.2f} ×{KELLY_PENALTY_NEGATIVE_LIFETIME_FACTOR}"
        )

    final_size = round(base_size * factor, 2)
    return final_size, notes


# ══ Rotation decision engine ══════════════════════════════════════════════
def analyze_rotation():
    """Core analysis: returns a report dict + optional action plan."""
    positions = get_active_positions()
    excluded  = {p["symbol"] for p in positions if p["open"]}

    # 1. Classify each open position
    classified = []
    for p in positions:
        if not p["open"]:
            classified.append({**p, "verdict": "closed"})
            continue
        if p["name"] in ANCHOR_BOTS:
            classified.append({**p, "verdict": "anchor-protected"})
            continue
        d = api_get_public("/openApi/swap/v2/quote/premiumIndex", {"symbol": p["symbol"]})
        cur_rate = 0
        try:
            cur_rate = float(d.get("data", {}).get("lastFundingRate", 0) or 0)
        except Exception:
            pass
        p["current_rate"] = cur_rate
        if cur_rate <= RATE_EXIT:
            verdict = "bad"
        elif cur_rate < RATE_UNDERPERFORM:
            verdict = "weak"
        else:
            verdict = "good"
        classified.append({**p, "verdict": verdict})

    # 2. Find candidates
    total_capital = sum(p["spot_budget"] for p in positions if p["open"])
    candidates = find_candidates(excluded, min_notional=MIN_POSITION_USD)

    # 3. Sizing for candidates (Block 3 v2: Adaptive Kelly with variance penalty,
    # fallback to v1 kelly_lite if rotation_v2_score not available).
    # Block 7 (E): Dynamic Kelly cap — режем base size по graveyard-истории
    # и отрицательному lifetime PnL. Читаем rotation_history.json ОДИН раз
    # на весь раунд, чтобы не дергать IO для каждого кандидата.
    _kelly_gy_history = _load_recent_graveyard_history()
    if V2_SCORING_AVAILABLE:
        for c in candidates:
            base_size = adaptive_kelly_size(
                c, total_capital, candidates,
                min_position_usd=MIN_POSITION_USD,
                max_position_pct=MAX_POSITION_PCT,
                min_apr_floor_pct=MIN_APR_FLOOR_PCT,
            )
            final_size, penalty_notes = apply_dynamic_kelly_penalty(
                c["symbol"], base_size, graveyard_history=_kelly_gy_history,
            )
            c["kelly_size_base"] = base_size
            c["kelly_size_usd"] = final_size
            c["kelly_penalties"] = penalty_notes
            # composite score by candidate's kelly size (used for ranking)
            cs = composite_score(
                rate=c["stability"].get("avg", 0),
                stability=c["stability"],
                slippage_pct=c["slippage"],
                notional_usd=c["kelly_size_usd"] or MIN_POSITION_USD,
            )
            c["composite_score"] = cs["score"]
            c["score_breakdown"] = cs
    else:
        all_aprs = [c["apr_pct"] for c in candidates]
        for c in candidates:
            base_size = kelly_lite_size(c["apr_pct"], total_capital, all_aprs)
            final_size, penalty_notes = apply_dynamic_kelly_penalty(
                c["symbol"], base_size, graveyard_history=_kelly_gy_history,
            )
            c["kelly_size_base"] = base_size
            c["kelly_size_usd"] = final_size
            c["kelly_penalties"] = penalty_notes
            c["composite_score"] = c["apr_pct"] / (1 + 100 * c["slippage"])
            c["score_breakdown"] = {}

    # rank: composite_score (выше = лучше), затем stability fallback
    candidates.sort(
        key=lambda c: (
            c["composite_score"],
            c["stability"]["positive_count"],
        ),
        reverse=True,
    )

    # 4. Rotation decision: first fill CLOSED (empty) slots, then rotate bad/weak
    # SAFETY: only consider bots that support --enter (uniform architecture).
    # Bots without --enter (legacy architecture, e.g. arb_bot2) are skipped until migrated.
    # Cache --enter support check once per run (avoid 6 subprocess calls per decision loop).
    _enter_support_cache = {b["name"]: bot_has_flag(b["file"], "--enter") for b in BOTS}
    def _bot_supports_enter(bot_name):
        return _enter_support_cache.get(bot_name, False)

    skipped_legacy = []
    decision = None

    # FIX (Block 5.x bug #4): anti-flap — не делать fill_empty на боте,
    # который недавно уже входил. hedge_health может выбивать бота из-за basis/dd,
    # и fill_empty раз в час будет ре-входить в ту же опасную пару → петля.
    # Смотрим в history: если этот бот уже делал applied=True fill_empty
    # < 30 мин назад — пропускаем, пусть оператор разберётся.
    recent_fillempty_bots = set()
    try:
        hist = load_json(HISTORY_FILE, default=[])
        cutoff = datetime.utcnow() - timedelta(minutes=FILL_EMPTY_ANTIFLAP_MIN)
        for h in hist[-30:]:
            d = h.get("decision") or {}
            if d.get("action") != "fill_empty" or not h.get("applied"):
                continue
            try:
                ts = datetime.fromisoformat(h["timestamp"].replace("Z", ""))
            except (ValueError, KeyError):
                continue
            if ts > cutoff:
                recent_fillempty_bots.add(d.get("eject_bot"))
    except Exception:
        # история недоступна — продолжаем без anti-flap (fail-open: лучше войти
        # в возможный loop, чем вообще не работать — объём проблемы ограничен)
        pass

    if candidates:
        # Priority 1: empty slots — fill them FIRST (no exit needed, just enter)
        empty_slots = [p for p in classified if p["verdict"] == "closed" and p["name"] not in ANCHOR_BOTS]
        for slot in empty_slots:
            if not _bot_supports_enter(slot["name"]):
                skipped_legacy.append(slot["name"])
                continue
            if slot["name"] in recent_fillempty_bots:
                # бот недавно входил и вывалился — скип, пусть оператор вручную
                continue
            for cand in candidates:
                if cand["apr_pct"] < MIN_APR_FLOOR_PCT:
                    continue
                # use slot's default_notional (no exit, so no existing spot_budget)
                slot_budget = slot["spot_budget"] if slot["spot_budget"] > 0 else slot["default_notional"]
                if cand["kelly_size_usd"] < slot_budget * 0.9:
                    continue
                decision = {
                    "action":       "fill_empty",
                    "eject_bot":    slot["name"],
                    "eject_symbol": "—",
                    "eject_reason": "empty_slot",
                    "new_symbol":   cand["symbol"],
                    "new_size_usd": slot_budget,
                    "candidate":    cand,
                    "slot":         slot,
                }
                break
            if decision: break

        # Priority 2: bad/weak slots — rotate them (exit + enter)
        if not decision:
            rotatable = [p for p in classified if p["verdict"] in ("bad", "weak")]
            rotatable.sort(key=lambda p: p["current_rate"])   # worst first
            for slot in rotatable:
                if not _bot_supports_enter(slot["name"]):
                    if slot["name"] not in skipped_legacy:
                        skipped_legacy.append(slot["name"])
                    continue
                # MIN_HOLD (Block 3 v2): унифицированный ISO-aware age check.
                # Раньше парсер ждал "%Y-%m-%d %H:%M UTC" и для ISO entry_time молча ротировал.
                if V2_SCORING_AVAILABLE:
                    age_ok, age_h, age_reason = can_rotate_by_age(slot, V2_MIN_HOLD_HOURS)
                    if not age_ok:
                        print(f"  [min_hold] slot {slot['name']} ({slot['symbol']}) {age_reason} — пропуск")
                        continue
                else:
                    et_str = slot.get("entry_time", "")
                    if et_str:
                        try:
                            et = datetime.strptime(et_str, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
                            age_h = (datetime.now(timezone.utc) - et).total_seconds() / 3600.0
                            if age_h < MIN_HOLD_HOURS:
                                print(f"  [min_hold] slot {slot['name']} ({slot['symbol']}) age={age_h:.1f}h < {MIN_HOLD_HOURS}h — пропуск")
                                continue
                        except Exception:
                            pass

                # Block 3 v2: вычисляем composite_score текущей позиции
                slot_score = 0.0
                if V2_SCORING_AVAILABLE:
                    slot_score = composite_score(
                        rate=slot["current_rate"],
                        stability={"history": [slot["current_rate"]], "positive_count": 1 if slot["current_rate"] > 0 else 0},
                        slippage_pct=0.001,  # оценочный slip для существующей позиции (не входим заново)
                        notional_usd=slot["spot_budget"],
                    )["score"]

                for cand in candidates:
                    # Block 3 v2: soft threshold по composite_score вместо жёсткого APR-ratio.
                    if V2_SCORING_AVAILABLE:
                        cand_score = cand.get("composite_score", 0)
                        do_rotate, ratio, why = should_rotate_by_score(
                            slot_score, cand_score, V2_SCORE_IMPROVEMENT
                        )
                        if not do_rotate:
                            print(f"  [score_gate] {slot['name']} {cand['symbol']}: {why}")
                            continue
                    else:
                        slot_apr = slot["current_rate"] * 3 * 365 * 100
                        if cand["apr_pct"] < max(MIN_APR_FLOOR_PCT, slot_apr * ROTATION_IMPROVEMENT):
                            continue
                    # Kelly size must be >= current slot size (don't downsize during rotation)
                    if cand["kelly_size_usd"] < slot["spot_budget"] * 0.9:
                        continue
                    decision = {
                        "action":       "rotate",
                        "eject_bot":    slot["name"],
                        "eject_symbol": slot["symbol"],
                        "eject_reason": slot["verdict"],
                        "new_symbol":   cand["symbol"],
                        "new_size_usd": slot["spot_budget"],  # keep same size for now
                        "candidate":    cand,
                        "slot":         slot,
                    }
                    break
                if decision: break

    # Priority 3 (Block 8): TIERED FORCED EXIT — выходим без candidate по tiered порогам
    # Чем старше позиция, тем выше floor APR. <6h grace; 6-24h требует bad_periods≥2;
    # >24h — хордковый floor (27-60% APR в зависимости от возраста).
    # Срабатывает ТОЛЬКО если выше ничего не выбрано (fill_empty/rotate приоритетнее).
    # Архитектурно analyze возвращает ОДНО decision за cron — это и есть натуральный cap.
    # TIERED_EXIT_MAX_PER_CRON=3 зарезервирован для будущего батчевого режима.
    if not decision:
        forced_candidates = []
        for slot in classified:
            if not slot.get("open"):
                continue
            if slot["name"] in ANCHOR_BOTS:
                continue
            if not _bot_supports_enter(slot["name"]):
                continue
            cur_rate = slot.get("current_rate", 0)
            bad_periods = int(slot.get("bad_periods", 0) or 0)
            # возраст позиции
            age_h = 0.0
            if V2_SCORING_AVAILABLE:
                _ok, age_h, _r = can_rotate_by_age(slot, 0.0)  # min_hold=0 чтобы получить возраст
            else:
                et_str = slot.get("entry_time", "")
                if et_str:
                    try:
                        et = datetime.strptime(et_str, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
                        age_h = (datetime.now(timezone.utc) - et).total_seconds() / 3600.0
                    except (ValueError, TypeError):
                        try:
                            et = datetime.fromisoformat(et_str.replace("Z", "+00:00"))
                            if et.tzinfo is None:
                                et = et.replace(tzinfo=timezone.utc)
                            age_h = (datetime.now(timezone.utc) - et).total_seconds() / 3600.0
                        except Exception:
                            age_h = 0.0
            # Block 8: tiered пороги вместо фиксированного SMART_EXIT_FLOOR
            should_exit, reason = should_force_exit_tiered(cur_rate, age_h, bad_periods)
            if should_exit:
                forced_candidates.append((slot, cur_rate, age_h, reason))
        # выбираем худшего кандидата: сначала negative_funding, потом самый низкий rate
        if forced_candidates:
            # priority order: negative_funding вперёд, внутри группы — по возрастающему rate
            forced_candidates.sort(
                key=lambda x: (0 if x[3] == "negative_funding" else 1, x[1])
            )
            # cap на будущее: обрезаем список (хранится в decision для видимости)
            top_forced = forced_candidates[:TIERED_EXIT_MAX_PER_CRON]
            slot, cur_rate, age_h, reason = top_forced[0]
            decision = {
                "action":       "forced_exit",
                "eject_bot":    slot["name"],
                "eject_symbol": slot["symbol"],
                "eject_reason": reason,  # 'negative_funding' / 'tier_young' / 'tier_mid' / ...
                "new_symbol":   None,
                "new_size_usd": 0,
                "candidate":    None,
                "slot":         slot,
                "current_rate": cur_rate,
                "age_hours":    age_h,
                "forced_queue_size": len(top_forced),
            }

    # Priority 4 (Block 8 Часть 2): SMART TOP-UP — доливка в зрелые выгодные
    # Срабатывает ТОЛЬКО если выше ничего не выбрано: вместо простоя свободного спота
    # вкладываем в пару с age >=72h, payments >=15, bad=0, rate >=0.0004 (~44% APR).
    # Транш $40, cap 25%, cooldown 24h — см. top_up.py.
    if not decision:
        try:
            import top_up
            # синхронизируем константы (rotation — источник правды)
            top_up.TOP_UP_MIN_AGE_H        = TOP_UP_MIN_AGE_H
            top_up.TOP_UP_MIN_PAYMENTS     = TOP_UP_MIN_PAYMENTS
            top_up.TOP_UP_MAX_BAD_PERIODS  = TOP_UP_MAX_BAD_PERIODS
            top_up.TOP_UP_MIN_RATE         = TOP_UP_MIN_RATE
            top_up.TOP_UP_CAP_PCT          = TOP_UP_CAP_PCT
            top_up.TOP_UP_TRANCHE_USD      = TOP_UP_TRANCHE_USD
            top_up.TOP_UP_COOLDOWN_H       = TOP_UP_COOLDOWN_H
            top_up.TOP_UP_LOG_FILE         = TOP_UP_LOG_FILE

            # собираем позиции в формате top_up.select_topup_candidate
            tu_positions = []
            for s in classified:
                if not s.get("open"):
                    continue
                tu_positions.append({
                    "name":         s["name"],
                    "symbol":       s.get("symbol"),
                    "current_rate": s.get("current_rate", 0),
                    "state":        s.get("raw_state") or s,
                })
            free_spot = float(globals().get("_LAST_FREE_SPOT_USD", 0) or 0)
            cand = top_up.select_topup_candidate(
                tu_positions, total_capital, free_spot,
                tranche_usd=TOP_UP_TRANCHE_USD,
            )
            if cand:
                decision = {
                    "action":         "top_up",
                    "eject_bot":      cand["bot"],
                    "eject_symbol":   cand["symbol"],
                    "eject_reason":   "top_up",
                    "new_symbol":     None,
                    "new_size_usd":   cand["new_budget"],
                    "current_budget": cand["current_budget"],
                    "tranche":        cand["tranche"],
                    "current_rate":   cand["current_rate"],
                    "candidate":      None,
                    "slot":           None,
                }
        except Exception as _tu_err:
            # fail-open: топ-ап это бонус, не блокируем ротацию
            print(f"[top_up] error: {_tu_err}", file=sys.stderr)

    return {
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "positions":  classified,
        "candidates": candidates[:10],
        "total_capital": total_capital,
        "decision":   decision,
        "graveyard":  load_graveyard(),
        "skipped_legacy": skipped_legacy,
    }


# ══ Executor ══════════════════════════════════════════════════════════════
def patch_symbol_in_bot(bot_file, new_symbol, state_file=None):
    """Rewrite the SYMBOL constant in a bot file AND sync the state JSON.
    Без sync state: бот пишет логи "Покупаем PTB" хотя реально торгует CLANKER (баг #4 fix включает label fix отдельно).
    """
    path = os.path.join(BOT_DIR, bot_file)
    with open(path) as f: src = f.read()
    new_src = re.sub(r'^SYMBOL\s*=\s*"[^"]+"', f'SYMBOL      = "{new_symbol}"', src,
                     count=1, flags=re.MULTILINE)
    if new_src == src:
        # SYMBOL уже совпадает с целевым — это не ошибка, просто patch не нужен.
        # Но state всё равно синхронизируем (см. ниже).
        print(f"  [INFO] SYMBOL в {bot_file} уже = {new_symbol}, patch не требуется")
    else:
        # Backup + write только если реально меняли
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(f"{path}.bak_rot_{stamp}", "w") as f: f.write(src)
        with open(path, "w") as f: f.write(new_src)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # FIX #3: синхронизируем state файл — обнуляем позицию и ставим новый symbol
    if state_file:
        st_path = os.path.join(BOT_DIR, state_file)
        try:
            st = load_json(st_path, default={})
            st["symbol"] = new_symbol
            st["spot_qty"] = 0.0
            st["entry_price"] = 0.0
            st["position_open"] = False
            with open(f"{st_path}.bak_rot_{stamp}", "w") as f:
                import json as _j; _j.dump(st, f, indent=2)
            save_json(st_path, st)
        except Exception as e:
            # Не падаем если state не существует — бот его создаст при --enter
            print(f"  [WARN] state sync failed for {state_file}: {e}")


def bot_has_flag(bot_file, flag):
    """Check if a bot supports a given argparse flag (e.g. '--enter')."""
    try:
        r = subprocess.run(["python3", os.path.join(BOT_DIR, bot_file), "--help"],
                           capture_output=True, text=True, cwd=BOT_DIR, timeout=15)
        return flag in (r.stdout or "")
    except Exception:
        return False


def verify_post_entry(bot_file, expected_symbol, state_file):
    """
    CRITICAL guard-rail: after an enter attempt, verify that the new position
    is actually delta-neutral (spot position exists + perp short exists, sizes match).
    Returns (ok, reason).
    """
    st = load_json(os.path.join(BOT_DIR, state_file), default={})
    if not st.get("position_open"):
        return False, "state.position_open=false — вход не состоялся"
    if st.get("symbol") != expected_symbol:
        return False, f"state.symbol={st.get('symbol')} != ожидаемый {expected_symbol}"
    if float(st.get("spot_qty", 0)) <= 0:
        return False, "spot_qty=0 — токены не куплены"
    return True, "OK"


def execute_rotation(decision, dry_run=True):
    """
    Performs: 1) exit old (if rotation, not fill_empty)
              2) patch SYMBOL constant in bot file
              3) enter new (via --enter if supported, else via state flip + --monitor)
              4) verify result
    Returns (ok, log_lines).
    """
    lines = []
    bot_cfg = next(b for b in BOTS if b["name"] == decision["eject_bot"])
    bot_file = bot_cfg["file"]
    state_file = bot_cfg["state"]

    is_forced_exit = decision.get("action") == "forced_exit"
    is_fill_empty = decision.get("action") == "fill_empty"
    is_top_up     = decision.get("action") == "top_up"

    # Block 8 (Часть 2): TOP-UP — обновляем spot_budget в state, бот докупит сам.
    # Не нужны basis/pause/spot проверки — это НЕ вход, это наращивание размера.
    if is_top_up:
        lines.append(f"[TOP-UP] {decision['eject_bot']}: {decision['eject_symbol']} "
                     f"${decision.get('current_budget', 0):.0f} → ${decision.get('new_size_usd', 0):.0f} "
                     f"(+${decision.get('tranche', 0):.0f}, rate={decision.get('current_rate', 0)*100:.4f}%/8ч)")
        if dry_run:
            lines.append("  [DRY-RUN] state не изменён.")
            return True, lines
        try:
            import top_up
            top_up.TOP_UP_LOG_FILE = TOP_UP_LOG_FILE
            ok, info = top_up.apply_topup(
                decision["eject_bot"],
                os.path.join(BOT_DIR, state_file),
                tranche_usd=decision.get("tranche") or TOP_UP_TRANCHE_USD,
            )
            if ok:
                lines.append(f"  ✓ spot_budget обновлён: ${info['old_budget']:.2f} → ${info['new_budget']:.2f}")
                lines.append(f"  Бот докупит разницу на следующем --monitor цикле.")
                return True, lines
            else:
                lines.append(f"  ⚠ top-up не применён: {info}")
                return False, lines
        except Exception as e:
            lines.append(f"  ⚠ top-up exception: {e}")
            return False, lines

    if is_forced_exit:
        # Block 7 (D): forced_exit — нет candidate, просто освобождаем слот.
        lines.append(f"[FORCED-EXIT] {decision['eject_bot']}: {decision['eject_symbol']} → (слот пуст)")
        lines.append(f"  Причина: {decision['eject_reason']} "
                     f"(rate={decision.get('current_rate', 0)*100:.4f}%/8ч, "
                     f"age={decision.get('age_hours', 0):.1f}ч)")
    else:
        lines.append(f"[ROTATE] {decision['eject_bot']}: {decision['eject_symbol']} → {decision['new_symbol']}")
        lines.append(f"  Причина: {decision['eject_reason']}")
        lines.append(f"  Новый APR: {decision['candidate']['apr_pct']:.1f}%, "
                     f"slippage: {decision['candidate']['slippage']*100:.3f}%, "
                     f"stability: {decision['candidate']['stability']['positive_count']}/6")

    if dry_run:
        lines.append("  [DRY-RUN] Никаких изменений не произведено.")
        return True, lines

    # FIX (Block 3 v2.1): PRE-FLIGHT PAUSE & BASIS GUARD
    # Раньше rotation патчила SYMBOL и звала --enter без проверки:
    #   1) не на паузе ли бот после Block 2 (PAUSE-GUARD блокирует entry, но SYMBOL уже сменён!)
    #   2) выживает ли новая пара базовые фильтры basis (бывает басис взлетает
    #      между candidate-scan и enter)
    # Оба случая наблюдались в live: TRADOOR basis 2.09% → пауза → была попытка
    # войти в GUA на том же боте → SYMBOL попатчился на GUA но вход заблокирован.
    # bot1 именуется как 'arb_bot' (без цифры!), bot2-6 — 'arb_bot2'..'arb_bot6'.
    # Пустая строка после .replace() = bot1.
    _eject = decision["eject_bot"]
    if _eject.startswith("arb_bot"):
        _suffix = _eject.replace("arb_bot", "")
        bot_id = int(_suffix) if _suffix else 1
    else:
        bot_id = None

    # 1. PAUSE check via pause_check.py (Block 4: single source of truth).
    # Раньше rotation дублировала логику (_is_pause_active + import из hedge_health).
    try:
        sys.path.insert(0, BOT_DIR)
        import pause_check
        safe, reason = pause_check.is_safe_mode()
        if safe:
            lines.append(f"  🚫 PRE-FLIGHT: SAFE-MODE → ротация отменена ({reason}). --resume вручную.")
            return False, lines
        paused, reason = pause_check.is_paused_global()
        if paused:
            lines.append(f"  🚫 PRE-FLIGHT: GLOBAL PAUSE активен ({reason}) → ротация отменена.")
            return False, lines
        if bot_id is not None:
            paused, reason = pause_check.is_paused_bot(bot_id)
            if paused:
                lines.append(f"  🚫 PRE-FLIGHT: {decision['eject_bot']} НА ПАУЗЕ ({reason}). Ротация отменена.")
                lines.append(f"     Для разблокировки: python3 arb_tools.py --resume (после разбора)")
                return False, lines
    except ImportError:
        lines.append("  [PRE-FLIGHT WARN] pause_check недоступен — pause guard пропущен")
    except Exception as e:
        lines.append(f"  [PRE-FLIGHT WARN] pause check failed: {e}")

    # 2. BASIS sanity check на НОВОЙ паре (basis = (perp - spot) / spot × 100%)
    # Бывают касты когда между scan и enter басис взлетает (выход funding-news, разрыв).
    # Порог совпадает с T4 в hedge_health (1.0%) — если войдём выше,
    # Block 2 выплюнет нас обратно через 5 минут. Быстрее проверить здесь.
    # Block 7: forced_exit не имеет new_symbol — басис проверять не нужно.
    BASIS_MAX_PCT = 1.0
    if is_forced_exit:
        lines.append("  [PRE-FLIGHT] basis check skipped (forced_exit — нет new_symbol)")
        # флаг чтобы обойти основной try-блок ниже
        _do_basis_check = False
    else:
        _do_basis_check = True
    try:
        if not _do_basis_check:
            raise StopIteration  # прыжок на except, чтобы пробросить basis блок
        from hedge_health import get_spot_price, get_mark_price as get_perp_mark_price
        spot_p = get_spot_price(decision["new_symbol"])
        perp_p = get_perp_mark_price(decision["new_symbol"])
        if spot_p > 0 and perp_p > 0:
            basis_pct = abs(perp_p - spot_p) / spot_p * 100.0
            if basis_pct > BASIS_MAX_PCT:
                lines.append(
                    f"  🚫 PRE-FLIGHT: {decision['new_symbol']} basis={basis_pct:.2f}% > {BASIS_MAX_PCT}% "
                    f"(spot=${spot_p:.6f} perp=${perp_p:.6f})"
                )
                lines.append("     Ротация отменена — вход ожидаемо бы был закрыт Block 2.")
                return False, lines
            lines.append(f"  [PRE-FLIGHT] basis {decision['new_symbol']}={basis_pct:.2f}% ≤ {BASIS_MAX_PCT}% ✓")
        else:
            lines.append(f"  [PRE-FLIGHT WARN] basis check skipped (spot={spot_p}, perp={perp_p})")
    except StopIteration:
        pass  # forced_exit — basis check пропущен специально
    except ImportError as e:
        lines.append(f"  [PRE-FLIGHT WARN] basis check skipped — helper missing: {e}")
    except Exception as e:
        lines.append(f"  [PRE-FLIGHT WARN] basis check failed: {e}")


    # FIX #2 v2 (Block 1): PRE-CHECK SPOT BALANCE с auto_balance integration
    # Если spot < нужно — пробуем автоматически перевести perp→spot через ensure_spot_balance().
    # Только если auto-transfer не сработал (нет средств на perp, circuit breaker, ошибка API)
    # — отменяем ротацию и шлём TG алерт. Раньше любой shortfall = stop + ручное вмешательство.
    # Block 7: forced_exit не входит в новую позицию — spot pre-check не нужен.
    if is_forced_exit:
        lines.append("  [PRE-CHECK] spot balance check skipped (forced_exit — только exit)")
    else:
        try:
            sys.path.insert(0, BOT_DIR)
            from arb_tools import get_spot_balance, tg_send
            SPOT_BUDGET_REQUIRED = 80.0  # должен совпадать с SPOT_BUDGET в ботах
            SPOT_BUFFER = 1.05            # +5% запас на slippage
            spot_usdt = get_spot_balance("USDT")
            needed = SPOT_BUDGET_REQUIRED * SPOT_BUFFER
            if spot_usdt < needed:
                lines.append(f"  [PRE-CHECK] spot=${spot_usdt:.2f} < ${needed:.2f} → пробую auto-transfer")
                ok_auto = False
                err_auto = None
                try:
                    from auto_balance import ensure_spot_balance
                    # ensure_spot_balance(needed, buffer=2.0) -> bool; сам логирует и шлёт TG при провале
                    ok_auto = ensure_spot_balance(needed, buffer=2.0)
                    spot_usdt = get_spot_balance("USDT")  # перечитываем после попытки
                    if ok_auto:
                        lines.append(f"  [AUTO-BAL] ✅ перевод выполнен → spot теперь ${spot_usdt:.2f}")
                    else:
                        lines.append(f"  [AUTO-BAL] ❌ не удалось добрать до ${needed:.2f} (spot=${spot_usdt:.2f})")
                except Exception as e:
                    err_auto = f"auto_balance error: {e}"
                    lines.append(f"  [AUTO-BAL WARN] {err_auto}")
                if not ok_auto or spot_usdt < needed:
                    msg = (
                        f"🛑 РОТАЦИЯ ОТМЕНЕНА: {decision['eject_bot']} "
                        f"{decision.get('eject_symbol', '?')} → {decision['new_symbol']}\n"
                        f"Spot USDT ${spot_usdt:.2f} < нужно ${needed:.2f}\n"
                        f"Auto-transfer fail: {err_auto or 'spot всё ещё ниже порога'}\n"
                        f"Проверь auto_balance.log. BingX UI → Assets → Transfer → Perp→Fund ${needed-spot_usdt+5:.0f} USDT"
                    )
                    lines.append(f"  🛑 PRE-CHECK FAIL после auto-transfer. Старая позиция НЕ тронута.")
                    try: tg_send(msg)
                    except Exception: pass
                    return False, lines
            else:
                lines.append(f"  [PRE-CHECK] spot=${spot_usdt:.2f} ≥ ${needed:.2f} ✓")
        except Exception as e:
            lines.append(f"  [PRE-CHECK WARN] не смог проверить spot: {e}")

    # ===== STEP 1: EXIT OLD (only for rotation, not fill_empty) =====
    if not is_fill_empty:
        # PRE-EXIT: запоминаем earned для записи в lifetime_pnl ПОСЛЕ успешного exit.
        # Читаем именно сейчас, потому что после exit некоторые боты обнуляют
        # total_earned_usdt в state.
        st_pre_exit = load_json(os.path.join(BOT_DIR, state_file), default={})
        pre_exit_earned = float(st_pre_exit.get("total_earned_usdt", 0) or 0)
        pre_exit_cycles = int(st_pre_exit.get("funding_cycles_collected", 0) or 0)

        lines.append(f"  [1/4] Exit {decision['eject_symbol']}...")
        r1 = subprocess.run(["python3", os.path.join(BOT_DIR, bot_file), "--exit"],
                            capture_output=True, text=True, cwd=BOT_DIR, timeout=180)
        lines.append(f"    stdout: {r1.stdout[-200:] if r1.stdout else '(пусто)'}")
        if r1.returncode != 0:
            lines.append(f"    ERROR rc={r1.returncode}: {r1.stderr[-200:]}")
            lines.append(f"    🛑 Ротация прервана — старая позиция возможно всё ещё открыта!")
            return False, lines

        # GUARD: verify exit actually closed the position before proceeding
        time.sleep(2)
        st_after_exit = load_json(os.path.join(BOT_DIR, state_file), default={})
        if st_after_exit.get("position_open"):
            lines.append(f"    🛑 EXIT не закрыл position_open! Отменяем вход во избежание дубликата.")
            return False, lines

        # LIFETIME PNL: фиксируем сколько эта пара заработала за всё время на этом боте.
        # Делаем это ПОСЛЕ verify exit, но ПЕРЕД graveyard и enter — чтобы при ошибке
        # дальше у нас уже остался правильный учёт.
        try:
            sys.path.insert(0, BOT_DIR)
            import lifetime_pnl
            lifetime_pnl.record_exit(
                bot_name=decision["eject_bot"],
                symbol=decision["eject_symbol"],
                earned_usdt=pre_exit_earned,
                cycles=pre_exit_cycles or None,
                reason=decision.get("eject_reason", "")[:60],
            )
            lines.append(f"  [PnL] {decision['eject_symbol']} earned ${pre_exit_earned:.4f} → lifetime_pnl.json")
        except Exception as e:
            lines.append(f"  [PnL WARN] не смог записать lifetime_pnl: {e}")

        _gv_h = cooldown_for_reason(decision["eject_reason"])
        add_to_graveyard(decision["eject_symbol"], decision["eject_reason"], cooldown_h=_gv_h)
        lines.append(f"  [1.5/4] {decision['eject_symbol']} добавлен в graveyard на {_gv_h}ч "
                     f"(reason='{decision['eject_reason'][:40]}')")

        # Block 7 (D): forced_exit — освободили слот, не входим обратно — ranny return.
        # Следующий раунд cron подберёт candidate и сделает fill_empty.
        if is_forced_exit:
            lines.append(f"  [✓] forced_exit завершён — слот {decision['eject_bot']} свободен")
            return True, lines
    else:
        # GUARD for fill_empty: verify slot actually is empty
        st_pre = load_json(os.path.join(BOT_DIR, state_file), default={})
        if st_pre.get("position_open"):
            lines.append(f"  🛑 fill_empty попытка, но position_open=true! Отменяем.")
            return False, lines
        lines.append(f"  [fill_empty] слот {decision['eject_bot']} пуст — exit не нужен")

    # ===== STEP 2: PATCH SYMBOL =====
    lines.append(f"  [2/4] Patch SYMBOL in {bot_file}...")
    try:
        patch_symbol_in_bot(bot_file, decision["new_symbol"], state_file=state_file)
    except Exception as e:
        lines.append(f"    ERROR: {e}")
        return False, lines
    time.sleep(2)

    # ===== STEP 3: ENTER NEW =====
    # Universal: if bot has --enter, use it; otherwise use --monitor (which auto-enters
    # when position_open=false). This works for both arb_bot (has --enter) and arb_bot2
    # (has --rotate but enter is embedded in --monitor logic).
    has_enter = bot_has_flag(bot_file, "--enter")
    if has_enter:
        entry_cmd = ["python3", os.path.join(BOT_DIR, bot_file), "--enter"]
        lines.append(f"  [3/4] Enter {decision['new_symbol']} via --enter...")
    else:
        entry_cmd = ["python3", os.path.join(BOT_DIR, bot_file), "--rotate"]
        lines.append(f"  [3/4] Enter {decision['new_symbol']} via --rotate (legacy bot)...")

    # FIX C: retry на slippage до 3 попыток (orderbook может стабилизироваться за 30 сек).
    # НЕ ретраим на API-lock 109400 (биржа сама блокирует) и не ретраим если уже зашли.
    MAX_ENTER_RETRIES = 3
    RETRY_DELAY_SEC = 30
    r2 = None
    for attempt in range(1, MAX_ENTER_RETRIES + 1):
        r2 = subprocess.run(entry_cmd, capture_output=True, text=True, cwd=BOT_DIR, timeout=180)
        out = (r2.stdout or "") + (r2.stderr or "")
        # Проверяем — если биржа заблокировала или зашли успешно, прерываем retry
        if "109400" in out or "API orders are temporarily disabled" in out:
            break
        # Маркер успешного входа
        if "DELTA-NEUTRAL ПОЗИЦИЯ ОТКРЫТА" in out:
            if attempt > 1:
                lines.append(f"    ✓ Вход успешен с попытки #{attempt}")
            break
        # Маркер slippage fail — ретраим
        is_slippage = ("книга тонкая" in out) or ("Slippage check FAIL" in out)
        if is_slippage and attempt < MAX_ENTER_RETRIES:
            lines.append(f"    ⏳ Попытка #{attempt}: slippage высокий, retry через {RETRY_DELAY_SEC} сек...")
            time.sleep(RETRY_DELAY_SEC)
            continue
        # Любой другой случай (rc!=0, неизвестный output) — выходим
        break
    lines.append(f"    stdout: {r2.stdout[-300:] if r2.stdout else '(пусто)'}")

    # ===== STEP 3.5: DETECT API-LOCK (109400) — AUTO-GRAVEYARD =====
    # Если биржа заблокировала пару на время высокой волатильности, safety patch v1
    # в cmd_enter уже откатил спот. Здесь мы добавляем пару в graveyard чтобы
    # ротация не предлагала её снова в ближайшие 48 часов.
    combined_output = (r2.stdout or "") + (r2.stderr or "")
    if "109400" in combined_output or "API orders are temporarily disabled" in combined_output:
        lines.append(f"    ⚠️  Биржа заблокировала {decision['new_symbol']} (code 109400). Safety patch должен был откатить спот.")
        _api_lock_h = cooldown_for_reason("api_vol_lock")
        add_to_graveyard(decision["new_symbol"], "api_vol_lock", cooldown_h=_api_lock_h)
        lines.append(f"    🪦 {decision['new_symbol']} → graveyard ({_api_lock_h}ч cool-down, tag=api_vol_lock)")
        # Проверим что позиция действительно не открылась (guard против false-positive)
        time.sleep(2)
        st_after = load_json(os.path.join(BOT_DIR, state_file), default={})
        if st_after.get("position_open"):
            lines.append(f"    🚨 CONFLICT: 109400 detected, но state показывает position_open=true! Ручная проверка!")
            return False, lines
        lines.append(f"    ✓ Позиция действительно не открыта (safety rollback подтверждён)")
        return False, lines

    if r2.returncode != 0:
        lines.append(f"    ERROR rc={r2.returncode}: {r2.stderr[-200:]}")
        return False, lines

    # ===== STEP 4: VERIFY POST-ENTRY =====
    time.sleep(3)
    ok, reason = verify_post_entry(bot_file, decision["new_symbol"], state_file)
    if ok:
        lines.append(f"  [4/4] ✅ Проверка пройдена: {reason}")
        return True, lines
    else:
        lines.append(f"  [4/4] ⚠️  Проверка НЕ пройдена: {reason}")
        lines.append(f"       Срочно проверь вручную: python3 {bot_file} --status")
        return False, lines


# ══ Report generation ═════════════════════════════════════════════════════
def format_report(result, max_candidates=5):
    lines = []
    lines.append(f"🔄 Smart Rotation Report ({result['timestamp'][:19]}Z)")
    if result.get("skipped_legacy"):
        lines.append(f"⚠️  Пропущены (legacy, нет --enter): {', '.join(result['skipped_legacy'])}")
    lines.append("")
    lines.append("Текущие позиции:")
    for p in result["positions"]:
        if not p["open"]:
            lines.append(f"  — {p['label']} ({p['name']}): закрыт")
            continue
        cr = p.get("current_rate", 0) * 100
        v  = {"good":"✅", "weak":"⚠️", "bad":"🔴",
              "anchor-protected":"⚓", "closed":"—"}.get(p["verdict"], "?")
        lines.append(f"  {v} {p['label']} {p['symbol']}: rate {cr:+.4f}%, ${p['spot_budget']:.0f}")

    lines.append("")
    lines.append(f"Капитал в игре: ${result['total_capital']:.0f}")
    lines.append(f"Graveyard (cool-down): {len(result['graveyard'])} пар")
    # Block 6 (A): показываем remaining hours по каждой записи
    if result.get("graveyard"):
        _now = datetime.now(timezone.utc)
        for sym, rec in list(result["graveyard"].items())[:5]:  # топ-5
            try:
                ej = datetime.fromisoformat(rec["ejected_at"])
                cd = int(rec.get("cooldown_h", GRAVEYARD_COOLDOWN_H))
                remaining_h = max(0, cd - (_now - ej).total_seconds() / 3600)
                lines.append(f"  🪦 {sym:14s} ещё {remaining_h:5.1f}ч "
                             f"(полно {cd}ч, reason='{str(rec.get('reason','?'))[:30]}')")
            except Exception:
                pass

    lines.append("")
    lines.append(f"Top-{max_candidates} кандидатов (прошли все фильтры):")
    if not result["candidates"]:
        lines.append("  — нет подходящих")
    else:
        for c in result["candidates"][:max_candidates]:
            stab = c["stability"]
            lines.append(f"  {c['symbol']:<14} APR {c['apr_pct']:6.1f}% | "
                         f"stab {stab['positive_count']}/6 | "
                         f"slip {c['slippage']*100:.3f}% | "
                         f"Kelly ${c['kelly_size_usd']:.0f}")

    lines.append("")
    d = result["decision"]
    if d:
        if d.get("action") == "forced_exit":
            lines.append(f"🚨 Решение (forced_exit): {d['eject_symbol']} → (слот свободен)")
            lines.append(f"   (причина: {d['eject_reason']}, rate={d.get('current_rate', 0)*100:.4f}%/8ч, "
                         f"age={d.get('age_hours', 0):.1f}ч)")
        else:
            lines.append(f"🎯 Решение: {d['eject_symbol']} → {d['new_symbol']}")
            lines.append(f"   (причина: {d['eject_reason']}, улучшение: {d['candidate']['apr_pct']:.0f}% APR)")
    else:
        lines.append("🟢 Решение: ротация не требуется")

    return "\n".join(lines)


# ══ Entry point ═══════════════════════════════════════════════════════════
def cmd_rotate_smart(apply_changes=False):
    """Called from arb_tools.py --rotate-smart [--apply]"""
    # Block 2: safe-mode блокирует все ротации
    if apply_changes:
        try:
            sys.path.insert(0, BOT_DIR)
            from pause_check import can_act
            _ok, _reason = can_act()
            if not _ok:
                print(f"🛑 ROTATION SKIPPED: {_reason}")
                try:
                    from arb_tools import tg_send
                    tg_send(f"🛑 ROTATION skipped (safe-mode): {_reason}")
                except Exception:
                    pass
                return
        except Exception as _e:
            print(f"[SAFE-MODE-CHECK] failed (proceed): {_e}")
    result = analyze_rotation()
    report = format_report(result)
    print(report)

    # TG notification (отчёт из analyze_rotation — до выполнения)
    try:
        sys.path.insert(0, BOT_DIR)
        from arb_bot import tg_send
        tg_send("<pre>" + report + "</pre>")
    except Exception as e:
        print(f"(TG send failed: {e})")

    # FIX (Block 5.x bug #2): логируем РЕАЛЬНЫЙ результат applied=True/False
    # Раньше в лог писали applied=True ДО выполнения — и при крэше/basis fail
    # лог врал. Теперь _log_decision вызывается ПОСЛЕ execute_rotation.
    rotation_ok = None
    if apply_changes and result["decision"]:
        print("")
        print("=" * 60)
        print("APPLYING ROTATION")
        print("=" * 60)
        rotation_ok, lines = execute_rotation(result["decision"], dry_run=False)
        for ln in lines: print(ln)
        # добавляем lifetime PnL summary в TG-отчёт
        try:
            sys.path.insert(0, BOT_DIR)
            import lifetime_pnl
            s = lifetime_pnl.get_summary()
            lines.append("")
            lines.append(f"💰 Lifetime PnL: ${s['total_earned_usdt']:.2f} "
                         f"за {s['total_rotations']} ротаций")
        except Exception:
            pass
        try:
            from arb_bot import tg_send
            tg_send("<pre>" + "\n".join(lines) + "</pre>")
        except Exception:
            pass

    # Лог и history ПОСЛЕ реального выполнения. applied=True только если execute_rotation успех.
    # Для случая «dry-run без --apply» или «no-decision» applied = False.
    really_applied = bool(rotation_ok) if rotation_ok is not None else False

    hist = load_json(HISTORY_FILE, default=[])
    hist.append({
        "timestamp": result["timestamp"],
        "decision":  result["decision"],
        "applied":   really_applied,
    })
    hist = hist[-100:]
    save_json(HISTORY_FILE, hist)
    _log_decision(result, applied=really_applied)

    if apply_changes and result["decision"]:
        return rotation_ok
    return True


def _log_decision(result, applied):
    """FIX #5: всегда писать строку в /var/log/bingx-rotation.log, даже при no-decision."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        d = result.get("decision")
        if d:
            line = (f"[{ts}] decision={d.get('action','?')} "
                    f"bot={d.get('eject_bot','?')} "
                    f"{d.get('eject_symbol','?')}→{d.get('new_symbol','?')} "
                    f"applied={applied} "
                    f"reason='{d.get('eject_reason','?')[:80]}'")
        else:
            reasons = result.get("no_decision_reasons", [])
            line = f"[{ts}] decision=none reasons={reasons}"
        with open("/var/log/bingx-rotation.log", "a") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"(log_decision failed: {e})")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    cmd_rotate_smart(apply_changes=apply)
