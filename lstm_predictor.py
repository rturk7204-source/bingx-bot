import numpy as np
import os, joblib

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
MODEL_DIR = "/root/bingx-bot/models"

class LSTMPredictor:
    def __init__(self):
        self.models = {}
        self.scalers = {}
        self._load_models()

    def _load_models(self):
        for f in os.listdir(MODEL_DIR):
            if f.endswith("_lstm.keras"):
                sym = f.replace("_lstm.keras","").replace("_","-")
                try:
                    import tensorflow as tf
                    tf.get_logger().setLevel("ERROR")
                    from tensorflow.keras.models import load_model
                    self.models[sym] = load_model(os.path.join(MODEL_DIR, f))
                    scaler_path = os.path.join(MODEL_DIR, f.replace("_lstm.keras","_lstm_scaler.pkl"))
                    if os.path.exists(scaler_path):
                        self.scalers[sym] = joblib.load(scaler_path)
                    print(f"[LSTM] Loaded: {sym}")
                except Exception as e:
                    print(f"[LSTM] Error loading {sym}: {e}")

    def predict(self, symbol, klines):
        if symbol not in self.models or symbol not in self.scalers:
            return 0, 0.0
        try:
            import pandas as pd
            closes = np.array([float(k["close"]) for k in klines])
            highs  = np.array([float(k["high"])  for k in klines])
            lows   = np.array([float(k["low"])   for k in klines])
            vols   = np.array([float(k["volume"]) for k in klines])

            def rsi(c, p=14):
                d = np.diff(c); g = np.where(d>0,d,0); l = np.where(d<0,-d,0)
                ag = np.mean(g[-p:]); al = np.mean(l[-p:])
                return 100-(100/(1+ag/(al+1e-9)))

            def ema(c, p): return pd.Series(c).ewm(span=p, adjust=False).mean().values

            ema9 = ema(closes,9); ema21 = ema(closes,21)
            ema50 = ema(closes,50); ema200 = ema(closes,200)
            ema12 = ema(closes,12); ema26 = ema(closes,26)
            macd_h = (ema12-ema26) - ema(ema12-ema26,9)
            vol_ma = np.array([np.mean(vols[max(0,i-20):i]) for i in range(len(vols))])
            vol_r = vols/(vol_ma+1e-9)
            bb_mid = ema(closes,20)
            bb_std = pd.Series(closes).rolling(20).std().fillna(0).values
            bb_pct = (closes-(bb_mid-2*bb_std))/(4*bb_std+1e-9)
            wr = np.zeros(len(closes))
            for i in range(14,len(closes)):
                hh=np.max(highs[i-14:i]); ll=np.min(lows[i-14:i])
                wr[i] = (hh-closes[i])/(hh-ll+1e-9)*-100
            cci = np.zeros(len(closes))
            for i in range(20,len(closes)):
                tp=(highs[i-20:i]+lows[i-20:i]+closes[i-20:i])/3
                cci[i]=(tp[-1]-np.mean(tp))/(0.015*np.mean(np.abs(tp-np.mean(tp)))+1e-9)
            stoch = np.zeros(len(closes))
            for i in range(14,len(closes)):
                hh=np.max(highs[i-14:i]); ll=np.min(lows[i-14:i])
                stoch[i]=(closes[i]-ll)/(hh-ll+1e-9)*100
            roc = np.zeros(len(closes))
            for i in range(10,len(closes)): roc[i]=(closes[i]-closes[i-10])/closes[i-10]*100
            trs = [max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
            atr = np.zeros(len(closes))
            for i in range(14,len(closes)): atr[i]=np.mean(trs[max(0,i-14):i])

            feat = np.column_stack([
                np.array([rsi(closes[:i+1]) for i in range(len(closes))]),
                ema9/closes, ema21/closes, ema50/closes,
                (closes-ema200)/(ema200+1e-9), macd_h/closes,
                wr/100, cci/200, vol_r, bb_pct, stoch/100, roc/10,
                atr/closes*100, (highs-lows)/closes*100,
                (closes-lows)/(highs-lows+1e-9)
            ])

            seq_len = self.scalers[symbol]["seq_len"]
            if len(feat) < seq_len: return 0, 0.0

            seq = feat[-seq_len:]
            if np.any(np.isnan(seq)): return 0, 0.0

            scaler = self.scalers[symbol]["scaler"]
            seq_scaled = scaler.transform(seq).reshape(1, seq_len, -1)

            proba = self.models[symbol].predict(seq_scaled, verbose=0)[0]
            pred_class = np.argmax(proba) - 1  # 0->-1, 1->0, 2->1
            confidence = float(proba[np.argmax(proba)])

            return int(pred_class), confidence
        except Exception as e:
            print(f"[LSTM] Predict error {symbol}: {e}")
            return 0, 0.0
