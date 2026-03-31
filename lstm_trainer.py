import numpy as np
import os
import joblib
import requests
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_class_weight

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

MODEL_DIR = "/root/bingx-bot/models"
BINGX_API = "https://open-api.bingx.com"

def get_klines(symbol, limit=1440):
    url = f"{BINGX_API}/openApi/swap/v3/quote/klines"
    try:
        r = requests.get(url, params={"symbol":symbol,"interval":"1h","limit":limit}, timeout=15)
        d = r.json()
        return d["data"] if d.get("code")==0 else []
    except: return []

def prepare_lstm_features(klines, seq_len=24):
    import pandas as pd
    closes = np.array([float(k["close"]) for k in klines])
    highs  = np.array([float(k["high"])  for k in klines])
    lows   = np.array([float(k["low"])   for k in klines])
    vols   = np.array([float(k["volume"]) for k in klines])

    # RSI
    def calc_rsi(c, p=14):
        d = np.diff(c)
        g = np.where(d>0,d,0); l = np.where(d<0,-d,0)
        rsi = np.zeros(len(c))
        for i in range(p, len(c)):
            ag = np.mean(g[max(0,i-p):i])
            al = np.mean(l[max(0,i-p):i])
            rsi[i] = 100-(100/(1+ag/(al+1e-9)))
        return rsi

    def calc_ema(c, p):
        return pd.Series(c).ewm(span=p, adjust=False).mean().values

    def calc_atr(h,l,c,p=14):
        trs = [max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(c))]
        atr = np.zeros(len(c))
        for i in range(p, len(c)):
            atr[i] = np.mean(trs[max(0,i-p):i])
        return atr

    rsi = calc_rsi(closes)
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    ema200 = calc_ema(closes, 200)
    atr = calc_atr(highs, lows, closes)

    # MACD
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd = ema12 - ema26
    signal = calc_ema(macd, 9)
    macd_hist = macd - signal

    # Williams %R
    wr = np.zeros(len(closes))
    for i in range(14, len(closes)):
        hh = np.max(highs[i-14:i])
        ll = np.min(lows[i-14:i])
        wr[i] = (hh-closes[i])/(hh-ll+1e-9)*-100

    # CCI
    cci = np.zeros(len(closes))
    for i in range(20, len(closes)):
        tp = (highs[i-20:i]+lows[i-20:i]+closes[i-20:i])/3
        cci[i] = (tp[-1]-np.mean(tp))/(0.015*np.mean(np.abs(tp-np.mean(tp)))+1e-9)

    # Volume ratio
    vol_ma = np.array([np.mean(vols[max(0,i-20):i]) for i in range(len(vols))])
    vol_ratio = vols / (vol_ma + 1e-9)

    # BB
    bb_mid = calc_ema(closes, 20)
    bb_std = pd.Series(closes).rolling(20).std().fillna(0).values
    bb_upper = bb_mid + 2*bb_std
    bb_lower = bb_mid - 2*bb_std
    bb_pct = (closes - bb_lower) / (bb_upper - bb_lower + 1e-9)

    # Stochastic
    stoch = np.zeros(len(closes))
    for i in range(14, len(closes)):
        hh = np.max(highs[i-14:i])
        ll = np.min(lows[i-14:i])
        stoch[i] = (closes[i]-ll)/(hh-ll+1e-9)*100

    # ROC
    roc = np.zeros(len(closes))
    for i in range(10, len(closes)):
        roc[i] = (closes[i]-closes[i-10])/closes[i-10]*100

    # Собираем матрицу фичей: [n_samples, n_features]
    feat_matrix = np.column_stack([
        rsi, ema9/closes, ema21/closes, ema50/closes,
        (closes-ema200)/ema200, macd_hist/closes,
        wr/100, cci/200, vol_ratio,
        bb_pct, stoch/100, roc/10,
        atr/closes*100, (highs-lows)/closes*100,
        (closes-lows)/(highs-lows+1e-9)
    ])

    # Метки: 3 класса
    labels = np.zeros(len(closes), dtype=int)
    for i in range(len(closes)-1):
        chg = (closes[i+1]-closes[i])/closes[i]*100
        if chg > 0.8: labels[i] = 1
        elif chg < -0.8: labels[i] = -1

    # Создаём последовательности для LSTM [samples, seq_len, features]
    X, y = [], []
    start = max(200, seq_len)
    for i in range(start, len(closes)-1):
        seq = feat_matrix[i-seq_len:i]
        if not np.any(np.isnan(seq)):
            X.append(seq)
            y.append(labels[i])

    return np.array(X), np.array(y)

def build_lstm_model(seq_len, n_features):
    import tensorflow as tf
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization
    from tensorflow.keras.optimizers import Adam

    model = Sequential([
        LSTM(64, input_shape=(seq_len, n_features), return_sequences=True),
        BatchNormalization(),
        Dropout(0.2),
        LSTM(32, return_sequences=False),
        BatchNormalization(),
        Dropout(0.2),
        Dense(16, activation="relu"),
        Dense(3, activation="softmax")
    ])
    model.compile(optimizer=Adam(0.001), loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model

def train_lstm(symbol, seq_len=24):
    import tensorflow as tf
    tf.get_logger().setLevel("ERROR")

    print(f"[LSTM] {symbol}: загружаю данные...")
    klines = get_klines(symbol)
    if len(klines) < 300:
        print(f"[LSTM] {symbol}: мало данных")
        return None

    X, y = prepare_lstm_features(klines, seq_len)
    if len(X) < 100:
        print(f"[LSTM] {symbol}: мало примеров")
        return None

    # Балансировка классов
    y_mapped = y + 1  # -1->0, 0->1, 1->2
    classes = np.unique(y_mapped)
    weights = compute_class_weight("balanced", classes=classes, y=y_mapped)
    class_weight = {int(c): float(w) for c, w in zip(classes, weights)}

    print(f"[LSTM] {symbol}: примеров={len(X)} | классы={dict(zip([-1,0,1],[np.sum(y==-1),np.sum(y==0),np.sum(y==1)]))} | веса={class_weight}")

    # Нормализация
    scaler = StandardScaler()
    X_flat = X.reshape(-1, X.shape[-1])
    X_flat_scaled = scaler.fit_transform(X_flat)
    X_scaled = X_flat_scaled.reshape(X.shape)

    # Train/val split
    split = int(len(X_scaled) * 0.8)
    X_train, X_val = X_scaled[:split], X_scaled[split:]
    y_train, y_val = y_mapped[:split], y_mapped[split:]

    # Строим и обучаем
    model = build_lstm_model(seq_len, X.shape[-1])

    from tensorflow.keras.callbacks import EarlyStopping
    es = EarlyStopping(patience=5, restore_best_weights=True, verbose=0)

    history = model.fit(
        X_train, y_train,
        validation_data=(X_val, y_val),
        epochs=30, batch_size=32,
        class_weight=class_weight,
        callbacks=[es], verbose=0
    )

    val_acc = max(history.history.get("val_accuracy", [0])) * 100
    train_acc = max(history.history.get("accuracy", [0])) * 100

    # Сохраняем
    model_path = f"{MODEL_DIR}/{symbol.replace('-','_')}_lstm.keras"
    scaler_path = f"{MODEL_DIR}/{symbol.replace('-','_')}_lstm_scaler.pkl"
    model.save(model_path)
    joblib.dump({"scaler": scaler, "seq_len": seq_len}, scaler_path)

    print(f"[LSTM] {symbol}: train={train_acc:.1f}% val={val_acc:.1f}% | сохранено")
    return val_acc

if __name__ == "__main__":
    SYMBOLS = ["BTC-USDT","ETH-USDT","SUI-USDT","XRP-USDT","AVAX-USDT",
               "TIA-USDT","ADA-USDT","DOGE-USDT","SOL-USDT","ONDO-USDT"]
    print("="*55)
    print("ОБУЧЕНИЕ LSTM МОДЕЛЕЙ + БАЛАНСИРОВКА КЛАССОВ")
    print("="*55)
    results = []
    for s in SYMBOLS:
        acc = train_lstm(s)
        if acc: results.append((s, acc))
    print()
    print("="*55)
    print("РЕЗУЛЬТАТЫ:")
    for s, acc in sorted(results, key=lambda x: -x[1]):
        bar = chr(9608) * int(acc/5)
        print(f"  {s:<15} {acc:>5.1f}%  {bar}")
    if results:
        print(f"  Средняя точность: {sum(a for _,a in results)/len(results):.1f}%")
    print("="*55)
