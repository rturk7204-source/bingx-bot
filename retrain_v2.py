#!/usr/bin/env python3
import os, sys, json, pickle
import numpy as np
sys.path.insert(0, "/root/bingx-bot")
from bingx_api import BingXAPI
from ml_predictor import MLPredictor
from dotenv import load_dotenv
load_dotenv("/root/bingx-bot/.env")

api = BingXAPI(api_key=os.getenv("BINGX_API_KEY"), secret_key=os.getenv("BINGX_SECRET_KEY"))
predictor = MLPredictor()

SYMBOLS = ["ETH-USDT","SUI-USDT","DOGE-USDT","ADA-USDT","XRP-USDT","OP-USDT","LINK-USDT","FET-USDT","WLD-USDT","TAO-USDT","DOT-USDT","ARB-USDT","PENDLE-USDT","FIL-USDT","AVAX-USDT","EIGEN-USDT","TIA-USDT","NEAR-USDT"]
MODELS_DIR = "/root/bingx-bot/models"
MFE_SYMBOLS = []  # MFE отключён — gap слишком высокий
STRONG_REG = SYMBOLS  # все пары с усиленной регуляризацией

def build_nc(klines):
    X, y = [], []
    if len(klines) < 210: return np.array([]), np.array([])
    for i in range(200, len(klines) - 1):
        w = klines[i-200:i+1]
        try:
            f = predictor.extract_features(w)
            if f is None: continue
            cn, cx = float(klines[i]["close"]), float(klines[i+1]["close"])
            ch = (cx - cn) / cn * 100
            label = 2 if ch > 0.5 else (0 if ch < -0.5 else 1)
            X.append(f.flatten()); y.append(label)
        except: continue
    return np.array(X, dtype=float), np.array(y, dtype=int)

def build_mfe(klines, la=6):
    X, y = [], []
    if len(klines) < 210: return np.array([]), np.array([])
    for i in range(200, len(klines) - la):
        w = klines[i-200:i+1]
        try:
            f = predictor.extract_features(w)
            if f is None: continue
            c = float(klines[i]["close"])
            fh = [float(klines[i+j]["high"]) for j in range(1, la+1)]
            fl = [float(klines[i+j]["low"]) for j in range(1, la+1)]
            mu, md = (max(fh)-c)/c*100, (c-min(fl))/c*100
            label = 2 if mu>md and mu>0.5 else (0 if md>mu and md>0.5 else 1)
            X.append(f.flatten()); y.append(label)
        except: continue
    return np.array(X, dtype=float), np.array(y, dtype=int)

def train_symbol(symbol):
    try:
        raw = api.get_klines(symbol, interval="1h", limit=1440)
        if not raw or raw.get("code") != 0: print(f"[TRAIN] {symbol}: API err"); return
        klines = raw["data"]
        X, y = build_mfe(klines, 6) if symbol in MFE_SYMBOLS else build_nc(klines)
        tt = "MFE" if symbol in MFE_SYMBOLS else "NC"
        if len(X) < 100: print(f"[TRAIN] {symbol}: short"); return
        from xgboost import XGBClassifier
        from lightgbm import LGBMClassifier
        from sklearn.metrics import accuracy_score
        sp = int(len(X)*0.7)
        Xr,Xe,yr,ye = X[:sp],X[sp:],y[:sp],y[sp:]
        if symbol in STRONG_REG:
            p = dict(n_estimators=200,max_depth=3,learning_rate=0.02,subsample=0.6,colsample_bytree=0.6,reg_alpha=2.0,reg_lambda=4.0,min_child_weight=10,gamma=0.2)
        else:
            p = dict(n_estimators=300,max_depth=4,learning_rate=0.03,subsample=0.7,colsample_bytree=0.7,reg_alpha=1.0,reg_lambda=2.0,min_child_weight=5,gamma=0.1)
        xgb = XGBClassifier(**p, objective="multi:softprob",num_class=3,eval_metric="mlogloss",n_jobs=2,random_state=42)
        xgb.fit(Xr,yr,eval_set=[(Xe,ye)],verbose=False)
        xa = accuracy_score(ye,xgb.predict(Xe)); xa_tr = accuracy_score(yr,xgb.predict(Xr))
        lgb = LGBMClassifier(**{k:v for k,v in p.items() if k!='gamma'},num_class=3,objective="multiclass",n_jobs=2,random_state=42,verbose=-1)
        lgb.fit(Xr,yr,eval_set=[(Xe,ye)])
        la = accuracy_score(ye,lgb.predict(Xe))
        sk = symbol.replace("-","_")
        with open(os.path.join(MODELS_DIR,f"{sk}_model.pkl"),"wb") as ff: pickle.dump(xgb,ff)
        with open(os.path.join(MODELS_DIR,f"{sk}_lgb_model.pkl"),"wb") as ff: pickle.dump(lgb,ff)
        gap = xa_tr - xa; st = "OK" if gap<0.15 else "OVERFIT"
        print(f"[TRAIN] {symbol} ({tt}): XGB={xa:.3f} LGB={la:.3f} gap={gap:.3f} [{st}]")
    except Exception as e:
        print(f"[TRAIN] {symbol} ERROR: {e}")

def main():
    from datetime import datetime
    print(f"[TRAIN] === Retrain v2 {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    for s in SYMBOLS: train_symbol(s)
    print("[TRAIN] === DONE ===")

if __name__ == "__main__": main()
