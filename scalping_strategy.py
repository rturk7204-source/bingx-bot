import numpy as np
import time
from datetime import datetime

class ScalpingStrategy:
    def __init__(self):
        self.timeframe = "5m"
        self.atr_tp_mult = 2.5
        self.atr_sl_mult = 1.2
        self.min_profit_pct = 0.8
        self.stop_loss_pct = 0.5
        self.max_position_size = 30.0
        self.min_position_size = 10.0
        self.max_daily_scalps = 6
        self.daily_scalp_count = 0
        self.daily_scalp_pnl = 0.0
        self.last_scalp_day = datetime.now().day
        self.position_open_time = {}
        self.max_position_minutes = 20
        self.trailing_activate_pct = 0.2
        self.trailing_step_pct = 0.15
        self.kill_zones = [
            (7, 32, 9, 0, "London Open"),
            (12, 2, 14, 0, "NY Overlap"),
            (15, 2, 17, 0, "NY Session"),
        ]

    def is_scalp_session(self):
        now = datetime.utcnow()
        h, m = now.hour, now.minute
        for sh, sm, eh, em, name in self.kill_zones:
            if (sh*60+sm) <= (h*60+m) <= (eh*60+em):
                return True, name
        return False, None

    def reset_daily_if_needed(self):
        today = datetime.now().day
        if today != self.last_scalp_day:
            self.daily_scalp_count = 0
            self.daily_scalp_pnl = 0.0
            self.last_scalp_day = today

    def calc_rsi(self, closes, period=7):
        if not closes or len(closes) < period + 1: return 50
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)[-period:]
        losses = np.where(deltas < 0, -deltas, 0)[-period:]
        if len(gains) == 0: return 50
        ag = np.mean(gains)
        al = np.mean(losses)
        return 100 - (100 / (1 + ag / (al + 1e-9)))

    def calc_ema(self, closes, period):
        import pandas as pd
        return pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1]

    def calc_atr(self, highs, lows, closes, period=14):
        if len(closes) < 2: return 0
        trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(closes))]
        if not trs: return 0
        arr = np.array(trs[-period:])
        return float(np.mean(arr)) if len(arr) > 0 else 0

    def calc_adx(self, highs, lows, closes, period=14):
        try:
            if len(closes) < period * 2: return 0
            pdm = [max(highs[i]-highs[i-1], 0) if highs[i]-highs[i-1] > lows[i-1]-lows[i] else 0 for i in range(1, len(highs))]
            mdm = [max(lows[i-1]-lows[i], 0) if lows[i-1]-lows[i] > highs[i]-highs[i-1] else 0 for i in range(1, len(lows))]
            trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(closes))]
            atr = np.mean(trs[-period:])
            if atr == 0: return 0
            pdi = np.mean(pdm[-period:]) / atr * 100
            mdi = np.mean(mdm[-period:]) / atr * 100
            return round(abs(pdi-mdi) / (pdi+mdi+1e-9) * 100, 1)
        except: return 0

    def calc_vwap(self, klines):
        try:
            tp = [(float(k["high"])+float(k["low"])+float(k["close"]))/3 for k in klines]
            vol = [float(k["volume"]) for k in klines]
            return sum(t*v for t,v in zip(tp,vol)) / (sum(vol)+1e-9)
        except: return 0

    def calc_stoch_rsi(self, closes, period=14):
        if len(closes) < period*2: return 50
        rsi_vals = [self.calc_rsi(closes[:i+1], period) for i in range(period, len(closes))]
        if len(rsi_vals) < period: return 50
        arr = np.array(rsi_vals[-period:])
        return round((rsi_vals[-1]-arr.min())/(arr.max()-arr.min()+1e-9)*100, 1)

    def detect_candle_patterns(self, klines):
        if len(klines) < 3: return "NONE"
        k, kp = klines[-1], klines[-2]
        o,h,l,c = float(k["open"]),float(k["high"]),float(k["low"]),float(k["close"])
        po,ph,pl,pc = float(kp["open"]),float(kp["high"]),float(kp["low"]),float(kp["close"])
        body = abs(c-o)
        upper = h-max(o,c)
        lower = min(o,c)-l
        if lower > body*2 and lower > upper*2: return "BULLISH_PIN"
        if upper > body*2 and upper > lower*2: return "BEARISH_PIN"
        if c > po and o < pc and body > abs(pc-po)*1.2: return "BULLISH_ENGULF"
        if c < po and o > pc and body > abs(pc-po)*1.2: return "BEARISH_ENGULF"
        if h > ph and l > pl and c > pc: return "INSIDE_BREAK_UP"
        if l < pl and h < ph and c < pc: return "INSIDE_BREAK_DOWN"
        return "NONE"

    def check_spread(self, api, symbol):
        try:
            book = api._request("GET", "/openApi/swap/v2/quote/depth", {"symbol":symbol,"limit":5}, signed=False)
            if book and book.get("code") == 0:
                bids = book["data"].get("bids",[])
                asks = book["data"].get("asks",[])
                if bids and asks:
                    return (float(asks[0][0])-float(bids[0][0]))/float(bids[0][0])*100 < 0.05
        except: pass
        return True

    def get_15m_trend(self, symbol, api):
        try:
            r = api.get_klines(symbol, interval="15m", limit=30)
            if not r or r.get("code") != 0: return "NEUTRAL"
            closes = [float(k["close"]) for k in r["data"]]
            ema8 = self.calc_ema(closes, 8)
            ema21 = self.calc_ema(closes, 21)
            rsi = self.calc_rsi(closes, 14)
            if ema8 > ema21 and rsi > 50: return "BULLISH"
            if ema8 < ema21 and rsi < 50: return "BEARISH"
            return "NEUTRAL"
        except: return "NEUTRAL"

    def get_btc_5m_trend(self, api):
        try:
            r = api.get_klines("BTC-USDT", interval="5m", limit=10)
            if not r or r.get("code") != 0: return "NEUTRAL"
            closes = [float(k["close"]) for k in r["data"]]
            if closes[-1] > closes[-3]: return "BULLISH"
            if closes[-1] < closes[-3]: return "BEARISH"
        except: pass
        return "NEUTRAL"

    def check_timeout(self, symbol):
        if symbol not in self.position_open_time: return False
        return (time.time()-self.position_open_time[symbol])/60 >= self.max_position_minutes

    def get_dynamic_tp_sl(self, klines5m, price):
        h = [float(k["high"]) for k in klines5m]
        l = [float(k["low"]) for k in klines5m]
        c = [float(k["close"]) for k in klines5m]
        atr = self.calc_atr(h, l, c, 14)
        atr_pct = atr/price*100 if price > 0 else 0
        return round(max(self.min_profit_pct, atr_pct*self.atr_tp_mult),3), round(max(self.stop_loss_pct, atr_pct*self.atr_sl_mult),3)

    def get_scalp_signal(self, symbol, api):
        try:
            self.reset_daily_if_needed()
            in_session, session_name = self.is_scalp_session()
            if not in_session: return "HOLD", 0, {}
            if self.daily_scalp_count >= self.max_daily_scalps:
                return "HOLD", 0, {"reason": "daily_limit"}
            if not self.check_spread(api, symbol):
                return "HOLD", 0, {"reason": "spread_wide"}

            r5m = api.get_klines(symbol, interval="5m", limit=100)
            if not r5m or r5m.get("code") != 0: return "HOLD", 0, {}
            k5m = r5m["data"]
            if len(k5m) < 30: return "HOLD", 0, {}

            closes = [float(k["close"]) for k in k5m]
            highs = [float(k["high"]) for k in k5m]
            lows = [float(k["low"]) for k in k5m]
            vols = [float(k["volume"]) for k in k5m]
            price = closes[-1]

            trend_15m = self.get_15m_trend(symbol, api)
            btc_trend = self.get_btc_5m_trend(api)
            rsi7 = self.calc_rsi(closes, 7)
            ema8 = self.calc_ema(closes, 8)
            ema21 = self.calc_ema(closes, 21)
            vwap = self.calc_vwap(k5m[-20:])
            stoch_k = self.calc_stoch_rsi(closes)
            adx = self.calc_adx(highs, lows, closes)
            candle = self.detect_candle_patterns(k5m[-3:])
            tp_pct, sl_pct = self.get_dynamic_tp_sl(k5m, price)
            avg_vol = np.mean(vols[-20:])
            vol_ok = vols[-1] > avg_vol * 1.3
            mom_up = closes[-1] > closes[-2] > closes[-3]
            mom_down = closes[-1] < closes[-2] < closes[-3]

            details = {"rsi7":rsi7,"stoch":stoch_k,"vwap":round(vwap,6),
                      "session":session_name,"adx":adx,"trend_15m":trend_15m,
                      "candle":candle,"tp_pct":tp_pct,"sl_pct":sl_pct,"vol_ok":vol_ok}

            if adx < 20: return "HOLD", 0, {**details, "reason":"low_adx"}

            buy_score = sell_score = 0
            if rsi7 < 35: buy_score += 2
            elif rsi7 < 45: buy_score += 1
            if stoch_k < 20: buy_score += 2
            elif stoch_k < 35: buy_score += 1
            if price > vwap: buy_score += 1
            if ema8 > ema21: buy_score += 1
            if mom_up: buy_score += 1
            if vol_ok: buy_score += 1
            if candle in ("BULLISH_PIN","BULLISH_ENGULF","INSIDE_BREAK_UP"): buy_score += 2
            if trend_15m == "BULLISH": buy_score += 2
            elif trend_15m == "BEARISH": buy_score -= 1
            if btc_trend == "BULLISH": buy_score += 1
            elif btc_trend == "BEARISH": buy_score -= 1

            if rsi7 > 65: sell_score += 2
            elif rsi7 > 55: sell_score += 1
            if stoch_k > 80: sell_score += 2
            elif stoch_k > 65: sell_score += 1
            if price < vwap: sell_score += 1
            if ema8 < ema21: sell_score += 1
            if mom_down: sell_score += 1
            if vol_ok: sell_score += 1
            if candle in ("BEARISH_PIN","BEARISH_ENGULF","INSIDE_BREAK_DOWN"): sell_score += 2
            if trend_15m == "BEARISH": sell_score += 2
            elif trend_15m == "BULLISH": sell_score -= 1
            if btc_trend == "BEARISH": sell_score += 1
            elif btc_trend == "BULLISH": sell_score -= 1

            details["buy_score"] = buy_score
            details["sell_score"] = sell_score

            if buy_score >= 6 and buy_score > sell_score:
                print(f"[SCALP] {symbol}: BUY {session_name} | RSI={rsi7:.1f} ADX={adx:.1f} 15m={trend_15m} candle={candle} score={buy_score}")
                return "BUY", buy_score, details
            elif sell_score >= 6 and sell_score > buy_score:
                print(f"[SCALP] {symbol}: SELL {session_name} | RSI={rsi7:.1f} ADX={adx:.1f} 15m={trend_15m} candle={candle} score={sell_score}")
                return "SELL", sell_score, details

            return "HOLD", 0, details
        except Exception as e:
            print(f"[SCALP] Error {symbol}: {e}")
            return "HOLD", 0, {}
