"""tests/test_hedge_precision.py — Block 8.6: hedge precision fix.

Проверяет ключевую инвариантность:
  - open_short_perp_qty(qty) посылает в API именно qty (округлённый до 4 знаков),
    НЕ зависит от mark_price, в отличие от старой open_short_perp(notional).
  - При spot_qty=3196.8 (например 160 USDT - fee на цене $0.05) ордер на перп
    идёт на 3196.8, а не на 160/mark = 3200 (старый баг).

Тестирование через мокирование _post_with_retry и get_mark_price.
"""
from tests._bootstrap import TEST_BOT_DIR  # noqa: F401

import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_open_short_perp_qty():
    """
    Извлекает функцию open_short_perp_qty из arb_bot.py без импорта всего модуля.
    Создаём stub-окружение с минимумом необходимых символов.
    """
    src = (REPO_ROOT / "arb_bot.py").read_text()
    # Извлекаем только нужный кусок: from def open_short_perp_qty до следующей def
    start = src.index("def open_short_perp_qty")
    # Конец = начало следующей def на той же индентации
    after = src[start + 1:]
    end_rel = after.index("\ndef ")
    src_fn = src[start:start + 1 + end_rel]

    # Минимальное окружение
    captured = {"posts": []}

    def fake_post(path, params, attempts=3, timeout=30):
        captured["posts"].append({"path": path, "params": dict(params)})
        return {"code": 0, "msg": "OK", "data": {"orderId": "TEST123"}}

    def fake_log_info(msg):
        captured.setdefault("logs", []).append(msg)

    def fake_mark_price():
        return captured.get("_mark_price", 1.0)

    log_stub = types.SimpleNamespace(info=fake_log_info, error=fake_log_info,
                                     warning=fake_log_info)

    ns = {
        "_post_with_retry": fake_post,
        "get_mark_price": fake_mark_price,
        "log": log_stub,
        "SYMBOL": "TEST-USDT",
    }
    exec(src_fn, ns)
    return ns["open_short_perp_qty"], captured, ns


# ============ ТЕСТЫ ============

def test_open_short_perp_qty_passes_exact_qty():
    """Базовая инвариантность: переданный qty уходит в API без изменений (до 4 знаков)."""
    fn, captured, _ = _load_open_short_perp_qty()
    captured["_mark_price"] = 0.05
    res = fn(3196.8)
    assert res["code"] == 0
    assert len(captured["posts"]) == 1
    sent_qty = float(captured["posts"][0]["params"]["quantity"])
    assert abs(sent_qty - 3196.8) < 1e-6, f"expected 3196.8, got {sent_qty}"


def test_open_short_perp_qty_rounds_to_4_decimals():
    """qty с 6 знаками после запятой → округление до 4."""
    fn, captured, _ = _load_open_short_perp_qty()
    captured["_mark_price"] = 1.0
    fn(123.456789)
    sent_qty = float(captured["posts"][0]["params"]["quantity"])
    assert sent_qty == 123.4568, f"expected 123.4568, got {sent_qty}"


def test_open_short_perp_qty_independent_of_mark_price():
    """
    Главный инвариант фикса: тот же qty при разных mark_price посылает ОДИН и тот же qty.
    Старый баг: qty = notional/mark_price → разный qty при разных mark_price.
    """
    fn1, cap1, _ = _load_open_short_perp_qty()
    cap1["_mark_price"] = 0.05
    fn1(3196.8)
    qty1 = float(cap1["posts"][0]["params"]["quantity"])

    fn2, cap2, _ = _load_open_short_perp_qty()
    cap2["_mark_price"] = 0.04999  # другая mark price
    fn2(3196.8)
    qty2 = float(cap2["posts"][0]["params"]["quantity"])

    assert qty1 == qty2, f"qty must not depend on mark_price: {qty1} vs {qty2}"


def test_open_short_perp_qty_zero_qty_rejected():
    """qty<=0 → return error без вызова API."""
    fn, captured, _ = _load_open_short_perp_qty()
    res = fn(0)
    assert res["code"] == -1, res
    assert captured["posts"] == [], "API не должен вызываться при qty=0"


def test_open_short_perp_qty_negative_qty_rejected():
    fn, captured, _ = _load_open_short_perp_qty()
    res = fn(-100)
    assert res["code"] == -1
    assert captured["posts"] == []


def test_open_short_perp_qty_sends_correct_side_and_position():
    """SELL + positionSide=SHORT — это критично для дельта-нейтрала."""
    fn, captured, _ = _load_open_short_perp_qty()
    fn(100.0)
    params = captured["posts"][0]["params"]
    assert params["side"] == "SELL"
    assert params["positionSide"] == "SHORT"
    assert params["type"] == "MARKET"
    assert params["symbol"] == "TEST-USDT"


# ============ КОНТРАСТНЫЙ ТЕСТ: показывает что фикс ИСПРАВЛЯЕТ ============

def test_old_bug_reproduction_shows_drift():
    """
    Документирует старый баг для истории.
    Spot покупка $160 на цене $0.05 (с fee 0.1%) → spot_qty = 159.84/0.05 = 3196.8
    Старая логика: perp_qty = 160/mark_price; если mark = 0.04985 (mark часто < spot
    при положительном funding) → perp_qty = 160/0.04985 = 3209.6
    Расхождение: 3209.6 - 3196.8 = 12.8 токена = 0.4% дрифт.

    Новая логика: perp_qty = spot_qty = 3196.8 → 0% дрифт.
    """
    spot_usdt = 160.0
    spot_price = 0.05
    spot_fee = 0.001
    mark_price = 0.04985  # mark < spot при positive funding

    # Реальный spot_qty (что прилетает на кошелёк):
    spot_qty = (spot_usdt * (1 - spot_fee)) / spot_price  # 3196.8

    # Старая (баговая) логика open_short_perp(notional):
    old_perp_qty = round(spot_usdt / mark_price, 4)  # 3209.6...

    # Новая логика open_short_perp_qty(spot_qty):
    new_perp_qty = round(spot_qty, 4)  # 3196.8

    old_drift_pct = abs(old_perp_qty - spot_qty) / spot_qty * 100
    new_drift_pct = abs(new_perp_qty - spot_qty) / spot_qty * 100

    assert old_drift_pct > 0.3, f"old bug should show >0.3% drift, got {old_drift_pct:.3f}%"
    assert new_drift_pct < 0.001, f"new fix must be ~0% drift, got {new_drift_pct:.6f}%"


# ============ ВСЕ 7 БОТОВ — ИДЕНТИЧНЫ ============

def test_all_7_bots_have_open_short_perp_qty():
    """Регрессия: все arb_botN.py должны иметь open_short_perp_qty (не пропустили патч)."""
    for fname in ["arb_bot.py", "arb_bot2.py", "arb_bot3.py", "arb_bot4.py",
                  "arb_bot5.py", "arb_bot6.py", "arb_bot7.py"]:
        src = (REPO_ROOT / fname).read_text()
        assert "def open_short_perp_qty" in src, f"{fname}: missing open_short_perp_qty"
        assert "open_short_perp_qty(spot_qty)" in src, \
            f"{fname}: should call open_short_perp_qty(spot_qty), not (SPOT_BUDGET)"


def test_all_7_bots_no_longer_call_open_short_perp_with_spot_budget():
    """Ни один бот не должен вызывать старую сигнатуру open_short_perp(SPOT_BUDGET)."""
    for fname in ["arb_bot.py", "arb_bot2.py", "arb_bot3.py", "arb_bot4.py",
                  "arb_bot5.py", "arb_bot6.py", "arb_bot7.py"]:
        src = (REPO_ROOT / fname).read_text()
        # Активный вызов open_short_perp(SPOT_BUDGET) НЕ должен встречаться
        assert "perp_res = open_short_perp(SPOT_BUDGET)" not in src, \
            f"{fname}: still calling old open_short_perp(SPOT_BUDGET)"


TESTS = [
    test_open_short_perp_qty_passes_exact_qty,
    test_open_short_perp_qty_rounds_to_4_decimals,
    test_open_short_perp_qty_independent_of_mark_price,
    test_open_short_perp_qty_zero_qty_rejected,
    test_open_short_perp_qty_negative_qty_rejected,
    test_open_short_perp_qty_sends_correct_side_and_position,
    test_old_bug_reproduction_shows_drift,
    test_all_7_bots_have_open_short_perp_qty,
    test_all_7_bots_no_longer_call_open_short_perp_with_spot_budget,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_hedge_precision] {len(TESTS)} passed")
