#!/usr/bin/env python3
"""Еженедельная проверка feature importance — запускается по cron"""
import os, json, joblib, numpy as np
from datetime import datetime

MODEL_DIR = '/root/bingx-bot/models'
HISTORY_FILE = '/root/bingx-bot/feature_importance_history.json'
FEATURE_NAMES = ['RSI','EMA_fast','EMA_slow','Stoch_K','Williams_R','CCI','ROC_1','ROC_3','ROC_7','MACD_hist','BB_width','BB_pct','ATR_pct','ATR_ratio','EMA200_dist','Momentum']

def get_avg_importance():
    models = sorted([f for f in os.listdir(MODEL_DIR) if f.endswith('_model.pkl')])
    all_imp = []
    for fname in models:
        try:
            model = joblib.load(os.path.join(MODEL_DIR, fname))
            imp = None
            if hasattr(model, 'feature_importances_'):
                imp = model.feature_importances_
            elif hasattr(model, 'named_steps'):
                for s in model.named_steps.values():
                    if hasattr(s, 'feature_importances_'):
                        imp = s.feature_importances_
                    elif hasattr(s, 'estimators_'):
                        imps = [e.feature_importances_ for e in s.estimators_ if hasattr(e, 'feature_importances_')]
                        if imps:
                            ml = min(len(i) for i in imps)
                            imp = np.mean([i[:ml] for i in imps], axis=0)
            if imp is not None:
                total = imp.sum()
                if total > 0:
                    imp = imp / total * 100
                all_imp.append(imp)
        except:
            pass
    if not all_imp:
        return None
    ml = min(len(i) for i in all_imp)
    return np.mean([i[:ml] for i in all_imp], axis=0).tolist()

def main():
    avg = get_avg_importance()
    if avg is None:
        print("[FI] Нет моделей")
        return

    n = min(len(avg), len(FEATURE_NAMES))
    names = FEATURE_NAMES[:n]
    
    # Загружаем историю
    history = []
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                history = json.load(f)
        except:
            pass

    entry = {"date": datetime.utcnow().isoformat()[:10], "importances": {names[i]: round(avg[i], 2) for i in range(n)}}
    
    # Сравниваем с прошлым
    if history:
        prev = history[-1].get("importances", {})
        shifts = []
        for i in range(n):
            prev_val = prev.get(names[i], avg[i])
            diff = avg[i] - prev_val
            if abs(diff) > 2.0:  # Сдвиг > 2%
                shifts.append(f"{names[i]}: {prev_val:.1f}% → {avg[i]:.1f}% ({diff:+.1f}%)")
        if shifts:
            print(f"[FI] ⚠️ Значительные изменения feature importance:")
            for s in shifts:
                print(f"  {s}")
        else:
            print(f"[FI] Feature importance стабильна")
    
    top3 = sorted(range(n), key=lambda i: avg[i], reverse=True)[:3]
    print(f"[FI] Топ-3: {' > '.join(f'{names[i]}({avg[i]:.1f}%)' for i in top3)}")

    history.append(entry)
    history = history[-30:]  # Храним 30 записей
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

if __name__ == '__main__':
    main()
