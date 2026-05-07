"""Состояние авто-режима: вкл/выкл, блок до timestamp, история последних авто-сделок."""
import sqlite3, time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "trading.db"

def _conn():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS auto_state(
        k TEXT PRIMARY KEY, v TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS auto_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER, symbol TEXT, direction TEXT, pnl_r REAL, result TEXT
    )""")
    return c

def get(k, default=None):
    with _conn() as c:
        r = c.execute("SELECT v FROM auto_state WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

def set_(k, v):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO auto_state(k,v) VALUES(?,?)", (k, str(v)))

def is_enabled():
    return get("enabled", "0") == "1"

def is_blocked():
    until = int(get("blocked_until", "0") or 0)
    return until > int(time.time()), until

def enable():
    set_("enabled", "1")
    set_("blocked_until", "0")

def disable():
    set_("enabled", "0")

def block_24h(reason="4 убытка подряд"):
    until = int(time.time()) + 24*3600
    set_("blocked_until", str(until))
    set_("block_reason", reason)
    set_("enabled", "0")
    return until

def log_close(symbol, direction, pnl_r, result):
    with _conn() as c:
        c.execute("INSERT INTO auto_history(ts,symbol,direction,pnl_r,result) VALUES(?,?,?,?,?)",
                  (int(time.time()), symbol, direction, float(pnl_r), result))

def last_n_results(n=2):
    with _conn() as c:
        r = c.execute("SELECT pnl_r FROM auto_history WHERE result!=? ORDER BY id DESC LIMIT ?", ("OPEN", n)).fetchall()
        return [x[0] for x in r]

def count_open_auto(active_dict):
    return sum(1 for p in active_dict.values() if p.get("auto"))

def status_text():
    en = is_enabled()
    blk, until = is_blocked()
    parts = [f"Авто: {'ВКЛ' if en else 'ВЫКЛ'}"]
    if blk:
        left = (until - int(time.time())) // 60
        parts.append(f"БЛОК {left}мин ({get('block_reason','')})")
    parts.append(f"сегодня: {count_today_auto()}/4")
    hist = last_n_results(5)
    if hist:
        parts.append(f"посл. сделки R: {', '.join(f'{x:+.2f}' for x in hist)}")
    return " | ".join(parts)

def log_open(symbol, direction):
    """Пишем в auto_history событие открытия (result='OPEN', pnl_r=0)."""
    with _conn() as c:
        c.execute("INSERT INTO auto_history(ts,symbol,direction,pnl_r,result) VALUES(?,?,?,?,?)",
                  (int(time.time()), symbol, direction, 0.0, "OPEN"))

def count_today_auto():
    """Сколько AUTO-сделок ОТКРЫТО за текущий UTC-день (по записям OPEN)."""
    import datetime
    now = datetime.datetime.utcnow()
    day_start = int(datetime.datetime(now.year, now.month, now.day).timestamp())
    with _conn() as c:
        r = c.execute("SELECT COUNT(*) FROM auto_history WHERE ts>=? AND result=?", (day_start, "OPEN")).fetchone()
        return r[0] if r else 0
