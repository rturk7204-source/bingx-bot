#!/usr/bin/env python3
"""
tools/show_lifetime.py — расширенный отчёт по PnL.

Показывает:
  1. Текущие открытые позиции (как arb_state{N}.json показывает) — earned по
     ТЕКУЩЕЙ паре, плюс symbol/budget.
  2. Lifetime PnL — накопленное через все ротации (lifetime_pnl.json).
  3. Совокупный итог = earned по текущим + lifetime по закрытым.

Запуск: python3 tools/show_lifetime.py
"""
import json
import os
import sys

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BOT_DIR)

BOTS = [
    ("arb_bot",  "arb_state.json",  "1"),
    ("arb_bot2", "arb_state2.json", "2"),
    ("arb_bot3", "arb_state3.json", "3"),
    ("arb_bot4", "arb_state4.json", "4"),
    ("arb_bot5", "arb_state5.json", "5"),
    ("arb_bot6", "arb_state6.json", "6"),
]


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def main():
    print("=" * 72)
    print("PnL Snapshot")
    print("=" * 72)

    try:
        import lifetime_pnl
        lt = lifetime_pnl.get_summary()
    except Exception as e:
        print(f"(lifetime_pnl недоступен: {e})")
        lt = {"total_earned_usdt": 0.0, "total_rotations": 0, "by_bot": {}}

    print()
    print(f"{'Bot':6s} {'Symbol':14s} {'Status':3s}  "
          f"{'Budget':>9s} {'Now':>9s} {'Lifetime':>10s} {'Total':>10s}  Rot")
    print("-" * 72)

    grand_now = 0.0
    grand_life = 0.0
    grand_rot = 0

    for bot_name, state_file, label in BOTS:
        st = _load(os.path.join(BOT_DIR, state_file))
        sym = st.get("symbol", "-")
        budget = float(st.get("spot_budget", 0) or 0) + float(st.get("perp_margin", 0) or 0)
        now_earned = float(st.get("total_earned_usdt", 0) or 0)
        is_open = bool(st.get("position_open"))
        status = "🟢" if is_open else "⚪"

        bot_lt = lt.get("by_bot", {}).get(bot_name, {})
        lifetime = float(bot_lt.get("total_earned_usdt", 0) or 0)
        rotations = int(bot_lt.get("rotations_count", 0) or 0)
        total = now_earned + lifetime

        print(f"bot{label:<3s} {sym:<14s} {status}    "
              f"${budget:>7.1f}  ${now_earned:>7.4f}  ${lifetime:>8.4f}  ${total:>8.4f}  {rotations}")

        grand_now += now_earned
        grand_life += lifetime
        grand_rot += rotations

    print("-" * 72)
    grand_total = grand_now + grand_life
    print(f"{'TOTAL':6s} {'':14s} {'':3s}     "
          f"{'':>9s} ${grand_now:>7.4f}  ${grand_life:>8.4f}  ${grand_total:>8.4f}  {grand_rot}")
    print()
    print("Now      = заработано на текущих парах с момента входа")
    print("Lifetime = накоплено через все ротации (lifetime_pnl.json)")
    print("Total    = Now + Lifetime по этому боту")


if __name__ == "__main__":
    main()
