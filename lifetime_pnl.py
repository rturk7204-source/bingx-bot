#!/usr/bin/env python3
"""
lifetime_pnl.py — накопление PnL через ротации.

ЗАЧЕМ:
  arb_state{N}.json.total_earned_usdt сбрасывается в 0 после каждого exit
  (потому что бот стартует "с нуля" с новой парой). Это нормально для
  оперативной телеметрии, но мы теряем общий заработок при оценке прибыльности.

ЧТО ДЕЛАЕТ:
  - lifetime_pnl.json хранит совокупный заработок и историю закрытых позиций
    по каждому боту.
  - record_exit(bot_name, symbol, earned_usdt, ...) вызывается из rotation.py
    ПЕРЕД exit и фиксирует, сколько эта пара заработала.
  - get_summary() возвращает совокупный отчёт.
  - get_bot_lifetime(bot_name) — за один бот.

ФАЙЛОВЫЕ ОПЕРАЦИИ:
  Используем safe_io.safe_write_json — атомарная запись через .tmp + rename,
  чтобы при выключении света не получить битый JSON. Если safe_io недоступен
  (например, в тестах без зависимостей), fallback на обычную запись.

СТРУКТУРА lifetime_pnl.json:
  {
    "version": 1,
    "updated_at": "2026-05-01T15:26:00Z",
    "bots": {
      "arb_bot": {
        "total_earned_usdt": 1.34,
        "rotations_count": 3,
        "history": [
          {"ts": "...", "symbol": "AIN-USDT", "earned": 0.67,
           "cycles": 12, "reason": "weak_apr"},
          ...
        ]
      },
      ...
    }
  }
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(BOT_DIR, "lifetime_pnl.json")
SCHEMA_VERSION = 1
HISTORY_LIMIT = 200  # последние 200 закрытий на бот — больше не храним

# Лимит на исторический список разумен: 6 ботов × 200 = 1200 записей.
# При ротации раз в час это ~8 дней непрерывной истории на каждого бота.


# ══ low-level IO ══════════════════════════════════════════════════════════
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty_state() -> Dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "updated_at": _now_iso(),
        "bots": {},
    }


def _read(path: str) -> Dict[str, Any]:
    """Read lifetime_pnl.json. Returns empty state if missing or corrupted."""
    if not os.path.exists(path):
        return _empty_state()
    try:
        with open(path, "r") as f:
            data = json.load(f)
        # минимальная санити-проверка
        if not isinstance(data, dict) or "bots" not in data:
            return _empty_state()
        return data
    except (json.JSONDecodeError, OSError):
        # битый файл — не падаем, возвращаем пустое (cron не должен падать
        # из-за битого PnL). Реальные действия с балансом не страдают.
        return _empty_state()


def _write(path: str, data: Dict[str, Any]) -> bool:
    """Atomic write. Returns True on success."""
    data["updated_at"] = _now_iso()
    # пытаемся через safe_io если есть
    try:
        sys.path.insert(0, BOT_DIR)
        from safe_io import safe_write_json
        return safe_write_json(path, data, indent=2)
    except ImportError:
        pass
    # fallback: tmp + rename
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"[lifetime_pnl] write failed: {e}", file=sys.stderr)
        return False


def _ensure_bot(state: Dict[str, Any], bot_name: str) -> Dict[str, Any]:
    if bot_name not in state["bots"]:
        state["bots"][bot_name] = {
            "total_earned_usdt": 0.0,
            "rotations_count": 0,
            "history": [],
        }
    return state["bots"][bot_name]


# ══ public API ════════════════════════════════════════════════════════════
def record_exit(
    bot_name: str,
    symbol: str,
    earned_usdt: float,
    cycles: Optional[int] = None,
    reason: Optional[str] = None,
    path: str = DEFAULT_PATH,
) -> Dict[str, Any]:
    """
    Записать exit позиции в lifetime_pnl.json.
    Возвращает обновлённую запись бота.

    bot_name: "arb_bot" / "arb_bot2" / ... (как в BOTS list)
    symbol:   "AIN-USDT" например
    earned_usdt: накопленный total_earned_usdt из arb_state{N}.json на момент exit
    cycles:   кол-во funding-периодов которое пара отстояла (необязательно)
    reason:   "weak_apr" / "negative_funding" / "manual" / ... (необязательно)
    """
    state = _read(path)
    bot_rec = _ensure_bot(state, bot_name)
    earned = float(earned_usdt or 0)
    bot_rec["total_earned_usdt"] = round(
        float(bot_rec.get("total_earned_usdt", 0)) + earned, 6
    )
    bot_rec["rotations_count"] = int(bot_rec.get("rotations_count", 0)) + 1
    entry = {
        "ts": _now_iso(),
        "symbol": symbol,
        "earned": round(earned, 6),
    }
    if cycles is not None:
        entry["cycles"] = int(cycles)
    if reason:
        entry["reason"] = str(reason)[:80]
    bot_rec["history"].append(entry)
    # обрезаем историю
    if len(bot_rec["history"]) > HISTORY_LIMIT:
        bot_rec["history"] = bot_rec["history"][-HISTORY_LIMIT:]

    _write(path, state)
    return bot_rec


def get_bot_lifetime(bot_name: str, path: str = DEFAULT_PATH) -> Dict[str, Any]:
    """Вернуть запись по одному боту (или пустую заготовку)."""
    state = _read(path)
    rec = state["bots"].get(bot_name)
    if rec is None:
        return {"total_earned_usdt": 0.0, "rotations_count": 0, "history": []}
    return rec


def get_summary(path: str = DEFAULT_PATH) -> Dict[str, Any]:
    """
    Вернуть совокупный отчёт.
    {
      "total_earned_usdt": 5.23,
      "total_rotations": 9,
      "by_bot": { "arb_bot": {...}, ... },
      "updated_at": "..."
    }
    """
    state = _read(path)
    total = 0.0
    rotations = 0
    for rec in state["bots"].values():
        total += float(rec.get("total_earned_usdt", 0) or 0)
        rotations += int(rec.get("rotations_count", 0) or 0)
    return {
        "total_earned_usdt": round(total, 6),
        "total_rotations": rotations,
        "by_bot": dict(state["bots"]),
        "updated_at": state.get("updated_at", _now_iso()),
    }


def reset(path: str = DEFAULT_PATH) -> bool:
    """Полный сброс. Использовать осторожно (только для тестов / migrate)."""
    return _write(path, _empty_state())


# ══ CLI ═══════════════════════════════════════════════════════════════════
def _print_summary(path: str = DEFAULT_PATH) -> None:
    s = get_summary(path)
    print("Lifetime PnL")
    print(f"  обновлено: {s['updated_at']}")
    print(f"  всего ротаций: {s['total_rotations']}")
    print(f"  всего заработано: ${s['total_earned_usdt']:.4f}")
    print()
    print("Раздельно по ботам:")
    if not s["by_bot"]:
        print("  (пусто)")
        return
    for name in sorted(s["by_bot"].keys()):
        rec = s["by_bot"][name]
        print(f"  {name:10s}  ${float(rec.get('total_earned_usdt',0)):>8.4f}  "
              f"ротаций: {rec.get('rotations_count', 0)}")
        # последние 3 закрытия
        hist = rec.get("history", [])[-3:]
        for h in hist:
            ts = h.get("ts", "")[:19]
            extra = ""
            if "cycles" in h:
                extra += f" cycles={h['cycles']}"
            if "reason" in h:
                extra += f" reason={h['reason']}"
            print(f"      {ts}  {h.get('symbol','?'):14s} ${float(h.get('earned',0)):>7.4f}{extra}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--reset":
        if reset():
            print("lifetime_pnl.json: сброшен")
        else:
            print("ошибка сброса")
            sys.exit(1)
    else:
        _print_summary()
