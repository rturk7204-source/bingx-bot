import os
import numpy as np
import sys
from io import StringIO
sys.path.insert(0, "/root/bingx-bot")
from smc_analyzer import SMCAnalyzer
import pandas as pd
import joblib

MODEL_DIR = "/root/bingx-bot/models"

class MLPredictor:
    def __init__(self):
        self.models = {}
        self.lgb_models = {}
        self.smc_analyzer = SMCAnalyzer()
        self.general_model = None
        self.load_models()

    # Только эти пары загружаем в память
    ACTIVE_SYMBOLS = ["ETH-USDT", "SUI-USDT", "DOGE-USDT", "ADA-USDT", "XRP-USDT", "OP-USDT", "LINK-USDT", "FET-USDT", "WLD-USDT"]

    def load_models(self):
        if not os.path.exists(MODEL_DIR):
            print("[ML] models/ directory not found")
            return
        for f in os.listdir(MODEL_DIR):
            if f.endswith("_model.pkl") and f != "general_model.pkl":
                symbol = f.replace("_model.pkl", "").replace("_", "-")
                # Загружаем только активные пары
                if symbol not in self.ACTIVE_SYMBOLS and symbol.replace("-smc", "") not in [s.replace("-USDT","_USDT").replace("-","_").replace("_USDT","-USDT") for s in self.ACTIVE_SYMBOLS]:
                    continue
                try:
                    self.models[symbol] = joblib.load(os.path.join(MODEL_DIR, f))
                    print(f"[ML] Loaded model for {symbol}")
                except Exception as e:
                    print(f"[ML] Error loading {f}: {e}")
        general_path = os.path.join(MODEL_DIR, "general_model.pkl")
        if os.path.exists(general_path):
            try:
                self.general_model = joblib.load(general_path)
                print("[ML] Loaded general model")
            except Exception as e:
                print(f"[ML] Error loading general model: {e}")

        # Загружаем LightGBM модели (ансамбль)
        for f in os.listdir(MODEL_DIR):
            if f.endswith("_lgb_model.pkl"):
                symbol = f.replace("_lgb_model.pkl", "").replace("_", "-")
                if hasattr(self, 'ACTIVE_SYMBOLS') and symbol not in self.ACTIVE_SYMBOLS:
                    continue
                try:
                    self.lgb_models[symbol] = joblib.load(os.path.join(MODEL_DIR, f))
                    print(f"[ML] Loaded LGB model for {symbol}")
                except Exception as e:
                    print(f"[ML] Error loading LGB {f}: {e}")

    def calc_rsi(self, closes, period=14):
        closes = np.array(closes, dtype=float)
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.convolve(gains, np.ones(period)/period, mode="valid")
        avg_loss = np.convolve(losses, np.ones(period)/period, mode="valid")
        rs = np.where(avg_loss == 0, 100, avg_gain / (avg_loss + 1e-9))
        rsi = 100 - 100 / (1 + rs)
        return rsi

    def calc_ema(self, closes, period):
        return pd.Series(closes, dtype=float).ewm(span=period, adjust=False).mean().values

    def calc_macd_hist(self, closes, fast=12, slow=26, signal=9):
        ema_fast = self.calc_ema(closes, fast)
        ema_slow = self.calc_ema(closes, slow)
        macd_line = ema_fast - ema_slow
        signal_line = self.calc_ema(macd_line, signal)
        return macd_line - signal_line

    def calc_bollinger(self, closes, period=20, std_dev=2):
        s = pd.Series(closes, dtype=float)
        sma = s.rolling(period).mean().values
        std = s.rolling(period).std().values
        upper = sma + std_dev * std
        lower = sma - std_dev * std
        width = np.where(sma != 0, (upper - lower) / (sma + 1e-9), 0)
        pct_b = np.where((upper - lower) != 0,
                         (closes - lower) / (upper - lower + 1e-9), 0.5)
        return width, pct_b

    def extract_features(self, klines):
        closes  = np.array([float(k["close"]) for k in klines])
        highs   = np.array([float(k["high"]) for k in klines])
        lows    = np.array([float(k["low"]) for k in klines])
        volumes = np.array([float(k["volume"]) for k in klines])

        if len(closes) < 60:
            return None

        rsi      = self.calc_rsi(closes, 14)
        ema9     = self.calc_ema(closes, 9)
        ema21    = self.calc_ema(closes, 21)
        ema50    = self.calc_ema(closes, 50)
        ema200   = pd.Series(closes, dtype=float).ewm(span=200, adjust=False).mean().values

        # Williams %R
        wr = np.zeros(len(closes))
        for i in range(14, len(closes)):
            hh = np.max(highs[i-14:i])
            ll = np.min(lows[i-14:i])
            wr[i] = (hh - closes[i]) / (hh - ll + 1e-9) * -100

        # CCI
        cci = np.zeros(len(closes))
        for i in range(20, len(closes)):
            tp = (highs[i-20:i] + lows[i-20:i] + closes[i-20:i]) / 3
            mean_tp = np.mean(tp)
            mad = np.mean(np.abs(tp - mean_tp))
            cci[i] = (tp[-1] - mean_tp) / (0.015 * mad + 1e-9)
        macd_h   = self.calc_macd_hist(closes)
        bb_w, bb_p = self.calc_bollinger(closes, 20)

        idx = len(closes) - 1
        rsi_val = rsi[-1] if len(rsi) > 0 else 50.0
        volume_ma = np.mean(volumes[max(0,idx-20):idx]) if idx > 0 else volumes[-1]

        # ATR
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        trs = np.array(trs)
        atr_val = np.mean(trs[-14:]) if len(trs) >= 14 else 0
        atr_pct = (atr_val / closes[idx] * 100) if closes[idx] > 0 else 0
        avg_atr = np.mean(trs[-50:]) if len(trs) >= 50 else atr_val
        atr_ratio = atr_val / (avg_atr + 1e-9)

        # EMA200 расстояние
        ema200_val = ema200[idx]
        dist_ema200 = (closes[idx] - ema200_val) / ema200_val * 100 if ema200_val > 0 else 0

        # Momentum
        mom_val = (closes[idx] - closes[idx-10]) / closes[idx-10] * 100 if idx >= 10 else 0

        f = [
            rsi_val,
            ema9[idx] - ema21[idx],
            ema21[idx] - ema50[idx],
            (closes[idx] - lows[idx]) / (highs[idx] - lows[idx] + 1e-9),
            volumes[idx] / (volume_ma + 1e-9),
            (closes[idx] - closes[idx-1]) / closes[idx-1] * 100 if idx > 0 else 0,
            (closes[idx] - closes[idx-3]) / closes[idx-3] * 100 if idx > 2 else 0,
            (closes[idx] - closes[idx-7]) / closes[idx-7] * 100 if idx > 6 else 0,
            macd_h[idx] if idx < len(macd_h) else 0,
            bb_w[idx] if idx < len(bb_w) else 0,
            bb_p[idx] if idx < len(bb_p) else 0.5,
            atr_pct,
            atr_ratio,
            dist_ema200,
            mom_val,
        ]
        # SMC фичи (8 признаков)
        try:
            from io import StringIO
            import sys
            window = klines[-50:] if len(klines) >= 50 else klines
            old_stdout = sys.stdout; sys.stdout = StringIO()
            try: smc_res = self.smc_analyzer.analyze(window)
            finally: sys.stdout = old_stdout
            price = closes[idx]
            details = smc_res.get('details', {})
            smc_score = min(smc_res.get('score', 0) / 10.0, 1.0)
            smc_dir = 1.0 if smc_res.get('signal') == 'BULLISH' else (-1.0 if smc_res.get('signal') == 'BEARISH' else 0.0)
            bull_ob = details.get('bullish_ob')
            dist_bull_ob = (price - (bull_ob['high']+bull_ob['low'])/2) / price * 100 if bull_ob else 0.0
            bear_ob = details.get('bearish_ob')
            dist_bear_ob = ((bear_ob['high']+bear_ob['low'])/2 - price) / price * 100 if bear_ob else 0.0
            bull_fvg = 1.0 if details.get('bullish_fvg') else 0.0
            bear_fvg = 1.0 if details.get('bearish_fvg') else 0.0
            sweep = details.get('sweep', 'NONE')
            sweep_val = 1.0 if sweep == 'BULLISH_SWEEP' else (-1.0 if sweep == 'BEARISH_SWEEP' else 0.0)
            bos = str(details.get('bos', ''))
            bos_val = (3.0 if 'CHOCH' in bos else (2.0 if 'BOS' in bos else 0.0)) / 3.0
            f += [smc_score, smc_dir, dist_bull_ob, dist_bear_ob, bull_fvg, bear_fvg, sweep_val, bos_val]
        except:
            f += [0.0] * 8

        return np.array(f).reshape(1, -1)

    def predict(self, symbol, klines):
        features = self.extract_features(klines)
        if features is None:
            return 0

        model = self.models.get(symbol, self.general_model)
        if model is None:
            return 0

        try:
            prediction = model.predict(features)[0]
            proba = model.predict_proba(features)[0]
            max_proba = np.max(proba)

            # Возвращаем сигнал только при уверенности > 55%
            if max_proba < 0.55:
                return 0  # HOLD
            return int(prediction)  # 1=UP, -1=DOWN, 0=HOLD
        except Exception as e:
            print(f"[ML] Prediction error: {e}")
            return 0

    def predict_with_confidence(self, symbol, klines):
        features = self.extract_features(klines)
        if features is None:
            return 0, 0.0

        xgb_model = self.models.get(symbol, self.general_model)
        lgb_model = self.lgb_models.get(symbol)

        if xgb_model is None:
            return 0, 0.0

        try:
            xgb_pred = xgb_model.predict(features)[0]
            xgb_proba = xgb_model.predict_proba(features)[0]
            xgb_conf = float(np.max(xgb_proba))

            # Если есть LGB — ансамбль
            if lgb_model is not None:
                try:
                    lgb_pred = lgb_model.predict(features)[0]
                    lgb_proba = lgb_model.predict_proba(features)[0]
                    lgb_conf = float(np.max(lgb_proba))

                    # Согласие — усредняем вероятности
                    if int(xgb_pred) == int(lgb_pred):
                        avg_conf = (xgb_conf + lgb_conf) / 2
                        return int(xgb_pred), avg_conf
                    else:
                        # Несогласие — берём модель с большей уверенностью, но снижаем conf
                        if xgb_conf >= lgb_conf:
                            return int(xgb_pred), xgb_conf * 0.7
                        else:
                            return int(lgb_pred), lgb_conf * 0.7
                except Exception:
                    pass

            # Fallback — только XGBoost
            return int(xgb_pred), xgb_conf
        except Exception as e:
            print(f"[ML] Prediction error: {e}")
            return 0, 0.0
