import os, sys, joblib, numpy as np
sys.path.insert(0, '/root/bingx-bot')

MODEL_DIR = '/root/bingx-bot/models'

FEATURE_NAMES = [
    'RSI', 'EMA_fast', 'EMA_slow', 'Stoch_K',
    'Williams_R', 'CCI',
    'ROC_1', 'ROC_3', 'ROC_7',
    'MACD_hist', 'BB_width', 'BB_pct',
    'ATR_pct', 'ATR_ratio', 'EMA200_dist', 'Momentum'
]

def extract_importances(model):
    """Извлекает feature importances из любой структуры модели"""
    # Прямая модель с feature_importances_
    if hasattr(model, 'feature_importances_'):
        return model.feature_importances_
    
    # Pipeline
    if hasattr(model, 'named_steps'):
        for step_name, step in model.named_steps.items():
            imp = extract_importances(step)
            if imp is not None:
                return imp
    
    # VotingClassifier / StackingClassifier
    if hasattr(model, 'estimators_'):
        imps = []
        for est in model.estimators_:
            imp = extract_importances(est)
            if imp is not None:
                imps.append(imp)
        if imps:
            # Приводим к одной длине (минимальная)
            min_len = min(len(i) for i in imps)
            imps = [i[:min_len] for i in imps]
            return np.mean(imps, axis=0)
    
    return None

print('=' * 60)
print('FEATURE IMPORTANCE')
print('=' * 60)

all_imp = []
models = sorted([f for f in os.listdir(MODEL_DIR) if f.endswith('_model.pkl')])

for fname in models:
    symbol = fname.replace('_model.pkl', '').replace('_', '-')
    path = os.path.join(MODEL_DIR, fname)
    try:
        model = joblib.load(path)
        imp = extract_importances(model)
        if imp is None:
            print(f"[{symbol}] Не удалось извлечь importances")
            continue
        
        n_features = len(imp)
        names = FEATURE_NAMES[:n_features] if n_features <= len(FEATURE_NAMES) else [f"f{i}" for i in range(n_features)]
        
        all_imp.append((imp, n_features))
        
        print(f"\n[{symbol}] top-5 ({n_features} features):")
        for i in np.argsort(imp)[::-1][:5]:
            bar = '█' * int(imp[i] * 100)
            print(f"  {names[i]:<15} {imp[i]*100:>5.1f}%  {bar}")
    except Exception as e:
        print(f"[{symbol}] Error: {e}")

if all_imp:
    # Среднее по моделям с одинаковым числом фичей
    min_n = min(n for _, n in all_imp)
    avg = np.mean([imp[:min_n] for imp, _ in all_imp], axis=0)
    names = FEATURE_NAMES[:min_n] if min_n <= len(FEATURE_NAMES) else [f"f{i}" for i in range(min_n)]
    
    print('\n' + '=' * 60)
    print(f'СРЕДНЕЕ по {len(all_imp)} моделям ({min_n} features):')
    for i in np.argsort(avg)[::-1]:
        bar = '█' * int(avg[i] * 100)
        print(f"  {names[i]:<15} {avg[i]*100:>5.1f}%  {bar}")
    
    print('\n🏆 Топ-3 самых важных:')
    for rank, i in enumerate(np.argsort(avg)[::-1][:3], 1):
        print(f"  {rank}. {names[i]}: {avg[i]*100:.1f}%")
    
    print('\n⚠️  Наименее полезные:')
    for i in np.argsort(avg)[:3]:
        print(f"  - {names[i]}: {avg[i]*100:.1f}%")
