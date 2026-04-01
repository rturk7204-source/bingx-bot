import numpy as np

class SMCAnalyzer:
    def __init__(self, swing_len=10):
        self.swing_len = swing_len

    def get_swing_highs_lows(self, klines):
        """Находим свинг хаи и лои"""
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        closes = [float(k["close"]) for k in klines]
        n = self.swing_len

        swing_highs = []
        swing_lows = []

        for i in range(n, len(highs) - n):
            if highs[i] == max(highs[i-n:i+n+1]):
                swing_highs.append({"idx": i, "price": highs[i]})
            if lows[i] == min(lows[i-n:i+n+1]):
                swing_lows.append({"idx": i, "price": lows[i]})

        return swing_highs, swing_lows

    def detect_bos(self, klines):
        """Break of Structure — пробой структуры рынка"""
        try:
            swing_highs, swing_lows = self.get_swing_highs_lows(klines)
            if len(swing_highs) < 2 or len(swing_lows) < 2:
                return "NEUTRAL"

            closes = [float(k["close"]) for k in klines]
            current_price = closes[-1]

            last_high = swing_highs[-1]["price"]
            prev_high = swing_highs[-2]["price"]
            last_low = swing_lows[-1]["price"]
            prev_low = swing_lows[-2]["price"]

            # Бычий BOS — пробой предыдущего свинг хая
            if current_price > last_high and last_high > prev_high:
                return "BULLISH_BOS"

            # Медвежий BOS — пробой предыдущего свинг лоя
            if current_price < last_low and last_low < prev_low:
                return "BEARISH_BOS"

            # CHoCH — смена характера (разворот)
            if current_price > last_high and last_high < prev_high:
                return "BULLISH_CHOCH"
            if current_price < last_low and last_low > prev_low:
                return "BEARISH_CHOCH"

            return "NEUTRAL"
        except:
            return "NEUTRAL"

    def detect_order_blocks(self, klines):
        """Order Blocks — зоны крупных игроков"""
        try:
            if len(klines) < 20:
                return None, None

            opens = [float(k["open"]) for k in klines]
            closes = [float(k["close"]) for k in klines]
            highs = [float(k["high"]) for k in klines]
            lows = [float(k["low"]) for k in klines]
            volumes = [float(k["volume"]) for k in klines]

            avg_vol = np.mean(volumes[-50:]) if len(volumes) >= 50 else np.mean(volumes)
            current_price = closes[-1]

            bullish_ob = None
            bearish_ob = None

            # Ищем последний бычий OB — медвежья свеча перед сильным ростом
            for i in range(len(klines)-20, len(klines)-1):
                if i < 1:
                    continue
                # Медвежья свеча с высоким объёмом
                if closes[i] < opens[i] and volumes[i] > avg_vol * 1.5:
                    # После неё идёт рост
                    if closes[i+1] > opens[i+1] and closes[i+1] > highs[i]:
                        ob_high = max(opens[i], closes[i])
                        ob_low = min(opens[i], closes[i])
                        # Цена ещё не вошла в зону OB
                        if current_price > ob_low:
                            bullish_ob = {"high": ob_high, "low": ob_low, "idx": i}

            # Ищем последний медвежий OB — бычья свеча перед сильным падением
            for i in range(len(klines)-20, len(klines)-1):
                if i < 1:
                    continue
                if closes[i] > opens[i] and volumes[i] > avg_vol * 1.5:
                    if closes[i+1] < opens[i+1] and closes[i+1] < lows[i]:
                        ob_high = max(opens[i], closes[i])
                        ob_low = min(opens[i], closes[i])
                        if current_price < ob_high:
                            bearish_ob = {"high": ob_high, "low": ob_low, "idx": i}

            return bullish_ob, bearish_ob
        except:
            return None, None

    def detect_fvg(self, klines):
        """Fair Value Gap — ценовые разрывы"""
        try:
            if len(klines) < 5:
                return None, None

            highs = [float(k["high"]) for k in klines]
            lows = [float(k["low"]) for k in klines]
            current_price = float(klines[-1]["close"])

            bullish_fvg = None
            bearish_fvg = None

            # Ищем FVG в последних 30 свечах
            for i in range(max(2, len(klines)-30), len(klines)-1):
                # Бычий FVG: low[i+1] > high[i-1]
                if lows[i+1] > highs[i-1]:
                    gap_high = lows[i+1]
                    gap_low = highs[i-1]
                    # Цена выше FVG — зона поддержки
                    if current_price > gap_low:
                        bullish_fvg = {"high": gap_high, "low": gap_low}

                # Медвежий FVG: high[i+1] < low[i-1]
                if highs[i+1] < lows[i-1]:
                    gap_high = lows[i-1]
                    gap_low = highs[i+1]
                    # Цена ниже FVG — зона сопротивления
                    if current_price < gap_high:
                        bearish_fvg = {"high": gap_high, "low": gap_low}

            return bullish_fvg, bearish_fvg
        except:
            return None, None

    def detect_liquidity_sweep(self, klines):
        """Liquidity Sweep — сбор ликвидности"""
        try:
            if len(klines) < 20:
                return "NEUTRAL"

            highs = [float(k["high"]) for k in klines]
            lows = [float(k["low"]) for k in klines]
            closes = [float(k["close"]) for k in klines]

            # Берём максимум/минимум последних 20 свечей (без последней)
            recent_high = max(highs[-21:-1])
            recent_low = min(lows[-21:-1])
            last_high = highs[-1]
            last_low = lows[-1]
            last_close = closes[-1]

            # Бычий sweep: пробой лоя с закрытием выше — сбор стопов медведей
            if last_low < recent_low and last_close > recent_low:
                return "BULLISH_SWEEP"

            # Медвежий sweep: пробой хая с закрытием ниже — сбор стопов быков
            if last_high > recent_high and last_close < recent_high:
                return "BEARISH_SWEEP"

            return "NEUTRAL"
        except:
            return "NEUTRAL"

    def detect_volume_profile(self, klines, bins=20):
        """Volume Profile — Point of Control (POC)"""
        try:
            if len(klines) < 20:
                return None

            highs = [float(k["high"]) for k in klines]
            lows = [float(k["low"]) for k in klines]
            volumes = [float(k["volume"]) for k in klines]
            closes = [float(k["close"]) for k in klines]
            current_price = closes[-1]

            price_min = min(lows)
            price_max = max(highs)
            if price_max == price_min:
                return None

            bin_size = (price_max - price_min) / bins
            volume_by_price = [0.0] * bins

            for i in range(len(klines)):
                low = lows[i]
                high = highs[i]
                vol = volumes[i]
                for b in range(bins):
                    bin_low = price_min + b * bin_size
                    bin_high = bin_low + bin_size
                    overlap = max(0, min(high, bin_high) - max(low, bin_low))
                    if high - low > 0:
                        volume_by_price[b] += vol * overlap / (high - low)

            poc_bin = volume_by_price.index(max(volume_by_price))
            poc_price = price_min + (poc_bin + 0.5) * bin_size

            # Value Area (70% объёма вокруг POC)
            total_vol = sum(volume_by_price)
            target_vol = total_vol * 0.70
            vah_bin = poc_bin
            val_bin = poc_bin
            accumulated = volume_by_price[poc_bin]

            while accumulated < target_vol:
                up = volume_by_price[vah_bin + 1] if vah_bin + 1 < bins else 0
                down = volume_by_price[val_bin - 1] if val_bin - 1 >= 0 else 0
                if up >= down and vah_bin + 1 < bins:
                    vah_bin += 1
                    accumulated += volume_by_price[vah_bin]
                elif val_bin - 1 >= 0:
                    val_bin -= 1
                    accumulated += volume_by_price[val_bin]
                else:
                    break

            vah = price_min + (vah_bin + 1) * bin_size
            val = price_min + val_bin * bin_size
            tolerance = bin_size * 1.5

            signal = "NEUTRAL"
            if abs(current_price - poc_price) <= tolerance:
                if current_price > poc_price:
                    signal = "BULLISH"
                else:
                    signal = "BEARISH"
                print(f"[POC] Цена у POC {poc_price:.4f} — {signal}")
            elif current_price < val:
                signal = "BULLISH"
                print(f"[POC] Цена ниже Value Area ({val:.4f}) — покупка")
            elif current_price > vah:
                signal = "BEARISH"
                print(f"[POC] Цена выше Value Area ({vah:.4f}) — продажа")

            return {"poc": poc_price, "vah": vah, "val": val, "signal": signal}
        except:
            return None

    def detect_fibonacci(self, klines):
        """Fibonacci уровни от последнего значимого свинга"""
        try:
            if len(klines) < 20:
                return None

            highs = [float(k["high"]) for k in klines]
            lows = [float(k["low"]) for k in klines]
            closes = [float(k["close"]) for k in klines]
            current_price = closes[-1]

            # Находим последний значимый свинг хай и лой
            lookback = min(50, len(klines))
            swing_high = max(highs[-lookback:])
            swing_low = min(lows[-lookback:])
            swing_range = swing_high - swing_low

            if swing_range == 0:
                return None

            # Уровни Fibonacci
            levels = {
                "0.0":   swing_high,
                "0.236": swing_high - 0.236 * swing_range,
                "0.382": swing_high - 0.382 * swing_range,
                "0.5":   swing_high - 0.5   * swing_range,
                "0.618": swing_high - 0.618 * swing_range,
                "0.786": swing_high - 0.786 * swing_range,
                "1.0":   swing_low
            }

            # Проверяем близость цены к уровням (в пределах 0.3%)
            tolerance = swing_range * 0.003
            nearest = None
            for name, level in levels.items():
                if abs(current_price - level) <= tolerance:
                    nearest = {"level": name, "price": level}
                    print(f"[FIB] {klines[-1].get('symbol', '')} цена у уровня {name}: {level:.4f}")
                    break

            # Определяем сигнал по положению цены
            fib_618 = levels["0.618"]
            fib_382 = levels["0.382"]
            fib_500 = levels["0.5"]

            signal = "NEUTRAL"
            # Бычий сигнал — цена у 0.618 или 0.786 (зона покупок)
            if levels["0.786"] <= current_price <= levels["0.618"]:
                signal = "BULLISH"
                print(f"[FIB] Бычья зона 0.618-0.786: {levels['0.786']:.4f} - {fib_618:.4f}")
            # Медвежий сигнал — цена у 0.236 или 0.382 (зона продаж)
            elif levels["0.236"] >= current_price >= levels["0.382"]:
                signal = "BEARISH"
                print(f"[FIB] Медвежья зона 0.236-0.382: {levels['0.382']:.4f} - {levels['0.236']:.4f}")

            return {
                "levels": levels,
                "nearest": nearest,
                "signal": signal,
                "swing_high": swing_high,
                "swing_low": swing_low
            }
        except:
            return None


    def detect_breaker_block(self, klines):
        """Breaker Block — бывший OB который был пробит (сильный разворот)"""
        if len(klines) < 30:
            return None, None
        
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        closes = [float(k["close"]) for k in klines]
        current_price = closes[-1]
        
        bullish_breaker = None
        bearish_breaker = None
        
        # Ищем бывший медвежий OB который был пробит вверх (bullish breaker)
        for i in range(5, len(klines)-5):
            # Медвежья свеча (бывший OB)
            if closes[i] < closes[i-1]:
                ob_high = highs[i]
                ob_low = lows[i]
                # Проверяем что цена пробила этот OB вверх
                broken_up = any(closes[j] > ob_high for j in range(i+1, min(i+10, len(klines))))
                # И сейчас цена вернулась к уровню (ретест)
                if broken_up and ob_low <= current_price <= ob_high * 1.01:
                    bullish_breaker = {"high": ob_high, "low": ob_low, "index": i}
                    break
        
        # Ищем бывший бычий OB который был пробит вниз (bearish breaker)
        for i in range(5, len(klines)-5):
            if closes[i] > closes[i-1]:
                ob_high = highs[i]
                ob_low = lows[i]
                broken_down = any(closes[j] < ob_low for j in range(i+1, min(i+10, len(klines))))
                if broken_down and ob_low * 0.99 <= current_price <= ob_high:
                    bearish_breaker = {"high": ob_high, "low": ob_low, "index": i}
                    break
        
        return bullish_breaker, bearish_breaker

    def detect_market_structure_shift(self, klines):
        """MSS — ранний сигнал смены тренда (до BOS)"""
        if len(klines) < 20:
            return "NEUTRAL"
        
        closes = [float(k["close"]) for k in klines]
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        
        # Последние 10 свечей
        recent_closes = closes[-10:]
        recent_highs = highs[-10:]
        recent_lows = lows[-10:]
        
        # Бычий MSS: цена делает higher low после серии lower lows
        if (recent_lows[-1] > recent_lows[-3] and 
            recent_lows[-3] < recent_lows[-5] and
            recent_closes[-1] > recent_closes[-2]):
            return "BULLISH_MSS"
        
        # Медвежий MSS: цена делает lower high после серии higher highs
        if (recent_highs[-1] < recent_highs[-3] and 
            recent_highs[-3] > recent_highs[-5] and
            recent_closes[-1] < recent_closes[-2]):
            return "BEARISH_MSS"
        
        return "NEUTRAL"


    def detect_premium_discount(self, klines, lookback=30):
        """
        Premium/Discount зоны.
        Находим swing high и swing low за lookback свечей.
        Выше 50% от диапазона = Premium (только SHORT).
        Ниже 50% = Discount (только LONG).
        Возвращает: ("PREMIUM"/"DISCOUNT"/"EQUILIBRIUM", pct_position)
        """
        try:
            if len(klines) < lookback:
                return "EQUILIBRIUM", 50.0

            highs = [float(k["high"]) for k in klines[-lookback:]]
            lows = [float(k["low"]) for k in klines[-lookback:]]
            current = float(klines[-1]["close"])

            swing_high = max(highs)
            swing_low = min(lows)
            swing_range = swing_high - swing_low

            if swing_range <= 0:
                return "EQUILIBRIUM", 50.0

            pct = (current - swing_low) / swing_range * 100

            if pct >= 70:
                return "PREMIUM", round(pct, 1)
            elif pct <= 30:
                return "DISCOUNT", round(pct, 1)
            else:
                return "EQUILIBRIUM", round(pct, 1)
        except:
            return "EQUILIBRIUM", 50.0


    def check_ote_zone(self, klines):
        """OTE (Optimal Trade Entry) — цена в зоне 62-79% Fibonacci от последнего BOS свинга"""
        try:
            if len(klines) < 30:
                return "NEUTRAL", 0.0
            swing_highs, swing_lows = self.get_swing_highs_lows(klines)
            if len(swing_highs) < 2 or len(swing_lows) < 2:
                return "NEUTRAL", 0.0
            closes = [float(k["close"]) for k in klines]
            current_price = closes[-1]
            # Определяем тренд по последним свингам
            last_sh = swing_highs[-1]["price"]
            last_sl = swing_lows[-1]["price"]
            swing_range = abs(last_sh - last_sl)
            if swing_range == 0:
                return "NEUTRAL", 0.0
            # Бычий OTE: цена откатилась в 62-79% от swing low к swing high
            fib_62_bull = last_sh - 0.618 * swing_range
            fib_79_bull = last_sh - 0.786 * swing_range
            if fib_79_bull <= current_price <= fib_62_bull:
                pct = (last_sh - current_price) / swing_range * 100
                print(f"[OTE] Бычья OTE зона: цена {current_price:.4f} в {pct:.1f}% retracement ({fib_79_bull:.4f}-{fib_62_bull:.4f})")
                return "BULLISH_OTE", pct
            # Медвежий OTE: цена откатилась вверх в 62-79% от swing high к swing low
            fib_62_bear = last_sl + 0.618 * swing_range
            fib_79_bear = last_sl + 0.786 * swing_range
            if fib_62_bear <= current_price <= fib_79_bear:
                pct = (current_price - last_sl) / swing_range * 100
                print(f"[OTE] Медвежья OTE зона: цена {current_price:.4f} в {pct:.1f}% retracement ({fib_62_bear:.4f}-{fib_79_bear:.4f})")
                return "BEARISH_OTE", pct
            return "NEUTRAL", 0.0
        except:
            return "NEUTRAL", 0.0


    def is_ob_unmitigated(self, ob, klines, ob_type="bullish"):
        """Проверяет что Order Block ещё не был протестирован ценой после формирования"""
        try:
            if not ob or "idx" not in ob:
                return False
            idx = ob["idx"]
            closes = [float(k["close"]) for k in klines]
            # Проверяем свечи ПОСЛЕ формирования OB
            for i in range(idx + 2, len(klines) - 1):
                if ob_type == "bullish":
                    # Если цена опускалась В зону OB и отскакивала — OB mitigated
                    if closes[i] <= ob["high"] and closes[i] >= ob["low"]:
                        return False
                else:
                    if closes[i] >= ob["low"] and closes[i] <= ob["high"]:
                        return False
            return True
        except:
            return True

    def analyze(self, klines):
        """Полный SMC анализ"""
        if len(klines) < 30:
            return {"signal": "NEUTRAL", "score": 0, "details": {}}

        bos = self.detect_bos(klines)
        bullish_ob, bearish_ob = self.detect_order_blocks(klines)
        bullish_fvg, bearish_fvg = self.detect_fvg(klines)
        sweep = self.detect_liquidity_sweep(klines)
        current_price = float(klines[-1]["close"])

        # OTE зона
        ote_signal, ote_pct = self.check_ote_zone(klines)
        details_ote = {"ote": ote_signal, "ote_pct": ote_pct}

        # Unmitigated OB check
        ob_bull_unmit = self.is_ob_unmitigated(bullish_ob, klines, "bullish") if bullish_ob else False
        ob_bear_unmit = self.is_ob_unmitigated(bearish_ob, klines, "bearish") if bearish_ob else False
        if bullish_ob and not ob_bull_unmit:
            print(f"[SMC] Бычий OB MITIGATED — отклоняем")
            bullish_ob = None
        if bearish_ob and not ob_bear_unmit:
            print(f"[SMC] Медвежий OB MITIGATED — отклоняем")
            bearish_ob = None

        buy_score = 0
        sell_score = 0
        details = {"bos": bos, "sweep": sweep, "ote": ote_signal, "ote_pct": ote_pct}

        # Premium/Discount зоны
        pd_zone, pd_pct = self.detect_premium_discount(klines)
        details["premium_discount"] = {"zone": pd_zone, "pct": pd_pct}
        if pd_zone == "PREMIUM":
            print(f"[SMC] Premium зона ({pd_pct:.0f}%) — SHORT предпочтителен")
        elif pd_zone == "DISCOUNT":
            print(f"[SMC] Discount зона ({pd_pct:.0f}%) — LONG предпочтителен")

        # BOS сигналы
        if bos == "BULLISH_BOS":
            buy_score += 2
        elif bos == "BULLISH_CHOCH":
            buy_score += 3  # Сильный разворот
        elif bos == "BEARISH_BOS":
            sell_score += 2
        elif bos == "BEARISH_CHOCH":
            sell_score += 3

        # Order Block — цена в зоне OB
        if bullish_ob and bullish_ob["low"] <= current_price <= bullish_ob["high"]:
            buy_score += 2
            details["bullish_ob"] = bullish_ob
            print(f"[SMC] Цена в бычьем OB: {bullish_ob['low']:.4f} - {bullish_ob['high']:.4f}")
        if bearish_ob and bearish_ob["low"] <= current_price <= bearish_ob["high"]:
            sell_score += 2
            details["bearish_ob"] = bearish_ob
            print(f"[SMC] Цена в медвежьем OB: {bearish_ob['low']:.4f} - {bearish_ob['high']:.4f}")

        # FVG — цена в зоне разрыва
        if bullish_fvg and bullish_fvg["low"] <= current_price <= bullish_fvg["high"]:
            buy_score += 1
            details["bullish_fvg"] = bullish_fvg
            print(f"[SMC] Бычий FVG: {bullish_fvg['low']:.4f} - {bullish_fvg['high']:.4f}")
        if bearish_fvg and bearish_fvg["low"] <= current_price <= bearish_fvg["high"]:
            sell_score += 1
            details["bearish_fvg"] = bearish_fvg
            print(f"[SMC] Медвежий FVG: {bearish_fvg['low']:.4f} - {bearish_fvg['high']:.4f}")

        # Liquidity Sweep — сильный сигнал разворота
        if sweep == "BULLISH_SWEEP":
            buy_score += 2
            print(f"[SMC] Бычий ликвидити свип!")
        elif sweep == "BEARISH_SWEEP":
            sell_score += 2
            print(f"[SMC] Медвежий ликвидити свип!")

        # OTE зона — сильный бонус
        if ote_signal == "BULLISH_OTE":
            buy_score += 2
            print(f"[OTE] +2 к BUY — цена в OTE зоне")
        elif ote_signal == "BEARISH_OTE":
            sell_score += 2
            print(f"[OTE] +2 к SELL — цена в OTE зоне")

        # Обязательный Liquidity Sweep для высокой конфлюэнции
        details["sweep_required"] = sweep != "NEUTRAL"

        # Confluence — считаем сколько ТИПОВ сигналов совпало
        bull_confluence = 0
        bear_confluence = 0

        if bos in ("BULLISH_BOS", "BULLISH_CHOCH"):
            bull_confluence += 1
        elif bos in ("BEARISH_BOS", "BEARISH_CHOCH"):
            bear_confluence += 1

        if bullish_ob and details.get("bullish_ob"):
            bull_confluence += 1
        if bearish_ob and details.get("bearish_ob"):
            bear_confluence += 1

        if bullish_fvg and details.get("bullish_fvg"):
            bull_confluence += 1
        if bearish_fvg and details.get("bearish_fvg"):
            bear_confluence += 1

        if sweep == "BULLISH_SWEEP":
            bull_confluence += 1
        elif sweep == "BEARISH_SWEEP":
            bear_confluence += 1

        if ote_signal == "BULLISH_OTE":
            bull_confluence += 1
        elif ote_signal == "BEARISH_OTE":
            bear_confluence += 1

        details["bull_confluence"] = bull_confluence
        details["bear_confluence"] = bear_confluence

        if buy_score > sell_score and buy_score >= 2:
            signal = "BULLISH"
        elif sell_score > buy_score and sell_score >= 2:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        # Volume Profile POC
        vp = self.detect_volume_profile(klines)
        if vp:
            details["volume_profile"] = vp
            if vp["signal"] == "BULLISH":
                buy_score += 1
            elif vp["signal"] == "BEARISH":
                sell_score += 1

        # Fibonacci уровни
        fib = self.detect_fibonacci(klines)
        if fib:
            details["fibonacci"] = fib
            if fib["signal"] == "BULLISH":
                buy_score += 1
                print(f"[FIB] Бычий сигнал по Fibonacci")
            elif fib["signal"] == "BEARISH":
                sell_score += 1
                print(f"[FIB] Медвежий сигнал по Fibonacci")

        # Breaker Block — бывший OB пробитый ценой
        bull_breaker, bear_breaker = self.detect_breaker_block(klines)
        if bull_breaker and bull_breaker["low"] <= current_price <= bull_breaker["high"] * 1.01:
            buy_score += 2
            bull_confluence += 1
            details["bull_breaker"] = bull_breaker
            print(f"[SMC] Бычий Breaker Block: {bull_breaker['low']:.4f} - {bull_breaker['high']:.4f}")
        if bear_breaker and bear_breaker["low"] * 0.99 <= current_price <= bear_breaker["high"]:
            sell_score += 2
            bear_confluence += 1
            details["bear_breaker"] = bear_breaker
            print(f"[SMC] Медвежий Breaker Block: {bear_breaker['low']:.4f} - {bear_breaker['high']:.4f}")

        # MSS — ранний сигнал смены тренда
        mss = self.detect_market_structure_shift(klines)
        if mss == "BULLISH_MSS":
            buy_score += 1
            details["mss"] = "BULLISH"
            print(f"[SMC] Bullish MSS — смена структуры")
        elif mss == "BEARISH_MSS":
            sell_score += 1
            details["mss"] = "BEARISH"
            print(f"[SMC] Bearish MSS — смена структуры")

        details["buy_score"] = buy_score
        details["sell_score"] = sell_score

        # Пересчитываем сигнал с учётом всех факторов
        if buy_score > sell_score and buy_score >= 2:
            signal = "BULLISH"
        elif sell_score > buy_score and sell_score >= 2:
            signal = "BEARISH"

        return {"signal": signal, "score": max(buy_score, sell_score), "details": details}
