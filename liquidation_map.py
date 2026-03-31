import requests
import time
import numpy as np

class LiquidationMap:
    def __init__(self):
        self.cache = {}
        self.cache_ttl = 300  # 5 минут
        self.bingx_api = "https://open-api.bingx.com"

    def get_open_interest(self, symbol):
        try:
            r = requests.get(f"{self.bingx_api}/openApi/swap/v2/quote/openInterest",
                params={"symbol": symbol}, timeout=10)
            d = r.json()
            if d.get("code") == 0:
                return float(d["data"]["openInterest"])
        except: pass
        return 0

    def get_orderbook(self, symbol, limit=50):
        try:
            r = requests.get(f"{self.bingx_api}/openApi/swap/v2/quote/depth",
                params={"symbol": symbol, "limit": limit}, timeout=10)
            d = r.json()
            if d.get("code") == 0:
                return d.get("data", {})
        except: pass
        return {}

    def get_coinglass_liquidations(self, symbol):
        try:
            coin = symbol.replace("-USDT", "")
            r = requests.get(
                f"https://open-api.coinglass.com/public/v2/liquidation_chart",
                params={"symbol": coin, "interval": "1h"},
                headers={"coinglassSecret": ""},
                timeout=10
            )
            if r.status_code == 200:
                return r.json().get("data", {})
        except: pass
        return {}

    def calc_liquidation_levels(self, symbol, current_price, leverage=10):
        try:
            cache_key = symbol
            now = time.time()
            if cache_key in self.cache and (now - self.cache.get(cache_key + "_time", 0)) < self.cache_ttl:
                return self.cache[cache_key]

            # Получаем стакан ордеров
            book = self.get_orderbook(symbol, 100)
            bids = [(float(p), float(v)) for p, v in book.get("bids", [])[:20]]
            asks = [(float(p), float(v)) for p, v in book.get("asks", [])[:20]]

            # Считаем уровни ликвидации на основе плеча
            # LONG ликвидируется при падении на (1/leverage - maintenance_margin)
            maintenance = 0.005  # 0.5% maintenance margin
            liq_long_pct = (1 / leverage) - maintenance
            liq_short_pct = (1 / leverage) - maintenance

            # Кластеры ликвидаций вокруг текущей цены
            liq_long_price = current_price * (1 - liq_long_pct)   # где ликвидируются лонги
            liq_short_price = current_price * (1 + liq_short_pct)  # где ликвидируются шорты

            # Ищем стены в стакане (крупные ордера)
            bid_walls = [(p, v) for p, v in bids if v > np.mean([x[1] for x in bids]) * 2]
            ask_walls = [(p, v) for p, v in asks if v > np.mean([x[1] for x in asks]) * 2]

            # Open Interest
            oi = self.get_open_interest(symbol)

            result = {
                "liq_long": round(liq_long_price, 4),
                "liq_short": round(liq_short_price, 4),
                "liq_long_pct": round(liq_long_pct * 100, 2),
                "liq_short_pct": round(liq_short_pct * 100, 2),
                "bid_walls": bid_walls[:3],
                "ask_walls": ask_walls[:3],
                "open_interest": oi,
                "current_price": current_price
            }

            self.cache[cache_key] = result
            self.cache[cache_key + "_time"] = now
            return result
        except Exception as e:
            print(f"[LIQ] Error: {e}")
            return None

    def get_signal(self, symbol, current_price, side="LONG"):
        liq = self.calc_liquidation_levels(symbol, current_price)
        if not liq:
            return "NEUTRAL", None

        signals = []

        # Цена близко к зоне ликвидации лонгов — осторожно с лонгами
        dist_to_liq_long = abs(current_price - liq["liq_long"]) / current_price * 100
        dist_to_liq_short = abs(current_price - liq["liq_short"]) / current_price * 100

        if side == "LONG" and dist_to_liq_long < 2.0:
            print(f"[LIQ] {symbol}: LONG ликвидации близко @ {liq['liq_long']:.4f} ({dist_to_liq_long:.1f}% от цены)")
            return "AVOID_LONG", liq

        if side == "SHORT" and dist_to_liq_short < 2.0:
            print(f"[LIQ] {symbol}: SHORT ликвидации близко @ {liq['liq_short']:.4f} ({dist_to_liq_short:.1f}% от цены)")
            return "AVOID_SHORT", liq

        # Стены ордеров как уровни поддержки/сопротивления
        for wall_price, wall_vol in liq["bid_walls"]:
            if abs(current_price - wall_price) / current_price < 0.01:
                print(f"[LIQ] {symbol}: Крупная стена BID @ {wall_price:.4f} (объём {wall_vol:.1f})")
                signals.append("SUPPORT")

        for wall_price, wall_vol in liq["ask_walls"]:
            if abs(current_price - wall_price) / current_price < 0.01:
                print(f"[LIQ] {symbol}: Крупная стена ASK @ {wall_price:.4f} (объём {wall_vol:.1f})")
                signals.append("RESISTANCE")

        if "SUPPORT" in signals:
            return "BULLISH", liq
        elif "RESISTANCE" in signals:
            return "BEARISH", liq

        print(f"[LIQ] {symbol}: Liq_LONG={liq['liq_long']:.4f} Liq_SHORT={liq['liq_short']:.4f} OI={liq['open_interest']:.0f}")
        return "NEUTRAL", liq
