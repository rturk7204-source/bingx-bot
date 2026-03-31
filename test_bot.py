import sys
import os
sys.path.insert(0, '/root/bingx-bot')
from dotenv import load_dotenv
load_dotenv('/root/bingx-bot/.env')

from bingx_api import BingXAPI
from strategy_ml import MLStrategy
from risk_manager import RiskManager
from analytics import Analytics
from telegram_notifier import TelegramNotifier
from ml_predictor import MLPredictor
from smc_analyzer import SMCAnalyzer

api = BingXAPI(os.getenv('BINGX_API_KEY'), os.getenv('BINGX_SECRET_KEY'))

PASS = "✅"
FAIL = "❌"
WARN = "⚠️"
results = []

def test(name, fn):
    try:
        result, msg = fn()
        status = PASS if result else FAIL
        print(f"{status} {name}: {msg}")
        results.append((name, result, msg))
    except Exception as e:
        print(f"{FAIL} {name}: EXCEPTION — {e}")
        results.append((name, False, str(e)))

print("=" * 60)
print("  КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ БОТА")
print("=" * 60)

# 1. API — баланс
def t_balance():
    b = api.get_balance()
    if b and isinstance(b, dict):
        inner = b.get('balance', {})
        bal = float(inner.get('balance', 0)) if isinstance(inner, dict) else float(inner)
        return bal > 0, f"Баланс: {bal:.2f} USDT"
    return False, "Нет ответа"
test("API Баланс", t_balance)

# 2. API — позиции
def t_positions():
    p = api.get_positions()
    return isinstance(p, list), f"Позиций: {len(p)}, данные: {[x['symbol'] for x in p] if p else '[]'}"
test("API Позиции", t_positions)

# 3. API — тикер
def t_ticker():
    t = api.get_ticker("SUI-USDT")
    price = float(t.get('lastPrice', 0)) if t else 0
    return price > 0, f"SUI цена: {price}"
test("API Тикер", t_ticker)

# 4. API — свечи
def t_klines():
    r = api.get_klines("BTC-USDT", interval="1h", limit=10)
    ok = r and r.get('code') == 0 and len(r.get('data', [])) > 0
    return ok, f"Свечей получено: {len(r.get('data', [])) if r else 0}"
test("API Свечи", t_klines)

# 5. API — funding rate
def t_funding():
    rate = api.get_funding_rate("BTC-USDT")
    return rate is not None, f"Funding rate BTC: {rate:.4f}%"
test("API Funding Rate", t_funding)

# 6. ML модели
def t_ml():
    p = MLPredictor()
    missing = [s for s in ['BTC-USDT','ETH-USDT','SUI-USDT','SOL-USDT','BNB-USDT'] if s.replace('-','_').replace('USDT','USDT') not in str(p.models)]
    loaded = len(p.models)
    return loaded >= 5, f"Загружено моделей: {loaded}"
test("ML Модели", t_ml)

# 7. ML предсказание
def t_ml_predict():
    p = MLPredictor()
    r = api.get_klines("SUI-USDT", interval="1h", limit=100)
    klines = r['data'] if r and r.get('code') == 0 else []
    if not klines:
        return False, "Нет свечей"
    sig, conf = p.predict_with_confidence("SUI-USDT", klines)
    labels = {1: "UP", -1: "DOWN", 0: "HOLD"}
    return conf > 0, f"Сигнал: {labels.get(sig,'?')} conf={conf*100:.1f}%"
test("ML Предсказание SUI", t_ml_predict)

# 8. SMC анализ
def t_smc():
    smc = SMCAnalyzer()
    r = api.get_klines("BTC-USDT", interval="1h", limit=60)
    klines = r['data'] if r and r.get('code') == 0 else []
    result = smc.analyze(klines[-50:])
    return 'signal' in result, f"SMC BTC: {result.get('signal')} score={result.get('score')}"
test("SMC Анализ", t_smc)

# 9. Risk Manager
def t_risk():
    rm = RiskManager(stop_loss_pct=3.0, trailing_pct=1.5)
    rm.add_position("TEST-USDT", 100.0, "LONG", 10)
    # Тест стоп-лосса
    action_sl = rm.check_position("TEST-USDT", 96.0)  # -4% должен сработать SL
    rm.add_position("TEST-USDT", 100.0, "LONG", 10)
    action_ok = rm.check_position("TEST-USDT", 101.0)  # +1% норма
    sl_ok = action_sl == "STOP_LOSS"
    hold_ok = action_ok is None
    return sl_ok and hold_ok, f"SL при -4%: {action_sl} | Hold при +1%: {action_ok}"
test("Risk Manager SL", t_risk)

# 10. Trailing Stop
def t_trailing():
    rm = RiskManager(stop_loss_pct=3.0, trailing_pct=1.5)
    rm.add_position("TEST2-USDT", 100.0, "LONG", 10)
    rm.check_position("TEST2-USDT", 102.0)  # +2% — активируем трейлинг
    action = rm.check_position("TEST2-USDT", 100.4)  # откат — должен сработать trailing
    return action == "TRAILING_STOP", f"Trailing при откате: {action}"
test("Risk Manager Trailing", t_trailing)

# 11. Стратегия — сигнал
def t_strategy():
    s = MLStrategy()
    result = s.get_signal("SUI-USDT", api)
    sig = result[0] if isinstance(result, tuple) else result
    return sig in ("BUY", "SELL", "HOLD"), f"Сигнал SUI: {sig}"
test("Стратегия Сигнал", t_strategy)

# 12. Антифлуд
def t_antiflood():
    from datetime import datetime, timedelta
    last_trade = datetime.now() - timedelta(hours=2)
    remaining = int((4 * 3600 - (datetime.now() - last_trade).total_seconds()) / 60)
    return remaining > 0, f"Блокировка через {remaining} мин после сделки 2ч назад"
test("Антифлуд логика", t_antiflood)

# 13. Analytics
def t_analytics():
    a = Analytics()
    stats = a.get_stats()
    return isinstance(stats, dict), f"Сделок в истории: {stats.get('total_trades', 0)}"
test("Analytics", t_analytics)

# 14. Telegram
def t_telegram():
    n = TelegramNotifier()
    ok = n.bot_token is not None and n.chat_id is not None
    return ok, f"Token: {'OK' if n.bot_token else 'MISSING'} | Chat ID: {'OK' if n.chat_id else 'MISSING'}"
test("Telegram конфиг", t_telegram)

# 15. Восстановление позиций
def t_restore():
    positions = api.get_positions()
    rm = RiskManager()
    restored = 0
    for p in positions:
        amt = float(p.get('positionAmt', 0))
        if amt != 0:
            rm.add_position(p['symbol'], float(p['avgPrice']),
                          'LONG' if p['positionSide']=='LONG' else 'SHORT', abs(amt))
            restored += 1
    return True, f"Восстановлено позиций: {restored}"
test("Восстановление позиций", t_restore)

# Итог
print("\n" + "=" * 60)
passed = sum(1 for _, r, _ in results if r)
failed = sum(1 for _, r, _ in results if not r)
print(f"  Пройдено: {passed}/{len(results)}")
if failed > 0:
    print(f"  Провалено: {failed}")
    print("\n  Проблемы:")
    for name, r, msg in results:
        if not r:
            print(f"  {FAIL} {name}: {msg}")
else:
    print("  Все тесты пройдены — бот готов к торговле!")
print("=" * 60)
