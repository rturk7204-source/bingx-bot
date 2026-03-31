from datetime import datetime

class RiskManager:
    def __init__(self, stop_loss_pct=3.0, take_profit_pct=999.0, trailing_pct=1.0, partial_tp_pct=2.0, partial_tp_ratio=0.6):
        self.stop_loss_pct = stop_loss_pct
        self.atr_sl_multiplier = 2.0
        self.atr_sl_min = 1.5
        self.atr_sl_max = 5.0
        self.take_profit_pct = take_profit_pct
        self.trailing_pct = trailing_pct
        self.partial_tp_pct = partial_tp_pct
        self.partial_tp_ratio = partial_tp_ratio
        self.positions = {}

        # HARD STOP
        self.hard_stop_pct = 30.0
        self.toxic_time_hours = 6
        self.toxic_loss_pct = 20.0

        # ПАТЧ 25: Менее агрессивный trailing для SMC
        # SMC ретест OB может быть 1-2%, поэтому trail шире на начальных уровнях
        self.trailing_steps = [
            (1.5, 1.2),   # было (0.8, 0.5) — слишком рано, теперь ждём +1.5%
            (2.5, 1.0),   # было (1.5, 0.8)
            (4.0, 0.8),   # было (3.0, 0.7)
            (6.0, 0.6),   # было (5.0, 0.5)
            (9.0, 0.5),   # было (8.0, 0.4)
            (14.0, 0.4),  # было (12.0, 0.3)
        ]

    def get_trailing_pct(self, profit_pct):
        current_step = self.trailing_pct
        for min_profit, trail_pct in self.trailing_steps:
            if profit_pct >= min_profit:
                current_step = trail_pct
        return current_step

    def calc_atr_pct(self, symbol):
        if symbol in self.positions:
            return self.positions[symbol].get("atr_pct", None)
        return None

    def calc_dynamic_sl(self, klines, side="LONG"):
        try:
            if len(klines) < 20:
                return self.stop_loss_pct
            highs  = [float(k["high"])  for k in klines[-20:]]
            lows   = [float(k["low"])   for k in klines[-20:]]
            closes = [float(k["close"]) for k in klines[-20:]]
            trs = []
            for i in range(1, len(closes)):
                tr = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i-1]),
                         abs(lows[i]  - closes[i-1]))
                trs.append(tr)
            atr    = sum(trs) / len(trs)
            atr_pct = atr / closes[-1] * 100
            sl_pct  = atr_pct * self.atr_sl_multiplier
            sl_pct  = max(self.atr_sl_min, min(self.atr_sl_max, sl_pct))
            return round(sl_pct, 2)
        except:
            return self.stop_loss_pct

    def add_position_with_atr(self, symbol, entry_price, side, qty, klines=None):
        atr_sl  = self.calc_dynamic_sl(klines, side) if klines else self.stop_loss_pct
        atr_pct = atr_sl / self.atr_sl_multiplier if klines else None
        self.positions[symbol] = {
            "entry_price": float(entry_price),
            "side": side,
            "qty": float(qty),
            "max_price": float(entry_price),
            "trailing_stop": None,
            "trailing_pct_current": self.trailing_pct,
            "partial_tp_done": False,
            "atr_pct": atr_pct,
            "dynamic_sl_pct": atr_sl,
            "open_time": datetime.now(),
        }
        print(f"[RISK] Position added: {symbol} {side} @ {entry_price} | ATR SL: {atr_sl:.2f}% | qty: {qty}")

    def add_position(self, symbol, entry_price, side, qty, atr_pct=None):
        self.positions[symbol] = {
            "entry_price": float(entry_price),
            "side": side,
            "qty": float(qty),
            "max_price": float(entry_price),
            "trailing_stop": None,
            "trailing_pct_current": self.trailing_pct,
            "partial_tp_done": False,
            "atr_pct": atr_pct,
            "open_time": datetime.now(),
        }
        print(f"[RISK] Position added: {symbol} {side} @ {entry_price}, qty: {qty}")

    def remove_position(self, symbol):
        if symbol in self.positions:
            del self.positions[symbol]
            print(f"[RISK] Position removed: {symbol}")

    def check_position(self, symbol, current_price):
        if symbol not in self.positions:
            return None

        pos           = self.positions[symbol]
        entry_price   = pos["entry_price"]
        side          = pos["side"]
        current_price = float(current_price)

        if side == "LONG":
            price_change_pct = (current_price - entry_price) / entry_price * 100
        else:
            price_change_pct = (entry_price - current_price) / entry_price * 100

        # HARD STOP -30%
        if price_change_pct <= -self.hard_stop_pct:
            print(f"[HARD-STOP] {symbol} | PnL: {price_change_pct:.2f}% — принудительное закрытие!")
            return "HARD_STOP"

        # TOXIC: >6ч в минусе >-20%
        open_time = pos.get("open_time")
        if open_time:
            hours_open = (datetime.now() - open_time).total_seconds() / 3600
            if hours_open >= self.toxic_time_hours and price_change_pct <= -self.toxic_loss_pct:
                print(f"[TOXIC] {symbol} | {hours_open:.1f}ч в минусе {price_change_pct:.2f}% — закрываем!")
                return "TOXIC_CLOSE"

        if side == "LONG":
            if not pos["partial_tp_done"] and price_change_pct >= self.partial_tp_pct:
                pos["partial_tp_done"] = True
                print(f"[PARTIAL TP] {symbol} +{price_change_pct:.2f}%")
                return "PARTIAL_TP"

            if not pos.get("breakeven_done", False):
                if (current_price - entry_price) / entry_price * 100 >= 1.5:
                    be_price = entry_price * 1.001
                    if pos["trailing_stop"] is None or be_price > pos.get("trailing_stop", 0):
                        pos["trailing_stop"] = be_price
                        pos["breakeven_done"] = True
                        print(f"[BE] {symbol} break-even @ {be_price:.4f}")

            if current_price > pos["max_price"]:
                pos["max_price"] = current_price
            max_profit_pct = (pos["max_price"] - entry_price) / entry_price * 100
            atr_pct = pos.get("atr_pct")
            if atr_pct and atr_pct > 0:
                trail_pct = min(max(self.get_trailing_pct(max_profit_pct), atr_pct * 1.5), 3.0)
            else:
                trail_pct = self.get_trailing_pct(max_profit_pct)
            if max_profit_pct >= self.trailing_pct:
                new_trailing = pos["max_price"] * (1 - trail_pct / 100)
                if pos["trailing_stop"] is None or new_trailing > pos["trailing_stop"]:
                    pos["trailing_stop"] = new_trailing
                    pos["trailing_pct_current"] = trail_pct
            if pos["trailing_stop"] and current_price <= pos["trailing_stop"]:
                print(f"[TRAIL] TRAILING STOP! {symbol} @ {current_price:.4f}")
                return "TRAILING_STOP"

        else:  # SHORT
            if not pos["partial_tp_done"] and price_change_pct >= self.partial_tp_pct:
                pos["partial_tp_done"] = True
                print(f"[PARTIAL TP] {symbol} SHORT +{price_change_pct:.2f}%")
                return "PARTIAL_TP"

            if not pos.get("breakeven_done", False):
                if (entry_price - current_price) / entry_price * 100 >= 1.5:
                    be_price = entry_price * 0.999
                    if pos["trailing_stop"] is None or be_price < pos.get("trailing_stop", float("inf")):
                        pos["trailing_stop"] = be_price
                        pos["breakeven_done"] = True
                        print(f"[BE] {symbol} SHORT break-even @ {be_price:.4f}")

            if current_price < pos["max_price"] or pos["max_price"] == entry_price:
                pos["max_price"] = current_price
            max_profit_pct = (entry_price - pos["max_price"]) / entry_price * 100
            atr_pct = pos.get("atr_pct")
            if atr_pct and atr_pct > 0:
                trail_pct = min(max(self.get_trailing_pct(max_profit_pct), atr_pct * 1.5), 3.0)
            else:
                trail_pct = self.get_trailing_pct(max_profit_pct)
            if max_profit_pct >= self.trailing_pct:
                new_trailing = pos["max_price"] * (1 + trail_pct / 100)
                if pos["trailing_stop"] is None or new_trailing < pos["trailing_stop"]:
                    pos["trailing_stop"] = new_trailing
                    pos["trailing_pct_current"] = trail_pct
            if pos["trailing_stop"] and current_price >= pos["trailing_stop"]:
                print(f"[TRAIL] TRAILING STOP SHORT! {symbol} @ {current_price:.4f}")
                return "TRAILING_STOP"

        sl_pct = pos.get("dynamic_sl_pct", self.stop_loss_pct)
        if price_change_pct <= -sl_pct:
            print(f"[RISK] STOP-LOSS! {symbol} | Loss: {price_change_pct:.2f}%")
            return "STOP_LOSS"

        if price_change_pct >= self.take_profit_pct:
            print(f"[RISK] TAKE-PROFIT! {symbol} | Profit: {price_change_pct:.2f}%")
            return "TAKE_PROFIT"

        return None

    def get_fib_tp_levels(self, symbol, current_price):
        if symbol not in self.positions:
            return []
        pos   = self.positions[symbol]
        entry = pos["entry_price"]
        side  = pos["side"]
        swing_range = entry * 0.03
        if side == "LONG":
            levels = [
                {"level": 0.382, "price": entry + swing_range * 0.382, "ratio": 0.30},
                {"level": 0.618, "price": entry + swing_range * 0.618, "ratio": 0.40},
                {"level": 1.0,   "price": entry + swing_range * 1.0,   "ratio": 0.30},
            ]
        else:
            levels = [
                {"level": 0.382, "price": entry - swing_range * 0.382, "ratio": 0.30},
                {"level": 0.618, "price": entry - swing_range * 0.618, "ratio": 0.40},
                {"level": 1.0,   "price": entry - swing_range * 1.0,   "ratio": 0.30},
            ]
        return [l for l in levels if not pos.get(f"fib_tp_{l['level']}_done", False)]

    def check_fib_tp(self, symbol, current_price):
        if symbol not in self.positions:
            return None
        pos   = self.positions[symbol]
        entry = pos["entry_price"]
        side  = pos["side"]
        swing_range = entry * 0.03
        fib_levels = [(0.382, 0.382, 0.30), (0.618, 0.618, 0.40)]
        for name, fib, ratio in fib_levels:
            key = f"fib_tp_{name}_done"
            if pos.get(key, False):
                continue
            if side == "LONG":
                target = entry + swing_range * fib
                if current_price >= target:
                    pos[key] = True
                    qty = (pos.get("qty") or 0) * ratio
                    if qty <= 0: continue
                    print(f"[FIB TP] {symbol} Fibonacci {name} @ {current_price:.4f} ({int(ratio*100)}%)")
                    return {"level": name, "qty": qty, "ratio": ratio}
            else:
                target = entry - swing_range * fib
                if current_price <= target:
                    pos[key] = True
                    qty = (pos.get("qty") or 0) * ratio
                    if qty <= 0: continue
                    print(f"[FIB TP] {symbol} SHORT Fibonacci {name} @ {current_price:.4f} ({int(ratio*100)}%)")
                    return {"level": name, "qty": qty, "ratio": ratio}
        return None

    def get_partial_qty(self, symbol):
        if symbol not in self.positions:
            return 0
        return self.positions[symbol]["qty"] * self.partial_tp_ratio

    def get_position_pnl(self, symbol, current_price):
        if symbol not in self.positions:
            return 0
        pos = self.positions[symbol]
        entry_price = pos["entry_price"]
        side = pos["side"]
        if side == "LONG":
            return (float(current_price) - entry_price) / entry_price * 100
        else:
            return (entry_price - float(current_price)) / entry_price * 100

    # ПАТЧ 17: 3-уровневый partial TP
    def check_multi_partial_tp(self, symbol, current_price):
        """
        3-уровневый partial TP на основе ATR:
        - Уровень 1: +1 ATR -> закрыть 33%
        - Уровень 2: +2 ATR -> закрыть 33%
        - Уровень 3: trail остаток
        Возвращает: {"action": "PARTIAL_33"/"PARTIAL_66"/"NONE", "pnl_pct": float}
        """
        if symbol not in self.positions:
            return {"action": "NONE", "pnl_pct": 0}

        pos = self.positions[symbol]
        entry = pos.get("entry_price", 0)
        side = pos.get("side", "LONG")
        atr_pct = pos.get("atr_pct", 1.5)

        if entry <= 0:
            return {"action": "NONE", "pnl_pct": 0}

        if side == "LONG":
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100

        # Трекаем уровни закрытия
        tp_level = pos.get("tp_level", 0)

        # Уровень 1: +1 ATR
        if tp_level == 0 and pnl_pct >= atr_pct * 1.0:
            self.positions[symbol]["tp_level"] = 1
            print(f"[TP] {symbol}: Уровень 1 (+{atr_pct:.1f}%) -> закрыть 33%")
            return {"action": "PARTIAL_33", "pnl_pct": round(pnl_pct, 2)}

        # Уровень 2: +2 ATR
        if tp_level == 1 and pnl_pct >= atr_pct * 2.0:
            self.positions[symbol]["tp_level"] = 2
            print(f"[TP] {symbol}: Уровень 2 (+{atr_pct*2:.1f}%) -> закрыть 33%")
            return {"action": "PARTIAL_33", "pnl_pct": round(pnl_pct, 2)}

        # Уровень 3: остаток на trail (управляется основным trailing)
        return {"action": "NONE", "pnl_pct": round(pnl_pct, 2)}

    # ПАТЧ 21: Breakeven SL
    def check_breakeven(self, symbol, current_price):
        """
        После +0.5% прибыли двигаем SL на entry price (breakeven).
        Возвращает True если SL обновлён.
        """
        if symbol not in self.positions:
            return False

        pos = self.positions[symbol]
        entry = pos.get('entry_price', 0)
        side = pos.get('side', 'LONG')
        be_set = pos.get('breakeven_set', False)

        if entry <= 0 or be_set:
            return False

        if side == 'LONG':
            pnl_pct = (current_price - entry) / entry * 100
        else:
            pnl_pct = (entry - current_price) / entry * 100

        if pnl_pct >= 0.5:
            # Двигаем SL на entry + 0.1% (чтобы покрыть комиссию)
            self.positions[symbol]['breakeven_set'] = True
            self.positions[symbol]['breakeven_price'] = entry
            print(f'[BE] {symbol}: Breakeven SL активирован @ {entry:.4f} (PnL={pnl_pct:.2f}%)')
            return True

        return False

    def is_breakeven_hit(self, symbol, current_price):
        """Проверяет сработал ли breakeven SL"""
        if symbol not in self.positions:
            return False
        pos = self.positions[symbol]
        if not pos.get('breakeven_set', False):
            return False
        be_price = pos.get('breakeven_price', 0)
        side = pos.get('side', 'LONG')
        if side == 'LONG' and current_price <= be_price:
            print(f'[BE] {symbol}: Breakeven SL сработал @ {current_price:.4f}')
            return True
        if side == 'SHORT' and current_price >= be_price:
            print(f'[BE] {symbol}: Breakeven SL сработал @ {current_price:.4f}')
            return True
        return False

    # ПАТЧ 24: SMC-based SL за swing low/high
    def calc_smc_sl(self, klines, side="LONG", buffer_pct=0.3):
        """
        SL за ближайший swing low (LONG) или swing high (SHORT).
        Если swing не найден — fallback на ATR SL.
        buffer_pct — буфер за swing level (чтобы не снесло тенью).
        """
        try:
            if not klines or len(klines) < 20:
                return self.calc_dynamic_sl(klines, side)

            highs = [float(k["high"]) for k in klines[-30:]]
            lows = [float(k["low"]) for k in klines[-30:]]
            closes = [float(k["close"]) for k in klines[-30:]]
            current = closes[-1]

            if side == "LONG":
                # Ищем ближайший swing low (low ниже 2 соседей с каждой стороны)
                swing_lows = []
                for i in range(2, len(lows) - 2):
                    if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
                        swing_lows.append(lows[i])

                if swing_lows:
                    # Берём ближайший swing low ниже текущей цены
                    valid = [sl for sl in swing_lows if sl < current]
                    if valid:
                        nearest_sl = max(valid)  # ближайший снизу
                        sl_pct = (current - nearest_sl) / current * 100 + buffer_pct
                        sl_pct = max(self.atr_sl_min, min(self.atr_sl_max, sl_pct))
                        print(f"[SMC-SL] LONG: swing low={nearest_sl:.4f}, SL={sl_pct:.2f}%")
                        return round(sl_pct, 2)

            elif side == "SHORT":
                # Ищем ближайший swing high
                swing_highs = []
                for i in range(2, len(highs) - 2):
                    if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                        swing_highs.append(highs[i])

                if swing_highs:
                    valid = [sh for sh in swing_highs if sh > current]
                    if valid:
                        nearest_sh = min(valid)  # ближайший сверху
                        sl_pct = (nearest_sh - current) / current * 100 + buffer_pct
                        sl_pct = max(self.atr_sl_min, min(self.atr_sl_max, sl_pct))
                        print(f"[SMC-SL] SHORT: swing high={nearest_sh:.4f}, SL={sl_pct:.2f}%")
                        return round(sl_pct, 2)

            # Fallback на ATR
            return self.calc_dynamic_sl(klines, side)

        except Exception as e:
            print(f"[SMC-SL] error: {e}")
            return self.calc_dynamic_sl(klines, side)
