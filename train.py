import os
import time
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, classification_report
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import requests

SYMBOLS = ["ETH-USDT", "EIGEN-USDT", "SUI-USDT", "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "XRP-USDT", "TIA-USDT", "SOL-USDT"]
MODEL_DIR = "/root/bingx-bot/models"
os.makedirs(MODEL_DIR, exist_ok=True)

BINGX_API = "https://open-api.bingx.com"

def get_klines(symbol, interval="1h", limit=1000):
    url = f"{BINGX_API}/openApi/swap/v2/quote/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("code") == 0:
            return data["data"]
    except Exception as e:
        print(f"  Error fetching {symbol}: {e}")
    return []

def calc_rsi(closes, period=14):
    closes = np.array(closes, dtype=float)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.convolve(gains, np.ones(period)/period, mode='valid')
    avg_loss = np.convolve(losses, np.ones(period)/period, mode='valid')
    rs = np.where(avg_loss == 0, 100, avg_gain / (avg_loss + 1e-9))
    rsi = 100 - 100 / (1 + rs)
    return rsi

def calc_ema(closes, period):
    return pd.Series(closes, dtype=float).ewm(span=period, adjust=False).mean().values

def calc_macd(closes, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def calc_bollinger(closes, period=20, std_dev=2):
    s = pd.Series(closes, dtype=float)
    sma = s.rolling(period).mean().values
    std = s.rolling(period).std().values
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    width = np.where(sma != 0, (upper - lower) / (sma + 1e-9), 0)
    pct_b = np.where((upper - lower) != 0, (closes - lower) / (upper - lower + 1e-9), 0.5)
    return width, pct_b

def calc_atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    trs = np.array(trs)
    atr = np.convolve(trs, np.ones(period)/period, mode="valid")
    return atr

def calc_ema200(closes):
    return pd.Series(closes, dtype=float).ewm(span=200, adjust=False).mean().values

def calc_momentum(closes, period=10):
    closes = np.array(closes, dtype=float)
    mom = np.zeros(len(closes))
    for i in range(period, len(closes)):
        mom[i] = (closes[i] - closes[i-period]) / closes[i-period] * 100
    return mom

def calc_williams_r(highs, lows, closes, period=14):
    wr = np.zeros(len(closes))
    for i in range(period, len(closes)):
        hh = np.max(highs[i-period:i])
        ll = np.min(lows[i-period:i])
        if hh - ll > 0:
            wr[i] = (hh - closes[i]) / (hh - ll) * -100
        else:
            wr[i] = -50
    return wr

def calc_cci(highs, lows, closes, period=20):
    cci = np.zeros(len(closes))
    for i in range(period, len(closes)):
        tp = (highs[i-period:i] + lows[i-period:i] + closes[i-period:i]) / 3
        mean_tp = np.mean(tp)
        mad = np.mean(np.abs(tp - mean_tp))
        cci[i] = (tp[-1] - mean_tp) / (0.015 * mad + 1e-9)
    return cci

def prepare_features(klines):
    closes  = np.array([float(k["close"]) for k in klines])
    highs   = np.array([float(k["high"])  for k in klines])
    lows    = np.array([float(k["low"])   for k in klines])
    volumes = np.array([float(k["volume"]) for k in klines])

    rsi   = calc_rsi(closes, 14)
    ema9  = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50)
    ema200 = calc_ema200(closes)
    _, _, macd_hist = calc_macd(closes)
    bb_width, bb_pct = calc_bollinger(closes, 20)
    atr = calc_atr(highs, lows, closes, 14)
    williams_r = calc_williams_r(highs, lows, closes, 14)
    cci = calc_cci(highs, lows, closes, 20)
    momentum = calc_momentum(closes, 10)

    # Минимальная длина с учётом всех индикаторов (rsi теряет period+period-1=27 точек)
    min_len = min(len(rsi), len(ema9)-50, len(ema21)-50, len(ema50)-50) - 30
    if min_len < 10:
        return np.array([]), np.array([])
    offset = len(closes) - min_len

    features, labels = [], []
    for i in range(min_len - 1):
        idx = offset + i
        if idx + 1 >= len(closes):
            break
        price_change = (closes[idx+1] - closes[idx]) / closes[idx] * 100
        # Трёхклассовая разметка: 1=UP, -1=DOWN, 0=HOLD
        if price_change > 0.8:
            label = 1
        elif price_change < -0.8:
            label = -1
        else:
            label = 0

        rsi_idx = i  # rsi shorter than closes by (period-1)*2 approx
        atr_idx = idx - 1 if idx > 0 else 0
        atr_val = atr[min(atr_idx, len(atr)-1)] if len(atr) > 0 else 0
        atr_pct = (atr_val / closes[idx] * 100) if closes[idx] > 0 else 0
        avg_atr = np.mean(atr[max(0,atr_idx-50):atr_idx+1]) if len(atr) > 0 else 0
        atr_ratio = atr_val / (avg_atr + 1e-9)
        ema200_val = ema200[idx]
        dist_ema200 = (closes[idx] - ema200_val) / ema200_val * 100 if ema200_val > 0 else 0
        mom_val = momentum[idx]

        f = [
            rsi[min(rsi_idx, len(rsi)-1)],
            ema9[idx] - ema21[idx],
            ema21[idx] - ema50[idx],
            (closes[idx] - lows[idx]) / (highs[idx] - lows[idx] + 1e-9),  # Stoch %K
            volumes[idx] / (np.mean(volumes[max(0,idx-20):idx]) + 1e-9),
            (closes[idx] - closes[idx-1]) / closes[idx-1] * 100 if idx > 0 else 0,
            (closes[idx] - closes[idx-3]) / closes[idx-3] * 100 if idx > 2 else 0,
            (closes[idx] - closes[idx-7]) / closes[idx-7] * 100 if idx > 6 else 0,
            macd_hist[idx] if idx < len(macd_hist) else 0,
            bb_width[idx] if idx < len(bb_width) else 0,
            bb_pct[idx] if idx < len(bb_pct) else 0.5,
            atr_pct,          # ATR в % от цены
            atr_ratio,        # ATR относительно среднего
            dist_ema200,      # расстояние от EMA200
            mom_val,          # momentum 10 свечей
        ]
        features.append(f)
        labels.append(label)

    return np.array(features), np.array(labels)

NFEATURES = 15

print("=" * 60)
print("  ОБУЧЕНИЕ ML-МОДЕЛИ (v2) — 3 класса + 11 фичей")
print("=" * 60)

all_features, all_labels = [], []

for symbol in SYMBOLS:
    print(f"\n[{symbol}] Скачиваю данные (1000 свечей 1h)...")
    klines = get_klines(symbol, "1h", 1440)
    if len(klines) < 100:
        print(f"  Недостаточно данных для {symbol}, пропускаю")
        continue
    print(f"  Получено {len(klines)} свечей")
    X, y = prepare_features(klines)
    if len(X) == 0:
        print(f"  Не удалось подготовить фичи для {symbol}")
        continue
    print(f"  Подготовлено {len(X)} примеров | классы: UP={np.sum(y==1)}, DOWN={np.sum(y==-1)}, HOLD={np.sum(y==0)}")
    all_features.append(X)
    all_labels.append(y)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    rf = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42, n_jobs=-1, class_weight='balanced')
    xgb = XGBClassifier(n_estimators=200, max_depth=6, learning_rate=0.05, random_state=42, n_jobs=-1, eval_metric='mlogloss', verbosity=0)
    ensemble = VotingClassifier(estimators=[('rf', rf), ('xgb', xgb)], voting='soft')
    pipeline = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', ensemble)
    ])
    pipeline.fit(X_train, y_train)
    acc = accuracy_score(y_test, pipeline.predict(X_test))
    cv_scores = cross_val_score(pipeline, X, y, cv=5, scoring='accuracy')
    print(f"  Точность: {acc*100:.1f}% | CV: {cv_scores.mean()*100:.1f}% (+/-{cv_scores.std()*100:.1f}%)")

    path = f"{MODEL_DIR}/{symbol.replace('-','_')}_model.pkl"
    joblib.dump(pipeline, path)
    print(f"  Модель сохранена: {path}")
    time.sleep(1)

print("\n" + "=" * 60)
print("  Обучение общей модели на всех парах...")
X_all = np.vstack(all_features)
y_all = np.concatenate(all_labels)
print(f"  Всего примеров: {len(X_all)} | UP={np.sum(y_all==1)}, DOWN={np.sum(y_all==-1)}, HOLD={np.sum(y_all==0)}")
X_train, X_test, y_train, y_test = train_test_split(X_all, y_all, test_size=0.2, random_state=42)

rf_all = RandomForestClassifier(n_estimators=300, max_depth=12, random_state=42, n_jobs=-1, class_weight='balanced')
xgb_all = XGBClassifier(n_estimators=300, max_depth=8, learning_rate=0.05, random_state=42, n_jobs=-1, eval_metric='mlogloss', verbosity=0)
ensemble_all = VotingClassifier(estimators=[("rf", rf_all), ("xgb", xgb_all)], voting='soft')
pipeline_all = Pipeline([
    ('scaler', StandardScaler()),
    ('clf', ensemble_all)
])
pipeline_all.fit(X_train, y_train)
acc_all = accuracy_score(y_test, pipeline_all.predict(X_test))
cv_all = cross_val_score(pipeline_all, X_all, y_all, cv=5, scoring='accuracy')
print(f"  Общая точность: {acc_all*100:.1f}% | CV: {cv_all.mean()*100:.1f}% (+/-{cv_all.std()*100:.1f}%)")
print(classification_report(y_test, pipeline_all.predict(X_test), target_names=['DOWN','HOLD','UP']))
joblib.dump(pipeline_all, f"{MODEL_DIR}/general_model.pkl")
print(f"  Общая модель сохранена: {MODEL_DIR}/general_model.pkl")
print("=" * 60)
print("  ОБУЧЕНИЕ ЗАВЕРШЕНО")
print("=" * 60)
