import os, sys, numpy as np, requests, joblib
from datetime import datetime

MODEL_DIR = "/root/bingx-bot/models"
BINGX_API = "https://open-api.bingx.com"
SYMBOLS = ["EIGEN-USDT","SOL-USDT","LINK-USDT","SUI-USDT","BTC-USDT",
           "ETH-USDT","XRP-USDT","DOGE-USDT","ARB-USDT","ADA-USDT",
           "AVAX-USDT","TIA-USDT","ONDO-USDT"]

def get_klines(symbol, limit=300):
    url = f"{BINGX_API}/openApi/swap/v3/quote/klines"
    try:
        r = requests.get(url, params={"symbol":symbol,"interval":"1h","limit":limit}, timeout=15)
        d = r.json()
        return d["data"] if d.get("code")==0 else []
    except: return []

def get_volatility():
    k = get_klines("BTC-USDT", 24)
    if not k: return 0
    c = [float(x["close"]) for x in k]
    return sum(abs(c[i]/c[i-1]-1) for i in range(1,len(c)))/len(c)*100

def send_telegram(msg):
    try:
        from dotenv import load_dotenv
        load_dotenv("/root/bingx-bot/.env")
        t = os.getenv("TELEGRAM_BOT_TOKEN")
        ch = os.getenv("TELEGRAM_CHAT_ID")
        requests.post(f"https://api.telegram.org/bot{t}/sendMessage",
            data={"chat_id":ch,"text":msg,"parse_mode":"HTML"}, timeout=10)
    except: pass

def online_update():
    sys.path.insert(0, "/root/bingx-bot")
    from train import prepare_features
    from sklearn.metrics import accuracy_score

    now = datetime.now()
    print(f"[ONLINE ML] {now.strftime('%Y-%m-%d %H:%M')}")

    vol = get_volatility()
    print(f"[ONLINE ML] BTC volatility 24h: {vol:.3f}%")

    if vol > 0.5:
        window = 168
    elif vol > 0.3:
        window = 72
    else:
        window = 48
    print(f"[ONLINE ML] Training window: {window}h")

    updated, errors = 0, 0

    for symbol in SYMBOLS:
        path = f"{MODEL_DIR}/{symbol.replace('-','_')}_model.pkl"
        if not os.path.exists(path):
            print(f"[ONLINE ML] {symbol}: no model")
            continue
        try:
            model = joblib.load(path)
            klines = get_klines(symbol, 350)
            if len(klines) < 100: continue

            X, y = prepare_features(klines)
            if X is None or len(X) == 0: continue

            X_new = X[-window:] if len(X) >= window else X
            y_new = y[-window:] if len(y) >= window else y

            if len(np.unique(y_new)) < 2: continue

            acc_before = accuracy_score(y_new[-20:], model.predict(X_new[-20:])) * 100 if len(X_new) > 20 else 0
            model.fit(X_new, y_new)
            acc_after = accuracy_score(y_new[-20:], model.predict(X_new[-20:])) * 100 if len(X_new) > 20 else 0

            joblib.dump(model, path)
            print(f"[ONLINE ML] {symbol}: {len(X_new)}ex | {acc_before:.1f}% -> {acc_after:.1f}% ({acc_after-acc_before:+.1f}%)")
            updated += 1
        except Exception as e:
            print(f"[ONLINE ML] {symbol}: error - {e}")
            errors += 1

    msg = (f"\U0001f9e0 <b>Online ML update</b>\n"
           f"Updated: <b>{updated}/{len(SYMBOLS)}</b>\n"
           f"BTC vol: <b>{vol:.3f}%</b> | window: <b>{window}h</b>\n"
           f"Errors: <b>{errors}</b> | {now.strftime('%H:%M')}")
    send_telegram(msg)
    print(f"[ONLINE ML] Done. Updated: {updated}, errors: {errors}")

if __name__ == "__main__":
    online_update()
