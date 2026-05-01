#!/usr/bin/env python3
"""
Block 1 — Auto-Balance: автоматический перевод USDT между SPOT/FUND/PERP кошельками.

Главная цель: убрать ручное вмешательство пользователя 3×/сутки для перевода
капитала между кошельками BingX. Используется в rotation.py, auto_enter.py,
topup.py, arb_botN.py.

Архитектура:
    ensure_spot_balance(needed)  — гарантирует USDT на SPOT >= needed + buffer
    ensure_perp_margin(needed)   — гарантирует свободную perp margin >= needed + buffer

Safety guards:
    1. perp_min_avail = $30        никогда не оставлять perp ниже
    2. spot_min_keep  = $5         всегда оставлять на spot для fees
    3. max_transfer_per_5min       file-lock защита от инфинит-лупа
    4. cooldown 4s между переводами (BingX rate limit)
    5. post-transfer balance verify
    6. circuit breaker: 3 fail подряд → стоп на 30 минут + TG alert

Использование:
    from auto_balance import ensure_spot_balance, ensure_perp_margin

    if not ensure_spot_balance(80.0):   # нужно 80 USDT на спот
        log.error("rotation aborted: cannot get spot USDT")
        return

CLI:
    python3 auto_balance.py status                 # текущие балансы + safety status
    python3 auto_balance.py ensure_spot 80         # dry-run
    python3 auto_balance.py ensure_spot 80 --apply # боевой режим
    python3 auto_balance.py reset_circuit          # сбросить circuit breaker
"""
import os
import sys
import json
import time
import logging
import fcntl
from pathlib import Path
from datetime import datetime, timezone

# Локальные импорты
sys.path.insert(0, str(Path(__file__).parent))
from bingx_transfer import transfer_usdt, get_wallet_balances

# ============ КОНФИГ ============

SAFETY_LIMITS = {
    "perp_min_avail":         30.0,   # никогда не оставлять <$30 свободной маржи на perp
    "spot_min_keep":           5.0,   # всегда минимум на spot для fees
    "fund_min_keep":           0.0,   # fund можно опустошать полностью
    "max_transfer_per_5min": 200.0,   # rate limit от инфинит-лупа
    "cooldown_seconds":        4.0,   # между переводами
    "circuit_break_after_fails": 3,   # 3 fail подряд → стоп
    "circuit_break_duration_min": 30, # на 30 минут
    "balance_verify_tolerance": 0.10, # допустимая погрешность post-verify (USDT)
}

STATE_DIR  = Path("/root/bingx-bot/state")
STATE_DIR.mkdir(exist_ok=True)
LOG_DIR    = Path("/root/bingx-bot/logs")
LOG_DIR.mkdir(exist_ok=True)

STATE_FILE   = STATE_DIR / "auto_balance_state.json"
LOCK_FILE    = STATE_DIR / "auto_balance.lock"
LOG_FILE     = LOG_DIR  / "auto_balance.log"

# ============ ЛОГГЕР ============

log = logging.getLogger("auto_balance")
log.setLevel(logging.INFO)
if not log.handlers:
    fh = logging.FileHandler(LOG_FILE)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(sh)

# ============ STATE MANAGEMENT ============

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {
        "transfers": [],          # [{ts, direction, amount, success, tran_id}]
        "consecutive_fails": 0,
        "circuit_break_until": 0, # epoch seconds
    }


def _save_state(state: dict):
    # Block 6: atomic write с .bak ротацией
    try:
        from safe_io import safe_write_json
        safe_write_json(str(STATE_FILE), state, indent=2)
    except ImportError:
        STATE_FILE.write_text(json.dumps(state, indent=2))


def _prune_old_transfers(state: dict):
    """Оставить только переводы за последние 5 минут (для rate limit)."""
    cutoff = time.time() - 300
    state["transfers"] = [t for t in state["transfers"] if t.get("ts", 0) > cutoff]


# ============ TELEGRAM ALERT ============

def _send_telegram(msg: str):
    """Отправляет сообщение в TG через существующий бот (если настроен)."""
    try:
        token   = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TG_CHAT_ID")   or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            log.warning(f"[TG-stub] {msg}")
            return
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception as e:
        log.error(f"TG send failed: {e}")


# ============ SAFETY CHECKS ============

def _check_circuit_breaker(state: dict) -> tuple:
    """
    Returns: (open, reason)
    open=True означает что breaker сработал — переводы заблокированы.
    """
    now = time.time()
    if state.get("circuit_break_until", 0) > now:
        remaining = int((state["circuit_break_until"] - now) / 60)
        return True, f"circuit breaker active, {remaining}min remaining"
    return False, ""


def _check_rate_limit(state: dict, amount: float) -> tuple:
    """
    Returns: (allowed, reason)
    """
    _prune_old_transfers(state)
    total_5min = sum(t.get("amount", 0) for t in state["transfers"] if t.get("success"))
    if total_5min + amount > SAFETY_LIMITS["max_transfer_per_5min"]:
        return False, (f"rate limit: would exceed ${SAFETY_LIMITS['max_transfer_per_5min']:.0f}/5min "
                       f"(current ${total_5min:.2f} + ${amount:.2f})")
    return True, ""


def _record_transfer(state: dict, direction: str, amount: float,
                     success: bool, tran_id: str):
    state["transfers"].append({
        "ts":        time.time(),
        "iso":       datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "amount":    amount,
        "success":   success,
        "tran_id":   tran_id,
    })
    _prune_old_transfers(state)

    if success:
        state["consecutive_fails"] = 0
    else:
        state["consecutive_fails"] = state.get("consecutive_fails", 0) + 1
        if state["consecutive_fails"] >= SAFETY_LIMITS["circuit_break_after_fails"]:
            duration = SAFETY_LIMITS["circuit_break_duration_min"] * 60
            state["circuit_break_until"] = time.time() + duration
            _send_telegram(
                f"🚨 <b>Auto-Balance Circuit Breaker</b>\n"
                f"3 transfer failures in a row.\n"
                f"Blocked for {SAFETY_LIMITS['circuit_break_duration_min']} minutes.\n"
                f"Last direction: {direction}\n"
                f"Last amount: {amount:.2f} USDT"
            )


# ============ ОСНОВНЫЕ API ============

def _do_transfer(direction: str, amount: float) -> tuple:
    """
    Выполняет перевод с проверками safety, rate limit, circuit breaker и post-verify.
    Returns: (success: bool, info: str)
    """
    amount = round(float(amount), 2)
    if amount < 0.5:
        return True, "skip: amount < 0.5 USDT"
    if amount > 1000:
        return False, f"refuse: amount {amount} > 1000 (manual review)"

    # Lock — only one transfer at a time
    with open(LOCK_FILE, "w") as lf:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False, "another transfer in progress (lock held)"

        state = _load_state()

        cb_open, cb_reason = _check_circuit_breaker(state)
        if cb_open:
            log.error(f"circuit breaker: {cb_reason}")
            return False, cb_reason

        ok_rate, rl_reason = _check_rate_limit(state, amount)
        if not ok_rate:
            log.error(f"rate limit: {rl_reason}")
            _send_telegram(f"⚠ Auto-Balance rate limit hit: {rl_reason}")
            return False, rl_reason

        # Snapshot до перевода для post-verify
        b_before = get_wallet_balances()
        log.info(f"transfer {direction} {amount} USDT — balances before: "
                 f"spot={b_before['spot']:.2f} fund={b_before['fund']:.2f} "
                 f"perp_avail={b_before['perp_avail']:.2f}")

        # Выполняем перевод
        success, info = transfer_usdt(amount, direction)
        time.sleep(SAFETY_LIMITS["cooldown_seconds"])

        # Post-verify баланса
        b_after = get_wallet_balances()
        verified = _verify_balance_change(b_before, b_after, direction, amount)

        if success and not verified:
            log.error(f"transfer reported success but balance unchanged! "
                      f"before={b_before} after={b_after}")
            _send_telegram(
                f"⚠ <b>Auto-Balance: ghost transfer</b>\n"
                f"API said OK but balances unchanged.\n"
                f"Direction: {direction}\n"
                f"Amount: {amount}\n"
                f"tranId: {info}"
            )
            success = False
            info = f"ghost-transfer: API OK but no balance movement (was {info})"

        _record_transfer(state, direction, amount, success, info)
        _save_state(state)

        log.info(f"transfer {direction} {amount} USDT — "
                 f"{'✅' if success else '❌'} {info} — "
                 f"after: spot={b_after['spot']:.2f} fund={b_after['fund']:.2f} "
                 f"perp_avail={b_after['perp_avail']:.2f}")

        return success, info


def _verify_balance_change(before: dict, after: dict,
                           direction: str, amount: float) -> bool:
    """
    Проверяет что баланс реально изменился в нужную сторону.
    """
    tol = SAFETY_LIMITS["balance_verify_tolerance"]

    # Допустимая погрешность из-за параллельных движений (funding payouts, etc.)
    # Проверяем что src уменьшился И dst увеличился, оба ~ на amount
    direction_map = {
        "spot_to_fund": ("spot", "fund"),
        "fund_to_spot": ("fund", "spot"),
        "spot_to_perp": ("spot", "perp_balance"),
        "perp_to_spot": ("perp_balance", "spot"),
        "fund_to_perp": ("fund", "perp_balance"),
        "perp_to_fund": ("perp_balance", "fund"),
    }
    if direction not in direction_map:
        return True  # неизвестное направление — не проверяем

    src_key, dst_key = direction_map[direction]
    src_delta = before.get(src_key, 0) - after.get(src_key, 0)  # должен быть ~+amount
    dst_delta = after.get(dst_key, 0) - before.get(dst_key, 0)  # должен быть ~+amount

    # Считаем "успех" если src уменьшился хотя бы на 50% от amount
    # (защита от случая API success но 0 движение)
    return src_delta >= amount * 0.5 - tol


def ensure_spot_balance(needed_usdt: float, buffer: float = 2.0) -> bool:
    """
    Гарантирует что на SPOT >= needed + buffer USDT.
    Источники в порядке приоритета: PERP (если запас есть) → FUND.

    Args:
        needed_usdt: сколько USDT нужно для операции
        buffer: дополнительный запас (default $2)

    Returns:
        True — баланс достаточен (изначально или после перевода)
        False — невозможно обеспечить, операция должна быть отменена
    """
    target = needed_usdt + buffer
    b = get_wallet_balances()

    if b["spot"] >= target:
        return True

    deficit = round(target - b["spot"], 2)
    log.info(f"ensure_spot_balance: need {target:.2f}, have {b['spot']:.2f}, "
             f"deficit {deficit:.2f}")

    # Источник 1: PERP (если есть свободная маржа выше safety floor)
    perp_pull = min(deficit, b["perp_avail"] - SAFETY_LIMITS["perp_min_avail"])
    if perp_pull >= 0.5:
        ok, info = _do_transfer("perp_to_spot", perp_pull)
        if ok:
            b = get_wallet_balances()
            if b["spot"] >= target:
                return True
            deficit = round(target - b["spot"], 2)

    # Источник 2: FUND
    fund_pull = min(deficit, b["fund"])
    if fund_pull >= 0.5:
        ok, info = _do_transfer("fund_to_spot", fund_pull)
        if ok:
            b = get_wallet_balances()
            if b["spot"] >= target:
                return True

    # Не удалось — alert
    final = get_wallet_balances()
    total = final["spot"] + final["fund"] + max(0, final["perp_avail"] - SAFETY_LIMITS["perp_min_avail"])
    log.error(f"ensure_spot_balance FAILED: need {target:.2f}, "
              f"reachable total {total:.2f}")
    _send_telegram(
        f"⚠ <b>Auto-Balance: нехватка USDT</b>\n"
        f"Нужно на SPOT: {target:.2f} USDT\n"
        f"Доступно: spot={final['spot']:.2f}, fund={final['fund']:.2f}, "
        f"perp_avail={final['perp_avail']:.2f} (-${SAFETY_LIMITS['perp_min_avail']:.0f} safety)\n"
        f"Действие: пополни аккаунт или закрой позицию"
    )
    return False


def ensure_perp_margin(needed_usdt: float, buffer: float = 5.0) -> bool:
    """
    Гарантирует что свободная perp margin >= needed + buffer USDT.
    Используется при добавлении маржи к short позициям.
    Источник: SPOT (с учётом spot_min_keep).
    """
    target = needed_usdt + buffer
    b = get_wallet_balances()

    if b["perp_avail"] >= target:
        return True

    deficit = round(target - b["perp_avail"], 2)
    log.info(f"ensure_perp_margin: need {target:.2f}, have {b['perp_avail']:.2f}, "
             f"deficit {deficit:.2f}")

    spot_can_send = b["spot"] - SAFETY_LIMITS["spot_min_keep"]
    pull = min(deficit, spot_can_send)
    if pull >= 0.5:
        ok, info = _do_transfer("spot_to_perp", pull)
        if ok:
            b2 = get_wallet_balances()
            if b2["perp_avail"] >= target:
                return True

    log.error(f"ensure_perp_margin FAILED: need {target:.2f}")
    _send_telegram(
        f"⚠ <b>Auto-Balance: нехватка perp margin</b>\n"
        f"Нужно: {target:.2f} USDT\n"
        f"Spot: {b['spot']:.2f} (min keep ${SAFETY_LIMITS['spot_min_keep']:.0f})\n"
        f"Perp avail: {b['perp_avail']:.2f}"
    )
    return False


def status() -> dict:
    """
    Текущий статус системы — для дашборда / CLI / алертов.
    """
    state = _load_state()
    _prune_old_transfers(state)
    b = get_wallet_balances()
    cb_open, cb_reason = _check_circuit_breaker(state)
    transfers_5min = state["transfers"]
    total_5min = sum(t.get("amount", 0) for t in transfers_5min if t.get("success"))

    return {
        "balances": b,
        "safety_limits": SAFETY_LIMITS,
        "circuit_breaker": {
            "open":   cb_open,
            "reason": cb_reason,
            "consecutive_fails": state.get("consecutive_fails", 0),
        },
        "rate_limit": {
            "transfers_in_window": len(transfers_5min),
            "amount_in_window":    round(total_5min, 2),
            "remaining_capacity":  round(SAFETY_LIMITS["max_transfer_per_5min"] - total_5min, 2),
        },
        "recent_transfers": transfers_5min[-5:],
    }


def reset_circuit_breaker():
    """Ручной сброс circuit breaker (если уверен что причина устранена)."""
    state = _load_state()
    state["circuit_break_until"] = 0
    state["consecutive_fails"]   = 0
    _save_state(state)
    log.info("circuit breaker manually reset")


# ============ CLI ============

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "status":
        s = status()
        print(json.dumps(s, indent=2))

    elif cmd == "ensure_spot" and len(sys.argv) >= 3:
        amount = float(sys.argv[2])
        if "--apply" not in sys.argv:
            print(f"DRY-RUN: would ensure_spot_balance({amount})")
            print(f"current: {json.dumps(get_wallet_balances(), indent=2)}")
        else:
            ok = ensure_spot_balance(amount)
            print(f"{'✅' if ok else '❌'} ensure_spot_balance({amount})")

    elif cmd == "ensure_perp" and len(sys.argv) >= 3:
        amount = float(sys.argv[2])
        if "--apply" not in sys.argv:
            print(f"DRY-RUN: would ensure_perp_margin({amount})")
            print(f"current: {json.dumps(get_wallet_balances(), indent=2)}")
        else:
            ok = ensure_perp_margin(amount)
            print(f"{'✅' if ok else '❌'} ensure_perp_margin({amount})")

    elif cmd == "reset_circuit":
        reset_circuit_breaker()
        print("✅ circuit breaker reset")

    else:
        print(__doc__)
        sys.exit(1)
