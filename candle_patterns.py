import numpy as np

class CandlePatterns:
    """Распознавание свечных паттернов"""

    def detect(self, klines):
        """Анализируем последние свечи и возвращаем сигнал"""
        try:
            if len(klines) < 5:
                return "NEUTRAL"

            opens  = [float(k["open"])  for k in klines]
            closes = [float(k["close"]) for k in klines]
            highs  = [float(k["high"])  for k in klines]
            lows   = [float(k["low"])   for k in klines]

            o1, c1, h1, l1 = opens[-1], closes[-1], highs[-1], lows[-1]
            o2, c2, h2, l2 = opens[-2], closes[-2], highs[-2], lows[-2]
            o3, c3 = opens[-3], closes[-3]

            body1 = abs(c1 - o1)
            body2 = abs(c2 - o2)
            range1 = h1 - l1
            range2 = h2 - l2
            avg_body = np.mean([abs(closes[i] - opens[i]) for i in range(-6, -1)])

            signals = []

            # 1. Доджи — нерешительность рынка
            if range1 > 0 and body1 / range1 < 0.1:
                signals.append(("DOJI", "NEUTRAL"))

            # 2. Молот (Hammer) — бычий разворот
            lower_shadow1 = min(o1, c1) - l1
            upper_shadow1 = h1 - max(o1, c1)
            if (lower_shadow1 > body1 * 2 and
                upper_shadow1 < body1 * 0.5 and
                c2 < o2):  # предыдущая медвежья
                signals.append(("HAMMER", "BULLISH"))
                print(f"[CANDLE] Молот — бычий разворот")

            # 3. Перевёрнутый молот (Shooting Star) — медвежий разворот
            if (upper_shadow1 > body1 * 2 and
                lower_shadow1 < body1 * 0.5 and
                c2 > o2):  # предыдущая бычья
                signals.append(("SHOOTING_STAR", "BEARISH"))
                print(f"[CANDLE] Падающая звезда — медвежий разворот")

            # 4. Бычье поглощение (Bullish Engulfing)
            if (c2 < o2 and  # предыдущая медвежья
                c1 > o1 and  # текущая бычья
                o1 <= c2 and c1 >= o2):  # поглощает предыдущую
                signals.append(("BULLISH_ENGULFING", "BULLISH"))
                print(f"[CANDLE] Бычье поглощение!")

            # 5. Медвежье поглощение (Bearish Engulfing)
            if (c2 > o2 and  # предыдущая бычья
                c1 < o1 and  # текущая медвежья
                o1 >= c2 and c1 <= o2):  # поглощает предыдущую
                signals.append(("BEARISH_ENGULFING", "BEARISH"))
                print(f"[CANDLE] Медвежье поглощение!")

            # 6. Утренняя звезда (Morning Star) — бычий разворот
            if (c3 < o3 and                    # 1я медвежья
                abs(c2 - o2) < avg_body * 0.5 and  # 2я маленькая
                c1 > o1 and                    # 3я бычья
                c1 > (o3 + c3) / 2):           # закрытие выше середины 1й
                signals.append(("MORNING_STAR", "BULLISH"))
                print(f"[CANDLE] Утренняя звезда — сильный бычий разворот!")

            # 7. Вечерняя звезда (Evening Star) — медвежий разворот
            if (c3 > o3 and                    # 1я бычья
                abs(c2 - o2) < avg_body * 0.5 and  # 2я маленькая
                c1 < o1 and                    # 3я медвежья
                c1 < (o3 + c3) / 2):           # закрытие ниже середины 1й
                signals.append(("EVENING_STAR", "BEARISH"))
                print(f"[CANDLE] Вечерняя звезда — сильный медвежий разворот!")

            # 8. Три белых солдата (Three White Soldiers) — сильный бычий
            if (c1 > o1 and c2 > o2 and c3 > o3 and
                c1 > c2 > c3 and
                body1 > avg_body and body2 > avg_body):
                signals.append(("THREE_WHITE_SOLDIERS", "BULLISH"))
                print(f"[CANDLE] Три белых солдата — сильный бычий тренд!")

            # 9. Три чёрных вороны (Three Black Crows) — сильный медвежий
            if (c1 < o1 and c2 < o2 and c3 < o3 and
                c1 < c2 < c3 and
                body1 > avg_body and body2 > avg_body):
                signals.append(("THREE_BLACK_CROWS", "BEARISH"))
                print(f"[CANDLE] Три чёрных вороны — сильный медвежий тренд!")

            # Подсчёт итогового сигнала
            bull = sum(1 for _, s in signals if s == "BULLISH")
            bear = sum(1 for _, s in signals if s == "BEARISH")

            if bull > bear and bull >= 1:
                return "BULLISH"
            elif bear > bull and bear >= 1:
                return "BEARISH"
            return "NEUTRAL"

        except:
            return "NEUTRAL"
