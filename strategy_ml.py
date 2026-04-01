import numpy as np
import pandas as pd
from datetime import datetime, timezone
from ml_predictor import MLPredictor
from candle_patterns import CandlePatterns
from fear_greed import FearGreedIndex
from news_analyzer import NewsAnalyzer
try:
    from lstm_predictor import LSTMPredictor
    _LSTM_AVAILABLE = True
except: _LSTM_AVAILABLE = False
from smc_analyzer import SMCAnalyzer
from oi_analyzer import OIAnalyzer
from rl_logger import RLLogger

_predictor = None

def get_predictor():
    global _predictor
    if _predictor is None:
        _predictor = MLPredictor()
    return _predictor

class MLStrategy:
    def __init__(self):
        self.rsi_period = 14
        self.ema_fast = 20
        self.ema_slow = 50
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        self.predictor = get_predictor()
        self.smc = SMCAnalyzer()
        self.oi = OIAnalyzer()
        self.rl_logger = RLLogger()
        self.in_kill_zone = False
        self.candles = CandlePatterns()
        self.fng = FearGreedIndex()
        self.news = NewsAnalyzer()
        if _LSTM_AVAILABLE:
            try:
                self.lstm = LSTMPredictor()
                print(f"[LSTM] Loaded {len(self.lstm.models)} LSTM models")
            except: self.lstm = None
        else:
            self.lstm = None

    def calc_rsi(self, closes, period=14):
        if len(closes) < period + 1:
            return 50
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calc_ema(self, closes, period):
        if len(closes) < period:
            return closes[-1]
        return pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1]

    def check_funding_rate(self, symbol, api):
        """Анализируем funding rate — перекупленность/перепроданность"""
        try:
            rate = api.get_funding_rate(symbol)
            rate_pct = rate * 100
            if rate_pct > 0.1:
                print(f"[FUND] {symbol}: высокий funding {rate_pct:.3f}% — рынок перекуплен, осторожно с LONG")
                return "BEARISH", rate_pct
            elif rate_pct < -0.1:
                print(f"[FUND] {symbol}: отрицательный funding {rate_pct:.3f}% — рынок перепродан, осторожно с SHORT")
                return "BULLISH", rate_pct
            elif rate_pct > 0.05:
                return "SLIGHT_BEARISH", rate_pct
            elif rate_pct < -0.05:
                return "SLIGHT_BULLISH", rate_pct
            return "NEUTRAL", rate_pct
        except:
            return "NEUTRAL", 0.0

    def calc_atr(self, klines, period=14):
        """Average True Range — измеряем волатильность"""
        try:
            highs = [float(k["high"]) for k in klines]
            lows = [float(k["low"]) for k in klines]
            closes = [float(k["close"]) for k in klines]
            trs = []
            for i in range(1, len(klines)):
                tr = max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]),
                    abs(lows[i] - closes[i-1])
                )
                trs.append(tr)
            if len(trs) < period:
                return 0, 0
            atr = np.mean(trs[-period:])
            atr_pct = (atr / closes[-1]) * 100
            avg_atr_pct = np.mean([(t / closes[i+1]) * 100 for i, t in enumerate(trs[-50:])])
            return atr_pct, avg_atr_pct
        except:
            return 0, 0

    def calc_macd(self, closes):
        ema12 = pd.Series(closes).ewm(span=12, adjust=False).mean()
        ema26 = pd.Series(closes).ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = macd - signal
        return float(hist.iloc[-1]), float(hist.iloc[-2])

    def get_signal_15m(self, symbol, api):
        """Подтверждение входа на 15m таймфрейме"""
        try:
            result = api.get_klines(symbol, interval="15m", limit=50)
            if not result or result.get("code") != 0:
                return "NEUTRAL"
            klines = result["data"]
            if not klines or len(klines) < 20:
                return "NEUTRAL"
            closes = [float(k["close"]) for k in klines]
            ema_fast = self.calc_ema(closes, 9)
            ema_slow = self.calc_ema(closes, 21)
            rsi = self.calc_rsi(closes, 14)
            current_price = closes[-1]
            macd_hist, macd_prev = self.calc_macd(closes)

            bull = 0
            bear = 0

            if current_price > ema_fast > ema_slow: bull += 1
            elif current_price < ema_fast < ema_slow: bear += 1

            if rsi < 40: bull += 1
            elif rsi > 60: bear += 1

            if macd_hist > 0 and macd_prev <= 0: bull += 1
            elif macd_hist < 0 and macd_prev >= 0: bear += 1

            if bull >= 2 and bull > bear:
                print(f"[15M] {symbol}: бычье подтверждение (RSI={rsi:.1f})")
                return "BULLISH"
            elif bear >= 2 and bear > bull:
                print(f"[15M] {symbol}: медвежье подтверждение (RSI={rsi:.1f})")
                return "BEARISH"
            return "NEUTRAL"
        except:
            return "NEUTRAL"

    def get_trend_4h(self, symbol, api):
        """Определяем тренд на 4h таймфрейме"""
        try:
            result = api.get_klines(symbol, interval="4h", limit=50)
            if not result or result.get("code") != 0:
                return "NEUTRAL"
            klines = result["data"]
            if not klines or len(klines) < 20:
                return "NEUTRAL"
            closes = [float(k["close"]) for k in klines]
            ema_fast = self.calc_ema(closes, 10)
            ema_slow = self.calc_ema(closes, 20)
            rsi = self.calc_rsi(closes, 14)
            current_price = closes[-1]
            if current_price > ema_fast > ema_slow and rsi < 70:
                return "BULLISH"
            elif current_price < ema_fast < ema_slow and rsi > 30:
                return "BEARISH"
            else:
                return "NEUTRAL"
        except:
            return "NEUTRAL"

    def detect_rsi_divergence(self, closes, rsi_period=14, lookback=20):
        """Определяем бычью и медвежью дивергенцию RSI"""
        try:
            if len(closes) < lookback + rsi_period:
                return "NEUTRAL"

            # Считаем RSI для последних N свечей
            rsi_values = []
            for i in range(lookback):
                idx = len(closes) - lookback + i
                window = closes[max(0, idx-rsi_period-1):idx+1]
                if len(window) < rsi_period + 1:
                    rsi_values.append(50)
                    continue
                deltas = np.diff(window)
                gains = np.where(deltas > 0, deltas, 0)
                losses = np.where(deltas < 0, -deltas, 0)
                avg_gain = np.mean(gains[-rsi_period:])
                avg_loss = np.mean(losses[-rsi_period:])
                if avg_loss == 0:
                    rsi_values.append(100)
                else:
                    rsi_values.append(100 - (100 / (1 + avg_gain / avg_loss)))

            prices = closes[-lookback:]

            # Находим локальные минимумы и максимумы
            price_lows = []
            price_highs = []
            rsi_lows = []
            rsi_highs = []

            for i in range(2, len(prices) - 2):
                if prices[i] < prices[i-1] and prices[i] < prices[i+1]:
                    price_lows.append((i, prices[i]))
                    rsi_lows.append((i, rsi_values[i]))
                if prices[i] > prices[i-1] and prices[i] > prices[i+1]:
                    price_highs.append((i, prices[i]))
                    rsi_highs.append((i, rsi_values[i]))

            # Бычья дивергенция: цена делает новый минимум, RSI нет
            if len(price_lows) >= 2 and len(rsi_lows) >= 2:
                p1, p2 = price_lows[-2][1], price_lows[-1][1]
                r1, r2 = rsi_lows[-2][1], rsi_lows[-1][1]
                if p2 < p1 and r2 > r1 and r2 < 45:
                    print(f"[DIV] Бычья дивергенция: цена {p1:.2f}->{p2:.2f}, RSI {r1:.1f}->{r2:.1f}")
                    return "BULLISH"

            # Медвежья дивергенция: цена делает новый максимум, RSI нет
            if len(price_highs) >= 2 and len(rsi_highs) >= 2:
                p1, p2 = price_highs[-2][1], price_highs[-1][1]
                r1, r2 = rsi_highs[-2][1], rsi_highs[-1][1]
                if p2 > p1 and r2 < r1 and r2 > 55:
                    print(f"[DIV] Медвежья дивергенция: цена {p1:.2f}->{p2:.2f}, RSI {r1:.1f}->{r2:.1f}")
                    return "BEARISH"

            return "NEUTRAL"
        except:
            return "NEUTRAL"

    def is_active_session(self):
        # ПАТЧ 16: Killzone фильтр — вход только в Лондон и NY сессии
        # Лондон: 08:00-11:00 UTC, NY: 13:00-16:00 UTC
        # Остальное время — только управление позициями (trailing, SL, TP)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Лондон killzone: 08:00-11:00 UTC
        if 8 <= hour <= 10:
            if not getattr(self, '_kz_logged', False):
                print(f"[KILLZONE] Лондон сессия (UTC {hour}:00) — торговля разрешена")
                self._kz_logged = True
            self.in_kill_zone = True
            return True

        # NY killzone: 13:00-16:00 UTC
        if 13 <= hour <= 17:
            if not getattr(self, '_kz_logged', False):
                print(f"[KILLZONE] NY сессия (UTC {hour}:00) — торговля разрешена")
                self._kz_logged = True
            self.in_kill_zone = True
            return True

        # Азия/переходная сессия: разрешаем торговлю но без killzone бонуса
        # Это позволяет не пропускать сильные тренды вне основных сессий
        self.in_kill_zone = False
        self._kz_logged = False
        return True  # Разрешаем торговлю всегда, killzone даёт только бонус

    def get_trend_daily(self, symbol, api):
        """Определяем тренд на дневном таймфрейме (HTF Bias)"""
        try:
            result = api.get_klines(symbol, interval="1d", limit=30)
            if not result or result.get("code") != 0:
                return "NEUTRAL"
            klines = result["data"]
            if not klines or len(klines) < 10:
                return "NEUTRAL"
            closes = [float(k["close"]) for k in klines]
            highs = [float(k["high"]) for k in klines]
            lows = [float(k["low"]) for k in klines]
            current_price = closes[-1]

            ema10 = self.calc_ema(closes, 10)
            ema20 = self.calc_ema(closes, 20)
            rsi = self.calc_rsi(closes, 14)

            # Структура рынка — Higher Highs/Higher Lows
            recent_highs = highs[-10:]
            recent_lows = lows[-10:]
            hh = recent_highs[-1] > max(recent_highs[:-1])
            hl = recent_lows[-1] > min(recent_lows[:-1])
            lh = recent_highs[-1] < max(recent_highs[:-1])
            ll = recent_lows[-1] < min(recent_lows[:-1])

            bullish = (ema10 > ema20) and (rsi > 45) and (hh or hl)
            bearish = (ema10 < ema20) and (rsi < 55) and (lh or ll)

            if bullish:
                print(f"[1D] {symbol}: BULLISH тренд (RSI={rsi:.1f})")
                return "BULLISH"
            elif bearish:
                print(f"[1D] {symbol}: BEARISH тренд (RSI={rsi:.1f})")
                return "BEARISH"
            return "NEUTRAL"
        except:
            return "NEUTRAL"

    def get_btc_trend(self, api):
        """Проверяем направление BTC за последний час"""
        try:
            result = api.get_klines("BTC-USDT", interval="1h", limit=3)
            if not result or result.get("code") != 0:
                return "NEUTRAL"
            klines = result["data"]
            if len(klines) < 2:
                return "NEUTRAL"
            prev_close = float(klines[-2]["close"])
            curr_close = float(klines[-1]["close"])
            change_pct = ((curr_close - prev_close) / prev_close) * 100
            if change_pct <= -2.0:
                print(f"[BTC] Резкое падение: {change_pct:.2f}% — блокируем лонги")
                return "DUMP"
            elif change_pct >= 2.0:
                print(f"[BTC] Резкий рост: {change_pct:.2f}% — блокируем шорты")
                return "PUMP"
            return "NEUTRAL"
        except:
            return "NEUTRAL"


    def get_htf_confluence(self, symbol, api):
        """Higher Timeframe Confluence — согласование 1D + 4H + 1H"""
        try:
            # 1D тренд
            trend_1d = self.get_trend_daily(symbol, api)
            # 4H тренд
            trend_4h = self.get_trend_4h(symbol, api)
            # 1H тренд (текущий)
            r = api.get_klines(symbol, interval="1h", limit=50)
            trend_1h = "NEUTRAL"
            if r and r.get("code") == 0:
                closes = [float(k["close"]) for k in r["data"]]
                ema20 = sum(closes[-20:]) / 20
                ema50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else ema20
                if closes[-1] > ema20 > ema50:
                    trend_1h = "BULLISH"
                elif closes[-1] < ema20 < ema50:
                    trend_1h = "BEARISH"

            # Считаем confluence
            bull_count = sum(1 for t in [trend_1d, trend_4h, trend_1h] if t == "BULLISH")
            bear_count = sum(1 for t in [trend_1d, trend_4h, trend_1h] if t == "BEARISH")

            if bull_count == 3:
                print(f"[HTF] {symbol}: FULL BULLISH confluence (1D+4H+1H)")
                return "STRONG_BULL", bull_count
            elif bear_count == 3:
                print(f"[HTF] {symbol}: FULL BEARISH confluence (1D+4H+1H)")
                return "STRONG_BEAR", bear_count
            elif bull_count == 2:
                print(f"[HTF] {symbol}: Partial BULLISH ({trend_1d}/{trend_4h}/{trend_1h})")
                return "BULL", bull_count
            elif bear_count == 2:
                print(f"[HTF] {symbol}: Partial BEARISH ({trend_1d}/{trend_4h}/{trend_1h})")
                return "BEAR", bear_count
            else:
                return "NEUTRAL", 0
        except Exception as e:
            print(f"[HTF] Error: {e}")
            return "NEUTRAL", 0

    def get_mtf_smc(self, symbol, api):
        """
        Multi-timeframe SMC конфлюэнция.
        Проверяет SMC сигнал на 15m, 1h, 4h.
        Возвращает: (signal, score, details)
          signal: "BULLISH" / "BEARISH" / "NEUTRAL"
          score: 0-6 (по 2 за каждый таймфрейм)
          details: dict с результатами по каждому TF
        """
        try:
            results = {}
            bull_score = 0
            bear_score = 0

            for tf, limit in [("15m", 60), ("1h", 60), ("4h", 60)]:
                try:
                    raw = api.get_klines(symbol, interval=tf, limit=limit)
                    if not raw or raw.get("code") != 0:
                        results[tf] = "ERROR"
                        continue
                    klines_tf = raw["data"]
                    if len(klines_tf) < 30:
                        results[tf] = "NO_DATA"
                        continue

                    smc_res = self.smc.analyze(klines_tf[-50:])
                    sig = smc_res.get("signal", "NEUTRAL")
                    sc = smc_res.get("score", 0)
                    details_tf = smc_res.get("details", {})

                    results[tf] = {
                        "signal": sig,
                        "score": sc,
                        "has_ob": bool(details_tf.get("bullish_ob") or details_tf.get("bearish_ob")),
                        "has_fvg": bool(details_tf.get("bullish_fvg") or details_tf.get("bearish_fvg")),
                        "bos": details_tf.get("bos", ""),
                    }

                    if sig == "BULLISH":
                        bull_score += min(sc, 2)
                    elif sig == "BEARISH":
                        bear_score += min(sc, 2)

                except Exception as e:
                    results[tf] = f"ERROR: {e}"

            # Конфлюэнция: нужно 2+ TF в одном направлении
            bull_tfs = sum(1 for tf in results.values() if isinstance(tf, dict) and tf.get("signal") == "BULLISH")
            bear_tfs = sum(1 for tf in results.values() if isinstance(tf, dict) and tf.get("signal") == "BEARISH")

            if bull_tfs >= 2 and bull_score > bear_score:
                final = "BULLISH"
                final_score = bull_score
            elif bear_tfs >= 2 and bear_score > bull_score:
                final = "BEARISH"
                final_score = bear_score
            else:
                final = "NEUTRAL"
                final_score = 0

            # Логируем
            tf_summary = []
            for tf in ["15m", "1h", "4h"]:
                r = results.get(tf, "N/A")
                if isinstance(r, dict):
                    tf_summary.append(f"{tf}={r['signal']}({r['score']})")
                else:
                    tf_summary.append(f"{tf}={r}")

            print(f"[MTF-SMC] {symbol}: {final} (score={final_score}) | {', '.join(tf_summary)}")
            return final, final_score, results

        except Exception as e:
            print(f"[MTF-SMC] {symbol} error: {e}")
            return "NEUTRAL", 0, {}


    def get_signal(self, symbol, api):
        try:
            # Фильтр торговых сессий
            if not self.is_active_session():
                return "HOLD", 0.0

            # Higher Timeframe Confluence
            htf_signal, htf_count = self.get_htf_confluence(symbol, api)

            # Daily HTF Bias — главный фильтр направления
            trend_1d = self.get_trend_daily(symbol, api)

            # 4h тренд (фильтр направления)
            trend_4h = self.get_trend_4h(symbol, api)

            # 15m подтверждение входа
            signal_15m = self.get_signal_15m(symbol, api)

            # Корреляция с BTC (только для не-BTC пар)
            btc_trend = "NEUTRAL"
            if symbol != "BTC-USDT":
                btc_trend = self.get_btc_trend(api)

            # 1h данные для входа
            result = api.get_klines(symbol, interval="1h", limit=200)
            if not result or result.get("code") != 0:
                return "HOLD"
            klines = result["data"]
            if not klines or len(klines) < 60:
                return "HOLD"

            closes = [float(k["close"]) for k in klines]
            current_price = closes[-1]

            # ATR Volatility Filter
            atr_pct, avg_atr_pct = self.calc_atr(klines)
            if avg_atr_pct > 0:
                if atr_pct < avg_atr_pct * 0.5:
                    print(f"[ATR] {symbol}: низкая волатильность {atr_pct:.2f}% < {avg_atr_pct*0.5:.2f}% — пропускаем")
                    return "HOLD", 0.0
                if atr_pct > avg_atr_pct * 3.0:
                    print(f"[ATR] {symbol}: аномальная волатильность {atr_pct:.2f}% > {avg_atr_pct*3.0:.2f}% — пропускаем")
                    return "HOLD", 0.0

            #            # Фильтр по объёму
            #            volumes = [float(k["volume"]) for k in klines]
            #            avg_volume = sum(volumes[-21:-1]) / 20
            #            current_volume = volumes[-1]
            #            if current_volume < avg_volume * 0.5:
            #                print(f"[VOL] {symbol}: низкий объём {current_volume:.2f} < avg {avg_volume:.2f} — пропускаем")
            #                return "HOLD", 0.0

            rsi = self.calc_rsi(closes, self.rsi_period)
            ema_fast = self.calc_ema(closes, self.ema_fast)
            ema_slow = self.calc_ema(closes, self.ema_slow)
            macd_hist, macd_prev = self.calc_macd(closes)

            buy_signals = 0
            sell_signals = 0

            # Kill Zone бонус — в приоритетное время добавляем очки
            if getattr(self, 'in_kill_zone', False):
                print(f"[KILLZONE] {symbol}: бонус +1 к сигналу")
                # Бонус применяется позже к победившей стороне

            # HTF Confluence бонус
            if htf_signal == "STRONG_BULL":
                buy_signals += 2
            elif htf_signal == "BULL":
                buy_signals += 1
            elif htf_signal == "STRONG_BEAR":
                sell_signals += 2
            elif htf_signal == "BEAR":
                sell_signals += 1

            # Funding Rate
            fund_signal, fund_rate = self.check_funding_rate(symbol, api)
            if fund_signal == "BEARISH":
                sell_signals += 1
                if abs(fund_rate) > 0.05:
                    sell_signals += 1
                    print(f"[FUND] {symbol}: extreme funding {fund_rate:.4f}% -> +2 SELL")
            elif fund_signal == "BULLISH":
                buy_signals += 1
                if abs(fund_rate) > 0.05:
                    buy_signals += 1
                    print(f"[FUND] {symbol}: extreme funding {fund_rate:.4f}% -> +2 BUY")





            # Fear & Greed Index
            fng_signal, fng_value = self.fng.get_signal()
            if fng_signal == "BULLISH":
                buy_signals += 1
            elif fng_signal == "SLIGHT_BEARISH":
                sell_signals += 1

            # Candlestick паттерны
            candle_signal = self.candles.detect(klines[-5:])
            if candle_signal == "BULLISH":
                buy_signals += 1
            elif candle_signal == "BEARISH":
                sell_signals += 1

            # SMC анализ
            smc = self.smc.analyze(klines[-50:])
            if smc["signal"] == "BULLISH":
                buy_signals += min(smc["score"], 3)
                print(f"[SMC] {symbol}: BULLISH (score={smc['score']}, bos={smc['details'].get('bos','N')})")
            elif smc["signal"] == "BEARISH":
                sell_signals += min(smc["score"], 3)
                print(f"[SMC] {symbol}: BEARISH (score={smc['score']}, bos={smc['details'].get('bos','N')})")

            # Premium/Discount зона — усиление SHORT/LONG
            try:
                pd_details = smc.get("details", {}).get("premium_discount", {})
                pd_zone = pd_details.get("zone", "EQUILIBRIUM")
                pd_pct = pd_details.get("pct", 50)
                if pd_zone == "PREMIUM":
                    sell_signals += 1
                    print(f"[PD] {symbol}: Premium ({pd_pct:.0f}%) -> +1 SELL")
                elif pd_zone == "DISCOUNT":
                    buy_signals += 1
                    print(f"[PD] {symbol}: Discount ({pd_pct:.0f}%) -> +1 BUY")
            except: pass

            # MTF SMC конфлюэнция (Патч 7)
            try:
                mtf_signal, mtf_score, mtf_details = self.get_mtf_smc(symbol, api)
                if mtf_signal == "BULLISH" and mtf_score >= 3:
                    buy_signals += 2
                    print(f"[MTF-SMC] {symbol}: +2 к BUY (конфлюэнция {mtf_score})")
                elif mtf_signal == "BEARISH" and mtf_score >= 3:
                    sell_signals += 2
                    print(f"[MTF-SMC] {symbol}: +2 к SELL (конфлюэнция {mtf_score})")
            except Exception as e:
                print(f"[MTF-SMC] {symbol} call error: {e}")

            # 4h тренд фильтр
            if trend_4h == "BULLISH":
                buy_signals += 1
                print(f"[4H] {symbol}: бычий тренд")
            elif trend_4h == "BEARISH":
                sell_signals += 1
                print(f"[4H] {symbol}: медвежий тренд")

            # 15m подтверждение
            if signal_15m == "BULLISH":
                buy_signals += 1
            elif signal_15m == "BEARISH":
                sell_signals += 1

            # RSI 1h
            if rsi < self.rsi_oversold:
                buy_signals += 1
            elif rsi > self.rsi_overbought:
                sell_signals += 1

            # RSI Divergence — сильный сигнал разворота
            divergence = self.detect_rsi_divergence(closes)
            if divergence == "BULLISH":
                print(f"[DIV] {symbol}: бычья дивергенция (info only)")
            elif divergence == "BEARISH":
                print(f"[DIV] {symbol}: медвежья дивергенция (info only)")

            # EMA тренд 1h
            if current_price > ema_fast > ema_slow:
                buy_signals += 1
            elif current_price < ema_fast < ema_slow:
                sell_signals += 1

            # MACD пересечение 1h
            # MACD: только лог, не добавляем баллы (шум)
            if macd_hist > 0 and macd_prev <= 0:
                print(f"[MACD] {symbol}: бычье пересечение (info only)")
            elif macd_hist < 0 and macd_prev >= 0:
                print(f"[MACD] {symbol}: медвежье пересечение (info only)")

            # ML сигнал
            ml_signal, ml_confidence = self.predictor.predict_with_confidence(symbol, klines)
            # ПАТЧ 22: Адаптивный порог уверенности ML по паре
            conf_thresh = 0.58  # default
            try:
                # Пробуем получить порог из бота (если вызывается из TradingBot)
                conf_map = {"ETH-USDT":0.58,"SUI-USDT":0.60,"DOGE-USDT":0.60,"ADA-USDT":0.62,"XRP-USDT":0.60,"OP-USDT":0.55,"LINK-USDT":0.68,"FET-USDT":0.60,"TAO-USDT":0.60,"DOT-USDT":0.58,"ARB-USDT":0.60,"PENDLE-USDT":0.60,"FIL-USDT":0.58,"NEAR-USDT":0.58}
                conf_thresh = conf_map.get(symbol, 0.58)
            except: pass
            short_thresh = conf_thresh + 0.04  # шорты строже на 4%

            if ml_signal == 1 and ml_confidence >= conf_thresh:
                buy_signals += 2
                print(f"[ML] {symbol}: UP сигнал (уверенность {ml_confidence*100:.1f}%)")
            elif ml_signal == -1 and ml_confidence >= short_thresh:
                sell_signals += 2
                print(f"[ML] {symbol}: DOWN сигнал (уверенность {ml_confidence*100:.1f}%, порог {short_thresh*100:.0f}%)")
            elif ml_signal in (1, -1) and ml_confidence < conf_thresh:
                print(f"[ML] {symbol}: слабый сигнал (conf={ml_confidence*100:.1f}% < {conf_thresh*100:.0f}%) — игнорируем")
            else:
                print(f"[ML] {symbol}: HOLD (RSI={rsi:.1f}, MACD={macd_hist:.4f}, 4H={trend_4h}, conf={ml_confidence*100:.1f}%)")

            # LSTM дополнительный голос
            if self.lstm and symbol in self.lstm.models:
                try:
                    lstm_signal, lstm_conf = self.lstm.predict(symbol, klines)
                    if lstm_conf >= 0.55:
                        if lstm_signal == 1:
                            buy_signals += 1
                            print(f"[LSTM] {symbol}: UP conf={lstm_conf*100:.1f}% +1 BUY")
                        elif lstm_signal == -1:
                            sell_signals += 1
                            print(f"[LSTM] {symbol}: DOWN conf={lstm_conf*100:.1f}% +1 SELL")
                except Exception as e:
                    pass

            # Новостной фильтр
            try:
                news_sentiment = self.news.get_market_sentiment(symbol)
                ns = news_sentiment['signal']
                if news_sentiment['high_impact']:
                    if ns == 'STRONG_BEARISH' and buy_signals > sell_signals:
                        buy_signals -= 2
                        print(f"[NEWS] {symbol}: -2 к BUY — STRONG_BEARISH новости")
                    elif ns == 'STRONG_BULLISH' and sell_signals > buy_signals:
                        sell_signals -= 2
                        print(f"[NEWS] {symbol}: -2 к SELL — STRONG_BULLISH новости")
                    elif ns == 'BEARISH':
                        buy_signals -= 1
                    elif ns == 'BULLISH':
                        sell_signals -= 1
                elif ns == 'BULLISH':
                    buy_signals += 1
                elif ns == 'BEARISH':
                    sell_signals += 1
            except Exception as e:
                print(f"[NEWS] Error: {e}")

            # Kill Zone бонус к победившей стороне
            if getattr(self, 'in_kill_zone', False):
                if buy_signals > sell_signals:
                    buy_signals += 1
                elif sell_signals > buy_signals:
                    sell_signals += 1

            # === Сессионный фильтр — блокируем слабые часы ===
            try:
                from datetime import datetime, timezone
                current_hour_utc = datetime.now(timezone.utc).hour
                weak_hours = [12]  # UTC часы с WR < 50% (8 убран — London killzone)
                if current_hour_utc in weak_hours:
                    print(f"[SESSION] {symbol}: блокируем вход — слабый час {current_hour_utc:02d} UTC (WR<50%)")
                    return "HOLD", ml_confidence
            except Exception as e:
                print(f"[SESSION] error: {e}")

            # === Open Interest анализ ===
            try:
                oi_result = self.oi.analyze(symbol, current_price)
                oi_score = oi_result.get("score", 0)
                oi_signal = oi_result.get("signal", "NEUTRAL")
                if oi_score > 0:
                    buy_signals += oi_score
                    print(f"[OI] {symbol}: +{oi_score} к BUY ({oi_signal})")
                elif oi_score < 0:
                    sell_signals += abs(oi_score)
                    print(f"[OI] {symbol}: +{abs(oi_score)} к SELL ({oi_signal})")
                # Squeeze = ненадёжное движение, предупреждаем
                if oi_signal == "SHORT_SQUEEZE" and buy_signals > sell_signals:
                    print(f"[OI] {symbol}: SHORT_SQUEEZE — рост ненадёжный, осторожно с LONG")
                elif oi_signal == "LONG_SQUEEZE" and sell_signals > buy_signals:
                    print(f"[OI] {symbol}: LONG_SQUEEZE — падение ненадёжное, осторожно с SHORT")
            except Exception as e:
                print(f"[OI] {symbol} error: {e}")

            # Funding дубль убран (один вызов выше)
                # smc_score_val — нужен для фильтров ниже
            smc_score_val = smc.get("score", 0) if isinstance(smc, dict) else 0

            # === ФИЛЬТР: Не торговать против 4H тренда (кроме сильного SMC) ===
            smc_details = smc.get("details", {}) if isinstance(smc, dict) else {}
            smc_bull_conf = smc_details.get("bull_confluence", 0)
            smc_bear_conf = smc_details.get("bear_confluence", 0)

            # Добавляем MTF confluence к SMC conf
            try:
                if mtf_signal == "BULLISH" and mtf_score >= 3:
                    smc_bull_conf += 1
                    print(f"[SMC-CONF] {symbol}: +1 bull conf от MTF (итого {smc_bull_conf})")
                elif mtf_signal == "BEARISH" and mtf_score >= 3:
                    smc_bear_conf += 1
                    print(f"[SMC-CONF] {symbol}: +1 bear conf от MTF (итого {smc_bear_conf})")
            except NameError:
                pass

            if trend_4h == "BULLISH" and sell_signals > buy_signals and sell_signals >= 3:
                if smc_score_val < 5:
                    print(f"[4H-FILTER] {symbol}: блокируем SELL — 4H бычий, SMC score={smc_score_val} < 5")
                    return "HOLD", ml_confidence
                else:
                    print(f"[4H-FILTER] {symbol}: SELL против 4H разрешён — SMC score={smc_score_val} >= 5")
            elif trend_4h == "BEARISH" and buy_signals > sell_signals and buy_signals >= 3:
                if smc_score_val < 5:
                    print(f"[4H-FILTER] {symbol}: блокируем BUY — 4H медвежий, SMC score={smc_score_val} < 5")
                    return "HOLD", ml_confidence
                else:
                    print(f"[4H-FILTER] {symbol}: BUY против 4H разрешён — SMC score={smc_score_val} >= 5")

            # === ФИЛЬТР: Минимум 2 SMC подтверждения (confluence) ===
            if buy_signals >= 3 and buy_signals > sell_signals:
                if smc_bull_conf < 2:
                    print(f"[SMC-CONF] {symbol}: блокируем BUY — только {smc_bull_conf} SMC подтверждений (нужно >=2)")
                    return "HOLD", ml_confidence
            elif sell_signals >= 3 and sell_signals > buy_signals:
                if smc_bear_conf < 2:
                    print(f"[SMC-CONF] {symbol}: блокируем SELL — только {smc_bear_conf} SMC подтверждений (нужно >=2)")
                    return "HOLD", ml_confidence

            # Финальное решение с учётом Daily Bias и BTC корреляции

                # === ОБЯЗАТЕЛЬНЫЙ Liquidity Sweep фильтр ===
            # Без sweep вход блокируется (кроме очень сильных сигналов score >= 6)
            smc_sweep = smc.get("details", {}).get("sweep", "NEUTRAL") if isinstance(smc, dict) else "NEUTRAL"
            if buy_signals >= 3 and buy_signals > sell_signals:
                if smc_sweep not in ("BULLISH_SWEEP",) and buy_signals < 6:
                    print(f"[SWEEP] {symbol}: блокируем BUY — нет liquidity sweep (sweep={smc_sweep})")
                    return "HOLD", ml_confidence
            if sell_signals >= 4 and sell_signals > buy_signals:
                if smc_sweep not in ("BEARISH_SWEEP",) and sell_signals < 6:
                    print(f"[SWEEP] {symbol}: блокируем SELL — нет liquidity sweep (sweep={smc_sweep})")
                    return "HOLD", ml_confidence

        # 15M обязательное подтверждение входа
            if buy_signals >= 3 and buy_signals > sell_signals and signal_15m == "BEARISH":
                print(f"[15M-CONF] {symbol}: блокируем BUY — 15M медвежий")
                return "HOLD", ml_confidence
            if sell_signals >= 4 and sell_signals > buy_signals and signal_15m == "BULLISH":
                print(f"[15M-CONF] {symbol}: блокируем SELL — 15M бычий")
                return "HOLD", ml_confidence

            if buy_signals >= 3 and buy_signals > sell_signals:
                if trend_1d == "BEARISH":
                    if smc_score_val >= 4:
                        print(f"[1D-BIAS] {symbol}: BUY против 1D тренда — SMC score={smc_score_val} достаточен")
                    else:
                        buy_signals -= 1
                        print(f"[1D-BIAS] {symbol}: -1 к BUY — 1D медвежий, SMC score={smc_score_val} < 4 (buy={buy_signals})")
                        if buy_signals < 3:
                            return "HOLD", ml_confidence
                if btc_trend == "DUMP":
                    print(f"[BTC-CORR] {symbol}: блокируем BUY — BTC падает")
                    return "HOLD", ml_confidence
                print(f"[SIGNAL] {symbol}: BUY (buy={buy_signals}, sell={sell_signals}, 1D={trend_1d}, 4H={trend_4h})")
                try:
                    self.rl_logger.log_state(symbol, {
                        "buy_signals": buy_signals, "sell_signals": sell_signals,
                        "smc_score": smc_score_val, "ml_conf": round(ml_confidence, 3),
                        "rsi": round(rsi, 1), "macd": round(macd_hist, 6),
                        "trend_4h": trend_4h, "trend_1d": trend_1d,
                        "bull_conf": smc_details.get("bull_confluence", 0),
                        "bear_conf": smc_details.get("bear_confluence", 0),
                    }, "BUY")
                except: pass
                return "BUY", ml_confidence
            elif sell_signals >= 4 and sell_signals > buy_signals:
                if trend_1d == "BULLISH":
                    if smc_score_val >= 4:
                        print(f"[1D-BIAS] {symbol}: SELL против 1D тренда — SMC score={smc_score_val} достаточен")
                    else:
                        sell_signals -= 1
                        print(f"[1D-BIAS] {symbol}: -1 к SELL — 1D бычий, SMC score={smc_score_val} < 4 (sell={sell_signals})")
                        if sell_signals < 3:
                            return "HOLD", ml_confidence
                if btc_trend == "PUMP":
                    print(f"[BTC-CORR] {symbol}: блокируем SELL — BTC растёт")
                    return "HOLD", ml_confidence
                print(f"[SIGNAL] {symbol}: SELL (buy={buy_signals}, sell={sell_signals}, 1D={trend_1d}, 4H={trend_4h})")
                try:
                    self.rl_logger.log_state(symbol, {
                        "buy_signals": buy_signals, "sell_signals": sell_signals,
                        "smc_score": smc_score_val, "ml_conf": round(ml_confidence, 3),
                        "rsi": round(rsi, 1), "macd": round(macd_hist, 6),
                        "trend_4h": trend_4h, "trend_1d": trend_1d,
                        "bull_conf": smc_details.get("bull_confluence", 0),
                        "bear_conf": smc_details.get("bear_confluence", 0),
                    }, "SELL")
                except: pass
                return "SELL", ml_confidence
            else:
                return "HOLD", ml_confidence

        except Exception as e:
            import traceback
            print(f"[STRATEGY] Error: {e}")
            print(traceback.format_exc())
            return "HOLD"
