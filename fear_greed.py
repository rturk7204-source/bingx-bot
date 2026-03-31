import requests

class FearGreedIndex:
    """Fear & Greed Index от Alternative.me"""

    def __init__(self):
        self.url = "https://api.alternative.me/fng/?limit=1"
        self.cached_value = None
        self.cached_time = 0

    def get(self):
        """Получаем текущий индекс страха и жадности"""
        try:
            import time
            # Кешируем на 1 час
            if self.cached_value and (time.time() - self.cached_time) < 3600:
                return self.cached_value

            r = requests.get(self.url, timeout=10)
            data = r.json()
            value = int(data["data"][0]["value"])
            classification = data["data"][0]["value_classification"]
            self.cached_value = {"value": value, "label": classification}
            self.cached_time = time.time()
            return self.cached_value
        except:
            return {"value": 50, "label": "Neutral"}

    def get_signal(self):
        """Возвращаем торговый сигнал на основе индекса"""
        fng = self.get()
        value = fng["value"]
        label = fng["label"]

        if value <= 20:
            # Экстремальный страх — нейтрально, не мешаем другим сигналам
            print(f"[FNG] Экстремальный страх ({value}) — нейтрально")
            return "NEUTRAL", value
        elif value >= 80:
            # Экстремальная жадность — осторожно с лонгами
            print(f"[FNG] Экстремальная жадность ({value}) — блокируем LONG")
            return "BEARISH", value
        elif value <= 35:
            print(f"[FNG] Страх ({value}) — небольшой бычий уклон")
            return "SLIGHT_BULLISH", value
        elif value >= 65:
            print(f"[FNG] Жадность ({value}) — небольшой медвежий уклон")
            return "SLIGHT_BEARISH", value
        else:
            return "NEUTRAL", value
