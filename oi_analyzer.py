import requests
import time
import json
import os

class OIAnalyzer:
    """
    Анализ Open Interest — изменение OI + цена = сигнал.
    
    OI растёт + цена растёт = сильный LONG тренд (подтверждение)
    OI растёт + цена падает = сильный SHORT тренд (подтверждение)
    OI падает + цена растёт = шорт-сквиз (слабый рост, скоро разворот)
    OI падает + цена падает = лонг-сквиз (слабое падение, скоро разворот)
    """
    
    def __init__(self):
        self.bingx_api = "https://open-api.bingx.com"
        self.history_file = "/root/bingx-bot/oi_history.json"
        self.history = self._load_history()
    
    def _load_history(self):
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, "r") as f:
                    return json.load(f)
        except:
            pass
        return {}
    
    def _save_history(self):
        try:
            with open(self.history_file, "w") as f:
                json.dump(self.history, f)
        except:
            pass
    
    def get_open_interest(self, symbol):
        try:
            r = requests.get(f"{self.bingx_api}/openApi/swap/v2/quote/openInterest",
                params={"symbol": symbol}, timeout=10)
            d = r.json()
            if d.get("code") == 0:
                return float(d["data"]["openInterest"])
        except:
            pass
        return 0
    
    def analyze(self, symbol, current_price):
        """
        Возвращает:
        {
            "signal": "BULLISH_CONFIRM" / "BEARISH_CONFIRM" / "LONG_SQUEEZE" / "SHORT_SQUEEZE" / "NEUTRAL",
            "oi_change_pct": float,
            "price_change_pct": float,
            "score": int  # -2 до +2
        }
        """
        try:
            oi_now = self.get_open_interest(symbol)
            if oi_now <= 0:
                return {"signal": "NEUTRAL", "oi_change_pct": 0, "price_change_pct": 0, "score": 0}
            
            now = time.time()
            
            # Получаем предыдущие данные
            prev = self.history.get(symbol, {})
            prev_oi = prev.get("oi", 0)
            prev_price = prev.get("price", 0)
            prev_time = prev.get("time", 0)
            
            # Сохраняем текущие
            self.history[symbol] = {"oi": oi_now, "price": current_price, "time": now}
            self._save_history()
            
            # Первый вызов — нет истории
            if prev_oi <= 0 or prev_price <= 0 or (now - prev_time) > 7200:
                return {"signal": "NEUTRAL", "oi_change_pct": 0, "price_change_pct": 0, "score": 0}
            
            oi_change = (oi_now - prev_oi) / prev_oi * 100
            price_change = (current_price - prev_price) / prev_price * 100
            
            signal = "NEUTRAL"
            score = 0
            
            # Значимое изменение OI > 1%
            if abs(oi_change) > 1.0:
                if oi_change > 0 and price_change > 0.2:
                    signal = "BULLISH_CONFIRM"
                    score = 2 if oi_change > 3 else 1
                elif oi_change > 0 and price_change < -0.2:
                    signal = "BEARISH_CONFIRM"
                    score = -2 if oi_change > 3 else -1
                elif oi_change < 0 and price_change > 0.2:
                    signal = "SHORT_SQUEEZE"
                    score = -1  # Ненадёжный рост
                elif oi_change < 0 and price_change < -0.2:
                    signal = "LONG_SQUEEZE"
                    score = 1   # Ненадёжное падение
            
            result = {
                "signal": signal,
                "oi_change_pct": round(oi_change, 2),
                "price_change_pct": round(price_change, 2),
                "score": score
            }
            
            if signal != "NEUTRAL":
                print(f"[OI] {symbol}: {signal} (OI={oi_change:+.1f}%, price={price_change:+.2f}%, score={score:+d})")
            
            return result
            
        except Exception as e:
            print(f"[OI] {symbol} error: {e}")
            return {"signal": "NEUTRAL", "oi_change_pct": 0, "price_change_pct": 0, "score": 0}
