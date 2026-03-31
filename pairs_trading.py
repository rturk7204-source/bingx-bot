#!/usr/bin/env python3
"""
Pairs Trading — статистический арбитраж.
Полностью изолирован от SMC.
Отдельный бюджет, отдельные позиции.
"""

import numpy as np
import requests
import json
from datetime import datetime, timezone
from pathlib import Path


class PairsTrader:
    def __init__(self, api, notifier=None, analytics=None, budget_cap_usdt=120.0):
        self.api = api
        self.notifier = notifier
        self.analytics = analytics
        self.bingx_api = "https://open-api.bingx.com"

        self.budget_cap_usdt = budget_cap_usdt
        self.max_position_size = 30.0
        self.max_pairs_open = 2

        self.pairs = [
            ("ETH-USDT", "SOL-USDT"),
            ("ARB-USDT", "OP-USDT"),
            ("FET-USDT", "TAO-USDT"),
        ]

        self.lookback = 72
        self.z_entry = 2.0
        self.z_exit = 0.3
        self.z_stop = 3.5
        self.min_correlation = 0.7

        self.state_file = Path("/root/bingx-bot/pairs_state.json")
        self.open_pairs = {}
        self.load_state()

    def log(self, msg):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = f"[PAIRS] {ts} {msg}"
        print(line, flush=True)
        if self.notifier:
            try:
                self.notifier.send_message(line)
            except Exception:
                pass
    def load_state(self):
        try:
            if self.state_file.exists():
                self.open_pairs = json.loads(self.state_file.read_text(encoding="utf-8"))
            else:
                self.open_pairs = {}
        except Exception as e:
            self.log(f"load_state error: {e}")
            self.open_pairs = {}

    def save_state(self):
        try:
            self.state_file.write_text(json.dumps(self.open_pairs, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self.log(f"save_state error: {e}")

    def fetch_klines(self, symbol, interval="1h", limit=100):
        url = f"{self.bingx_api}/openApi/swap/v2/quote/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        try:
            r = requests.get(url, params=params, timeout=10)
            data = r.json()
            if data.get("code") != 0:
                self.log(f"fetch_klines error {symbol}: {data}")
                return []
            return data.get("data", []) or []
        except Exception as e:
            self.log(f"fetch_klines exception {symbol}: {e}")
            return []

    def get_close_prices(self, symbol):
        klines = self.fetch_klines(symbol, interval="1h", limit=self.lookback + 5)
        if not klines:
            return []
        closes = []
        for k in klines[-self.lookback:]:
            try:
                closes.append(float(k["close"]))
            except Exception:
                pass
        return closes

    def calc_correlation(self, a_prices, b_prices):
        if len(a_prices) < self.lookback or len(b_prices) < self.lookback:
            return 0.0
        try:
            return float(np.corrcoef(a_prices, b_prices)[0, 1])
        except Exception:
            return 0.0
    def get_spread_zscore(self, a_prices, b_prices):
        spread = np.array(a_prices, dtype=float) - np.array(b_prices, dtype=float)
        if len(spread) < 20:
            return 0.0, 0.0, 0.0, 0.0
        mu = float(np.mean(spread))
        sigma = float(np.std(spread))
        current = float(spread[-1])
        if sigma == 0:
            return 0.0, current, mu, sigma
        z = (current - mu) / sigma
        return float(z), current, mu, sigma

    def get_pairs_budget_used(self):
        return len(self.open_pairs) * (self.max_position_size * 2)

    def can_open_new_pair(self):
        if len(self.open_pairs) >= self.max_pairs_open:
            return False
        used = self.get_pairs_budget_used()
        return (used + self.max_position_size * 2) <= self.budget_cap_usdt

    def open_pair(self, sym_a, sym_b, zscore, corr):
        pair_key = f"{sym_a}|{sym_b}"
        if pair_key in self.open_pairs:
            return

        if zscore >= self.z_entry:
            side = "SHORT_A_LONG_B"
        elif zscore <= -self.z_entry:
            side = "LONG_A_SHORT_B"
        else:
            return

        self.open_pairs[pair_key] = {
            "a": sym_a,
            "b": sym_b,
            "side": side,
            "entry_z": round(float(zscore), 4),
            "corr": round(float(corr), 4),
            "budget_per_leg": self.max_position_size,
            "opened_at": datetime.now(timezone.utc).isoformat()
        }
        self.save_state()
        self.log(f"OPEN {pair_key} {side} z={zscore:.2f} corr={corr:.2f} budget={self.max_position_size}+{self.max_position_size}")

    def close_pair(self, pair_key, reason, zscore):
        pair = self.open_pairs.get(pair_key)
        if not pair:
            return
        self.log(f"CLOSE {pair_key} reason={reason} z={zscore:.2f} side={pair['side']}")
        del self.open_pairs[pair_key]
        self.save_state()
    def manage_open_pairs(self):
        for pair_key in list(self.open_pairs.keys()):
            pair = self.open_pairs[pair_key]
            a_prices = self.get_close_prices(pair["a"])
            b_prices = self.get_close_prices(pair["b"])
            if len(a_prices) < self.lookback or len(b_prices) < self.lookback:
                self.log(f"skip manage {pair_key}: not enough data")
                continue

            corr = self.calc_correlation(a_prices, b_prices)
            zscore, spread, mu, sigma = self.get_spread_zscore(a_prices, b_prices)

            if abs(zscore) <= self.z_exit:
                self.close_pair(pair_key, "mean_reversion", zscore)
            elif abs(zscore) >= self.z_stop:
                self.close_pair(pair_key, "z_stop", zscore)
            elif corr < self.min_correlation:
                self.close_pair(pair_key, "correlation_break", zscore)

    def scan_new_pairs(self):
        for sym_a, sym_b in self.pairs:
            if not self.can_open_new_pair():
                return

            pair_key = f"{sym_a}|{sym_b}"
            if pair_key in self.open_pairs:
                continue

            a_prices = self.get_close_prices(sym_a)
            b_prices = self.get_close_prices(sym_b)

            if len(a_prices) < self.lookback or len(b_prices) < self.lookback:
                self.log(f"skip {pair_key}: not enough data")
                continue

            corr = self.calc_correlation(a_prices, b_prices)
            if corr < self.min_correlation:
                self.log(f"skip {pair_key}: corr={corr:.2f} < {self.min_correlation}")
                continue

            zscore, spread, mu, sigma = self.get_spread_zscore(a_prices, b_prices)
            self.log(f"scan {pair_key}: corr={corr:.2f} z={zscore:.2f}")

            if abs(zscore) >= self.z_entry:
                self.open_pair(sym_a, sym_b, zscore, corr)

    def run_cycle(self):
        try:
            self.manage_open_pairs()
            self.scan_new_pairs()
            self.log(f"cycle ok | open_pairs={len(self.open_pairs)} | budget_used={self.get_pairs_budget_used():.2f}/{self.budget_cap_usdt:.2f}")
        except Exception as e:
            self.log(f"run_cycle fatal error: {e}")
