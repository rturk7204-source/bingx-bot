"""Block 8 (Часть 2): Smart Top-Up — авто-доливка в зрелые выгодные пары.

ИДЕЯ: вместо открытия новой позиции (риск) мы наращиваем размер УЖЕ доказавшей
себя пары: возраст >= 72ч, >= 15 выплат, 0 bad_periods, текущий rate >= 0.0004
(~44% APR). Транш фиксированный $40, cap 25% от total_capital на одну пару,
cooldown 24ч между доливками одного бота.

Используется из rotation.analyze_rotation() ПОСЛЕ того как fill_empty/rotate/
forced_exit отработали и не приняли решения. Тогда у нас есть свободный спот —
вкладываем его в надёжный источник дохода, а не паркуем.
"""
import env_loader  # noqa: F401  (auto-loads .env)
import os
import json
from datetime import datetime, timezone

# ══ Константы (дублируем из rotation для тестируемости) ═══════════════════
# При импорте из rotation мы их перепишем актуальными значениями.
TOP_UP_MIN_AGE_H        = 72.0       # min возраст позиции
TOP_UP_MIN_PAYMENTS     = 15         # min полученных выплат
TOP_UP_MAX_BAD_PERIODS  = 0          # ноль срывов
TOP_UP_MIN_RATE         = 0.0004     # ~44% APR
TOP_UP_CAP_PCT          = 0.25       # max 25% от total capital на одну пару
TOP_UP_TRANCHE_USD      = 40.0       # размер одной доливки
TOP_UP_COOLDOWN_H       = 24.0       # min интервал между доливками

# BOT_DIR определяется через env (тесты переопределяют)
BOT_DIR = os.environ.get("BOT_DIR", "/root/bingx-bot")
TOP_UP_LOG_FILE = os.path.join(BOT_DIR, "top_up_log.json")


def _now_utc():
    return datetime.now(timezone.utc)


def _load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _parse_entry_time(et_str):
    """Парсит entry_time как делает rotation. Возвращает datetime UTC или None."""
    if not et_str:
        return None
    # формат arb_bot — "%Y-%m-%d %H:%M UTC"
    try:
        return datetime.strptime(et_str, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    # fallback ISO
    try:
        et = datetime.fromisoformat(et_str.replace("Z", "+00:00"))
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        return et
    except Exception:
        return None


def _age_hours(state, now=None):
    if now is None:
        now = _now_utc()
    et = _parse_entry_time(state.get("entry_time"))
    if et is None:
        return 0.0
    return (now - et).total_seconds() / 3600.0


def load_recent_topups(log_file=None):
    """Возвращает {bot_name: latest_topup_datetime} из top_up_log.json.

    Если файла нет — пустой dict (fail-open).
    Записи в логе: [{bot, symbol, timestamp, amount, new_budget}, ...]
    """
    if log_file is None:
        log_file = TOP_UP_LOG_FILE
    log = _load_json(log_file, default=[])
    if not isinstance(log, list):
        return {}
    latest = {}
    for entry in log:
        if not isinstance(entry, dict):
            continue
        bot = entry.get("bot")
        ts_str = entry.get("timestamp", "")
        if not bot or not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if bot not in latest or ts > latest[bot]:
            latest[bot] = ts
    return latest


def is_eligible_for_topup(state, current_rate, total_capital, recent_topups,
                          bot_name=None, now=None,
                          min_age_h=None, min_payments=None,
                          max_bad_periods=None, min_rate=None,
                          cap_pct=None, tranche_usd=None,
                          cooldown_h=None):
    """Подходит ли пара под доливку?

    Возвращает (eligible: bool, reason: str).

    Параметры:
      state:           dict состояния бота (arb_state.json)
      current_rate:    текущий funding rate (доли)
      total_capital:   суммарный капитал в долларах ($1404)
      recent_topups:   {bot_name: datetime} из load_recent_topups()
      bot_name:        имя бота (для cooldown lookup)
    """
    if min_age_h is None:        min_age_h = TOP_UP_MIN_AGE_H
    if min_payments is None:     min_payments = TOP_UP_MIN_PAYMENTS
    if max_bad_periods is None:  max_bad_periods = TOP_UP_MAX_BAD_PERIODS
    if min_rate is None:         min_rate = TOP_UP_MIN_RATE
    if cap_pct is None:          cap_pct = TOP_UP_CAP_PCT
    if tranche_usd is None:      tranche_usd = TOP_UP_TRANCHE_USD
    if cooldown_h is None:       cooldown_h = TOP_UP_COOLDOWN_H
    if now is None:              now = _now_utc()

    # 1. Позиция должна быть открыта
    if not state.get("position_open"):
        return False, "not_open"

    # 2. Возраст
    age_h = _age_hours(state, now=now)
    if age_h < min_age_h:
        return False, f"too_young ({age_h:.1f}h<{min_age_h}h)"

    # 3. Количество выплат
    payments = int(state.get("payments_received", 0) or 0)
    if payments < min_payments:
        return False, f"few_payments ({payments}<{min_payments})"

    # 4. Bad periods
    bad = int(state.get("bad_periods", 0) or 0)
    if bad > max_bad_periods:
        return False, f"bad_periods ({bad}>{max_bad_periods})"

    # 5. Текущий rate
    if current_rate < min_rate:
        return False, f"low_rate ({current_rate:.6f}<{min_rate:.6f})"

    # 6. Cap: spot_budget + tranche <= cap_pct * total_capital
    cur_budget = float(state.get("spot_budget", 0) or 0)
    cap_usd = cap_pct * total_capital
    if cur_budget + tranche_usd > cap_usd:
        return False, f"cap_exceeded (${cur_budget:.0f}+${tranche_usd:.0f}>${cap_usd:.0f})"

    # 7. Cooldown
    if bot_name and bot_name in recent_topups:
        last_ts = recent_topups[bot_name]
        elapsed_h = (now - last_ts).total_seconds() / 3600.0
        if elapsed_h < cooldown_h:
            return False, f"cooldown ({elapsed_h:.1f}h<{cooldown_h}h)"

    return True, "ok"


def select_topup_candidate(positions, total_capital, free_spot_usd,
                            recent_topups=None, now=None,
                            tranche_usd=None):
    """Выбирает пару для доливки из открытых позиций.

    positions: список dict с минимум полями name, state (dict), current_rate
    free_spot_usd: сколько свободного USDT на споте (должно быть >= tranche)
    Возвращает: dict {bot, symbol, current_budget, new_budget, tranche, reason}
                или None если кандидата нет.

    Из всех eligible выбираем с НАИВЫСШИМ current_rate (самая выгодная).
    """
    if tranche_usd is None:
        tranche_usd = TOP_UP_TRANCHE_USD
    if recent_topups is None:
        recent_topups = load_recent_topups()
    if now is None:
        now = _now_utc()

    if free_spot_usd < tranche_usd:
        return None  # нечего доливать

    eligible = []
    for pos in positions:
        bot_name = pos.get("name")
        state = pos.get("state") or {}
        current_rate = float(pos.get("current_rate", 0) or 0)
        ok, reason = is_eligible_for_topup(
            state, current_rate, total_capital, recent_topups,
            bot_name=bot_name, now=now, tranche_usd=tranche_usd,
        )
        if ok:
            eligible.append((current_rate, pos, state))

    if not eligible:
        return None

    # самая выгодная пара — максимальный rate
    eligible.sort(key=lambda x: -x[0])
    rate, pos, state = eligible[0]
    cur_budget = float(state.get("spot_budget", 0) or 0)
    return {
        "bot":            pos["name"],
        "symbol":         state.get("symbol") or pos.get("symbol"),
        "current_rate":   rate,
        "current_budget": cur_budget,
        "new_budget":     cur_budget + tranche_usd,
        "tranche":        tranche_usd,
    }


def record_topup(bot_name, symbol, tranche_usd, new_budget,
                 log_file=None, now=None):
    """Записывает доливку в top_up_log.json."""
    if log_file is None:
        log_file = TOP_UP_LOG_FILE
    if now is None:
        now = _now_utc()
    log = _load_json(log_file, default=[])
    if not isinstance(log, list):
        log = []
    log.append({
        "bot":        bot_name,
        "symbol":     symbol,
        "timestamp":  now.isoformat(),
        "amount":     tranche_usd,
        "new_budget": new_budget,
    })
    _save_json(log_file, log)


def apply_topup(bot_name, state_file, tranche_usd=None, dry_run=False):
    """Применяет доливку: обновляет spot_budget в state-файле.

    Возвращает (success: bool, info: dict).
    info содержит old_budget, new_budget, tranche, symbol.

    Замечание: РЕАЛЬНОЕ перекладывание USDT (spot купить ещё, futures
    добавить collateral) делается в arb_botN на следующем цикле — он
    видит новый spot_budget и докупает разницу. Эта функция только
    корректирует state.
    """
    if tranche_usd is None:
        tranche_usd = TOP_UP_TRANCHE_USD
    state = _load_json(state_file, default={})
    if not state.get("position_open"):
        return False, {"error": "not_open"}
    old_budget = float(state.get("spot_budget", 0) or 0)
    new_budget = old_budget + tranche_usd
    info = {
        "bot":         bot_name,
        "symbol":      state.get("symbol"),
        "old_budget":  old_budget,
        "new_budget":  new_budget,
        "tranche":     tranche_usd,
    }
    if dry_run:
        return True, info
    state["spot_budget"] = new_budget
    _save_json(state_file, state)
    record_topup(bot_name, state.get("symbol"), tranche_usd, new_budget)
    return True, info
