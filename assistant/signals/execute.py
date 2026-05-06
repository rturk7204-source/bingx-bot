"""Исполнение сделки: market-вход, SL, TP с учётом precision контракта."""
from ..core import exchange as ex

_CONTRACT_CACHE = {}


def get_contract(symbol):
    """Кэш параметров контракта (precision)."""
    if symbol in _CONTRACT_CACHE:
        return _CONTRACT_CACHE[symbol]
    r = ex.request("GET", "/openApi/swap/v2/quote/contracts", {})
    for c in r.get("data", []):
        _CONTRACT_CACHE[c["symbol"]] = c
    return _CONTRACT_CACHE.get(symbol, {})


def round_to(value, precision):
    """Округление вниз до нужного количества знаков."""
    if precision <= 0:
        return int(value)
    f = 10 ** precision
    return int(value * f) / f


def open_trade(plan):
    # защита от дублей: уже есть активная позиция по символу?
    try:
        from ..core import journal as _j
        sym_check = plan["symbol"]
        active = _j.load_all_active() or {}
        if sym_check in active:
            existing = active[sym_check]
            if existing.get("direction") != plan["direction"]:
                return {"error": f"уже есть {existing['direction']} по {sym_check} — закрой сначала"}
            return {"error": f"уже есть {plan['direction']} по {sym_check} — дубль"}
    except Exception:
        pass

    # depth-check: плита против нас в радиусе 0.3% от входа
    try:
        from ..core import exchange as _ex
        symbol = plan["symbol"]; direction = plan["direction"]
        entry = float(plan["entry"]); qty = float(plan["qty"])
        depth = _ex.get_depth(symbol, limit=20)
        d = depth.get("data") if isinstance(depth, dict) else depth
        bids = (d or {}).get("bids") or []
        asks = (d or {}).get("asks") or []
        radius = entry * 0.003
        wall_qty = 0.0
        if direction == "LONG":
            for row in asks[:20]:
                px, q = float(row[0]), float(row[1])
                if px <= entry + radius:
                    wall_qty += q
        else:
            for row in bids[:20]:
                px, q = float(row[0]), float(row[1])
                if px >= entry - radius:
                    wall_qty += q
        if False:  # depth check disabled
            return {"error": f"плита против нас {wall_qty:.0f} (наша {qty:.0f}, x{wall_qty/qty:.1f}) — отказ"}
    except Exception:
        pass

    sym = plan["symbol"]
    direction = plan["direction"]
    qty_raw = plan["qty"]
    sl_raw = plan["sl"]
    tp_raw = plan["tp"]
    lev = plan["leverage"]

    contract = get_contract(sym)
    if not contract:
        return {"ok": False, "stage": "contract", "msg": f"contract not found for {sym}"}

    qp = int(contract.get("quantityPrecision", 4))
    pp = int(contract.get("pricePrecision", 6))
    min_qty = float(contract.get("tradeMinQuantity", 0))
    min_usdt = float(contract.get("tradeMinUSDT", 2))

    qty = round_to(qty_raw, qp)
    sl = round_to(sl_raw, pp)
    tp = round_to(tp_raw, pp)

    if qty < min_qty:
        return {"ok": False, "stage": "qty",
                "msg": f"qty {qty} < min {min_qty} (precision {qp})"}
    notional = qty * plan["entry"]
    if notional < min_usdt:
        return {"ok": False, "stage": "qty",
                "msg": f"notional ${notional:.2f} < min ${min_usdt}"}

    pos_side = "LONG" if direction == "LONG" else "SHORT"
    side = "BUY" if direction == "LONG" else "SELL"
    sl_side = "SELL" if direction == "LONG" else "BUY"

    # 1) Плечо
    ex.set_margin_mode(sym, 'ISOLATED')  # изолированная маржа
    r_lev = ex.set_leverage(sym, pos_side, lev)
    if r_lev.get("code") != 0:
        return {"ok": False, "stage": "leverage", "msg": r_lev}

    # 2) Маркет-вход
    r_open = ex.place_order(
        symbol=sym, side=side, positionSide=pos_side,
        type="MARKET", quantity=qty
    )
    if r_open.get("code") != 0:
        return {"ok": False, "stage": "entry", "msg": r_open,
                "rounded": {"qty": qty, "sl": sl, "tp": tp}}
    order_id = r_open.get("data", {}).get("order", {}).get("orderId")

    # 3) Stop Loss
    r_sl = ex.place_order(
        symbol=sym, side=sl_side, positionSide=pos_side,
        type="STOP_MARKET", stopPrice=sl, quantity=qty,
        workingType="MARK_PRICE"
    )
    sl_ok = r_sl.get("code") == 0

    # 4) Take Profit
    r_tp = ex.place_order(
        symbol=sym, side=sl_side, positionSide=pos_side,
        type="TAKE_PROFIT_MARKET", stopPrice=tp, quantity=qty,
        workingType="MARK_PRICE"
    )
    tp_ok = r_tp.get("code") == 0

    return {
        "ok": True,
        "entry_order_id": order_id,
        "sl_placed": sl_ok, "sl_msg": r_sl,
        "tp_placed": tp_ok, "tp_msg": r_tp,
        "rounded": {"qty": qty, "sl": sl, "tp": tp},
        "plan": plan,
    }
