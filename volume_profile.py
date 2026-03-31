import statistics

class VolumeProfile:
    """
    Volume Profile фильтр.
    Проверяет достаточность объёма перед открытием позиции.
    """

    def __init__(self, lookback=20, min_volume_ratio=1.2, strong_volume_ratio=2.0):
        # lookback — сколько свечей анализируем
        self.lookback = lookback
        # min_volume_ratio — минимальный объём относительно среднего (1.2 = +20%)
        self.min_volume_ratio = min_volume_ratio
        # strong_volume_ratio — сильный объём (2.0 = в 2 раза выше среднего)
        self.strong_volume_ratio = strong_volume_ratio

    def analyze(self, klines):
        """
        Анализирует объём по последним klines.
        Возвращает: (confirmed: bool, ratio: float, signal: str)
        """
        try:
            if not klines or len(klines) < self.lookback + 1:
                return True, 1.0, "UNKNOWN"  # если данных нет — не блокируем

            volumes = [float(k["volume"]) for k in klines[-self.lookback-1:-1]]
            current_volume = float(klines[-1]["volume"])

            if not volumes or sum(volumes) == 0:
                return True, 1.0, "UNKNOWN"

            avg_volume = statistics.mean(volumes)
            if avg_volume == 0:
                return True, 1.0, "UNKNOWN"

            ratio = current_volume / avg_volume

            if ratio >= self.strong_volume_ratio:
                return True, ratio, "STRONG"    # сильный объём — хороший вход
            elif ratio >= self.min_volume_ratio:
                return True, ratio, "NORMAL"    # нормальный объём — входим
            else:
                return False, ratio, "WEAK"     # слабый объём — пропускаем

        except Exception as e:
            print(f"[VOL] Error in analyze: {e}")
            return True, 1.0, "UNKNOWN"

    def get_poc(self, klines):
        """
        Point of Control — ценовой уровень с наибольшим объёмом.
        Полезно для определения зон поддержки/сопротивления.
        """
        try:
            if not klines or len(klines) < 5:
                return None

            max_vol = 0
            poc_price = None
            for k in klines[-self.lookback:]:
                vol = float(k["volume"])
                if vol > max_vol:
                    max_vol = vol
                    poc_price = (float(k["high"]) + float(k["low"])) / 2

            return poc_price
        except:
            return None

    def is_near_poc(self, klines, current_price, threshold_pct=0.5):
        """
        Проверяет находится ли цена вблизи POC (±0.5%).
        Торговля у POC — высокая вероятность реакции.
        """
        poc = self.get_poc(klines)
        if poc is None:
            return False
        distance_pct = abs(current_price - poc) / poc * 100
        return distance_pct <= threshold_pct
