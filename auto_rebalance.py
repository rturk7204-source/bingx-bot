#!/usr/bin/env python3
"""
Block 8.5 — Auto-Rebalance: ПРОАКТИВНЫЙ перевод USDT между PERP ↔ SPOT.

Проблема которую решает модуль:
    auto_balance.ensure_spot_balance() работает только РЕАКТИВНО — вызывается из
    rotation.py при попытке войти в новую пару. Когда все 6 ботов уже открыты,
    ротация не происходит → автоперевод не срабатывает → капитал лежит idle.

    На практике (1 мая 2026): $443 free на perp, $93 на spot, top_up не может
    долить, потому что spot пустой, а ensure_spot_balance не вызывается.

Решение:
    Независимый cron каждые 30 минут запускает decide_rebalance() и
    apply_rebalance(). Логика проста и безопасна:

        1. Если spot < SPOT_BUFFER (100) → перевести с perp до SPOT_TARGET (150),
           но не оставить perp ниже PERP_SAFETY_MARGIN (30).

        2. Если perp_free > PERP_OVERFLOW (100) И spot уже >= SPOT_TARGET →
           перевести излишек spot → fund (для будущей маржи), оставив 30 на perp.

        3. Cooldown 30 минут между трансферами (не запускать чаще чем cron).

Архитектурно НЕ заменяет auto_balance.ensure_spot_balance() — работает рядом.
Использует те же SAFETY_LIMITS из auto_balance.py.

CLI:
    python3 auto_rebalance.py                     # боевой режим (cron)
    python3 auto_rebalance.py --dry-run           # что бы сделал, без перевода
    python3 auto_rebalance.py --status            # текущие балансы + decision
"""
import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))
from bingx_transfer import transfer_usdt, get_wallet_balances

# ============ КОНФИГ ============

# Целевая структура балансов:
PERP_SAFETY_MARGIN = 30.0    # никогда не оставлять <$30 free на perp (см. auto_balance)
SPOT_TARGET        = 150.0   # целевой уровень spot после перевода с perp
SPOT_BUFFER        = 50.0    # триггер: если spot < 100 → перевод с perp → spot
PERP_OVERFLOW      = 100.0   # триггер обратного: если perp_free > overflow → spot
COOLDOWN_MIN       = 30      # минут между трансферами

# Минимальная сумма перевода (BingX отвергает <0.1 USDT, плюс fee-эффективность):
MIN_TRANSFER_USD   = 5.0

BOT_DIR   = Path(os.environ.get("BOT_DIR", "/root/bingx-bot"))
STATE_DIR = BOT_DIR / "state"
LOG_DIR   = BOT_DIR / "logs"
try:
    STATE_DIR.mkdir(exist_ok=True, parents=True)
    LOG_DIR.mkdir(exist_ok=True, parents=True)
except (PermissionError, OSError):
    # В тестовой среде BOT_DIR может быть read-only или отсутствовать.
    pass

LOG_FILE   = BOT_DIR / "auto_rebalance_log.json"
PYLOG_FILE = LOG_DIR / "auto_rebalance.log"

# ============ ЛОГГЕР ============

log = logging.getLogger("auto_rebalance")
log.setLevel(logging.INFO)
if not log.handlers:
    try:
        fh = logging.FileHandler(PYLOG_FILE)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    except (PermissionError, OSError, FileNotFoundError):
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(sh)


# ============ JSON LOG (audit trail) ============

def _load_recent_transfers(window_min: int = COOLDOWN_MIN) -> list:
    """Возвращает список переводов из LOG_FILE за последние window_min минут."""
    if not LOG_FILE.exists():
        return []
    try:
        data = json.loads(LOG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_min)
    recent = []
    for entry in data:
        ts_str = entry.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            recent.append(entry)
    return recent


def _record_transfer(entry: dict) -> None:
    """Append-only запись в LOG_FILE (rolling, последние 1000 записей)."""
    try:
        if LOG_FILE.exists():
            data = json.loads(LOG_FILE.read_text())
            if not isinstance(data, list):
                data = []
        else:
            data = []
    except (json.JSONDecodeError, OSError):
        data = []
    data.append(entry)
    # Trim to last 1000 entries
    if len(data) > 1000:
        data = data[-1000:]
    try:
        LOG_FILE.write_text(json.dumps(data, indent=2))
    except OSError as e:
        log.error("failed to write LOG_FILE: %s", e)


# ============ DECISION LOGIC ============

def decide_rebalance(balances: dict) -> dict:
    """
    На основе текущих балансов решает, нужен ли перевод.

    balances: {'spot': X, 'fund': X, 'perp_balance': X, 'perp_avail': X, 'perp_equity': X}

    Returns dict:
        {'action': 'perp_to_spot'|'spot_to_perp'|'noop', 'amount': float, 'reason': str}
    """
    spot       = float(balances.get("spot", 0.0))
    perp_avail = float(balances.get("perp_avail", 0.0))

    # Триггер 1: spot слишком пустой → перевести с perp на spot
    spot_threshold = SPOT_TARGET - SPOT_BUFFER  # = 100
    if spot < spot_threshold:
        needed = SPOT_TARGET - spot
        # Не опускать perp ниже PERP_SAFETY_MARGIN
        max_from_perp = max(0.0, perp_avail - PERP_SAFETY_MARGIN)
        amount = min(needed, max_from_perp)
        if amount < MIN_TRANSFER_USD:
            return {
                "action": "noop",
                "amount": 0.0,
                "reason": f"spot={spot:.2f} < {spot_threshold:.0f}, "
                          f"но perp_avail={perp_avail:.2f} оставляет только "
                          f"{max_from_perp:.2f} (< MIN_TRANSFER {MIN_TRANSFER_USD})",
            }
        return {
            "action": "perp_to_spot",
            "amount": round(amount, 2),
            "reason": f"spot={spot:.2f} < {spot_threshold:.0f}, "
                      f"перевод {amount:.2f} с perp (avail={perp_avail:.2f})",
        }

    # Триггер 2: perp излишек И spot уже накормлен → излишек на spot (буфер для top_up)
    if perp_avail > (PERP_SAFETY_MARGIN + PERP_OVERFLOW) and spot >= SPOT_TARGET:
        excess = perp_avail - PERP_SAFETY_MARGIN - PERP_OVERFLOW
        amount = excess  # переводим всё что выше overflow
        if amount < MIN_TRANSFER_USD:
            return {
                "action": "noop",
                "amount": 0.0,
                "reason": f"perp излишек {amount:.2f} < MIN_TRANSFER {MIN_TRANSFER_USD}",
            }
        return {
            "action": "perp_to_spot",
            "amount": round(amount, 2),
            "reason": f"perp_avail={perp_avail:.2f} > safety+overflow="
                      f"{PERP_SAFETY_MARGIN + PERP_OVERFLOW:.0f}, "
                      f"излишек {amount:.2f} → spot",
        }

    # Балансы OK
    return {
        "action": "noop",
        "amount": 0.0,
        "reason": f"balanced: spot={spot:.2f} (>={spot_threshold:.0f}), "
                  f"perp_avail={perp_avail:.2f} (safety={PERP_SAFETY_MARGIN:.0f})",
    }


# ============ APPLY ============

def apply_rebalance(dry_run: bool = False) -> dict:
    """
    Главная точка входа. Вызывается cron каждые 30 минут.

    Returns dict:
        {
          'ts': iso8601,
          'balances_before': {...},
          'decision': {...},
          'executed': bool,
          'success': bool|None,
          'tran_id': str|None,
          'balances_after': {...}|None,
          'error': str|None,
        }
    """
    ts_now = datetime.now(timezone.utc).isoformat()

    # 1. Cooldown check — если за последние COOLDOWN_MIN был перевод, не делаем второй
    recent = _load_recent_transfers(window_min=COOLDOWN_MIN)
    successful_recent = [e for e in recent if e.get("success") is True]
    if successful_recent:
        last = successful_recent[-1]
        result = {
            "ts": ts_now,
            "balances_before": None,
            "decision": {"action": "noop", "amount": 0.0,
                         "reason": f"cooldown: last transfer at {last.get('ts')}"},
            "executed": False,
            "success": None,
            "tran_id": None,
            "balances_after": None,
            "error": None,
        }
        log.info("noop (cooldown): last transfer %s", last.get("ts"))
        _record_transfer(result)
        return result

    # 2. Получить балансы
    try:
        balances = get_wallet_balances("USDT")
    except Exception as e:
        result = {
            "ts": ts_now,
            "balances_before": None,
            "decision": None,
            "executed": False,
            "success": False,
            "tran_id": None,
            "balances_after": None,
            "error": f"get_wallet_balances failed: {e}",
        }
        log.error("balance fetch failed: %s", e)
        _record_transfer(result)
        return result

    # 3. Решение
    decision = decide_rebalance(balances)
    log.info("decision: %s", decision)

    if decision["action"] == "noop":
        result = {
            "ts": ts_now,
            "balances_before": balances,
            "decision": decision,
            "executed": False,
            "success": None,
            "tran_id": None,
            "balances_after": None,
            "error": None,
        }
        _record_transfer(result)
        return result

    # 4. Dry-run
    if dry_run:
        result = {
            "ts": ts_now,
            "balances_before": balances,
            "decision": decision,
            "executed": False,
            "success": None,
            "tran_id": "DRY_RUN",
            "balances_after": None,
            "error": None,
        }
        log.info("DRY-RUN: would transfer %.2f via %s",
                 decision["amount"], decision["action"])
        # dry-run не пишем в LOG_FILE (чтобы cooldown не блокировал)
        return result

    # 5. Боевой перевод
    try:
        ok, tran_id = transfer_usdt(
            amount=decision["amount"],
            direction=decision["action"],
            asset="USDT",
            dry_run=False,
        )
    except Exception as e:
        result = {
            "ts": ts_now,
            "balances_before": balances,
            "decision": decision,
            "executed": True,
            "success": False,
            "tran_id": None,
            "balances_after": None,
            "error": f"transfer_usdt raised: {e}",
        }
        log.error("transfer failed: %s", e)
        _record_transfer(result)
        return result

    # 6. Verify post-transfer balances
    time.sleep(2)
    try:
        balances_after = get_wallet_balances("USDT")
    except Exception as e:
        balances_after = None
        log.warning("post-transfer balance fetch failed: %s", e)

    result = {
        "ts": ts_now,
        "balances_before": balances,
        "decision": decision,
        "executed": True,
        "success": bool(ok),
        "tran_id": str(tran_id) if ok else None,
        "balances_after": balances_after,
        "error": None if ok else str(tran_id),
    }
    log.info("transfer %s: %.2f %s tran_id=%s",
             "OK" if ok else "FAIL",
             decision["amount"], decision["action"], tran_id)
    _record_transfer(result)
    return result


# ============ CLI ============

def main(argv: list) -> int:
    dry_run = "--dry-run" in argv
    status  = "--status" in argv

    if status:
        try:
            b = get_wallet_balances("USDT")
        except Exception as e:
            print(f"ERROR: balance fetch failed: {e}")
            return 1
        d = decide_rebalance(b)
        print(json.dumps({
            "balances": b,
            "decision": d,
            "config": {
                "PERP_SAFETY_MARGIN": PERP_SAFETY_MARGIN,
                "SPOT_TARGET":        SPOT_TARGET,
                "SPOT_BUFFER":        SPOT_BUFFER,
                "PERP_OVERFLOW":      PERP_OVERFLOW,
                "COOLDOWN_MIN":       COOLDOWN_MIN,
            },
            "recent_transfers": _load_recent_transfers(window_min=COOLDOWN_MIN),
        }, indent=2))
        return 0

    result = apply_rebalance(dry_run=dry_run)
    print(json.dumps(result, indent=2, default=str))
    return 0 if (result.get("success") is not False) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
