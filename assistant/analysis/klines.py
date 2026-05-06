"""Общий хелпер для свечей — с поддержкой бэктеста."""
from ..core import exchange as ex

# Бэктест-режим: установи _BACKTEST_BUFFER = {(symbol,interval): [klines...]}
# и _BACKTEST_NOW_TS = int(timestamp), чтобы fetch_klines возвращал срез <= now_ts
_BACKTEST_BUFFER = {}
_BACKTEST_NOW_TS = None


def set_backtest(buffer, now_ts):
    global _BACKTEST_BUFFER, _BACKTEST_NOW_TS
    _BACKTEST_BUFFER = buffer or {}
    _BACKTEST_NOW_TS = now_ts


def clear_backtest():
    global _BACKTEST_BUFFER, _BACKTEST_NOW_TS
    _BACKTEST_BUFFER = {}
    _BACKTEST_NOW_TS = None


def _normalize(data):
    if data and isinstance(data[0], dict):
        data = sorted(data, key=lambda x: int(x.get("time", x.get("timestamp", 0))))
        return [{
            "t": int(k.get("time", k.get("timestamp", 0))),
            "o": float(k["open"]), "h": float(k["high"]),
            "l": float(k["low"]), "c": float(k["close"]),
            "v": float(k["volume"]),
        } for k in data]
    return [{
        "t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
        "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
    } for k in sorted(data, key=lambda x: int(x[0]))]


def fetch_klines(symbol, interval="15m", limit=100):
    # Бэктест-режим
    if _BACKTEST_NOW_TS is not None:
        buf = _BACKTEST_BUFFER.get((symbol, interval), [])
        if not buf:
            return []
        cut = [k for k in buf if k["t"] <= _BACKTEST_NOW_TS]
        return cut[-limit:]
    # Live режим
    r = ex.get_klines(symbol, interval, limit)
    if r.get("code") != 0:
        return []
    return _normalize(r.get("data", []))
