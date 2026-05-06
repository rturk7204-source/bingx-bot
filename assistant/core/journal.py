"""Журнал сделок и активные позиции в SQLite."""
import sqlite3, time, json, os

DB = os.path.join(os.path.dirname(__file__), "..", "data", "trading.db")


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_active_table():
    """Таблица для in-flight позиций (для восстановления после рестарта)."""
    with conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS active_positions (
                symbol TEXT PRIMARY KEY,
                direction TEXT,
                entry REAL,
                sl REAL,
                tp REAL,
                be REAL,
                qty REAL,
                sl_order_id TEXT,
                be_done INTEGER DEFAULT 0,
                chat_id INTEGER,
                opened_ts INTEGER,
                trade_id INTEGER,
                setup_tag TEXT
            )
        """)


def save_active(symbol, pos):
    """Сохранить/обновить активную позицию."""
    with conn() as c:
        c.execute("""
            INSERT OR REPLACE INTO active_positions
            (symbol, direction, entry, sl, tp, be, qty, sl_order_id, be_done, chat_id, opened_ts, trade_id, setup_tag, auto)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            symbol, pos["direction"], pos["entry"], pos["sl"], pos["tp"],
            pos["be"], pos["qty"], pos.get("sl_order_id"),
            int(pos.get("be_done", False)), pos["chat_id"],
            pos.get("opened_ts", int(time.time())),
            pos.get("trade_id"), pos.get("setup_tag", ""),
            int(pos.get("auto", False))
        ))


def update_active(symbol, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE active_positions SET {cols} WHERE symbol=?",
                  (*fields.values(), symbol))


def remove_active(symbol):
    with conn() as c:
        c.execute("DELETE FROM active_positions WHERE symbol=?", (symbol,))


def load_all_active():
    with conn() as c:
        rows = c.execute("SELECT * FROM active_positions").fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        d["be_done"] = bool(d["be_done"])
        d["auto"] = bool(d.get("auto") or 0)
        out[d["symbol"]] = d
    return out


def journal_open(symbol, direction, entry, qty, setup_tag, source="assistant",
                 sl=None, tp=None, score=None, adj_rr=None, ch24=None, atr_pct=None,
                 quality_info=None):
    """Создаёт запись trade. Возвращает trade_id."""
    # гарантируем что колонки существуют (миграция)
    with conn() as c:
        for col, typ in [("sl","REAL"),("tp","REAL"),("score","INTEGER"),
                         ("adj_rr","REAL"),("ch24","REAL"),("atr_pct","REAL"),
                         ("quality_info","TEXT")]:
            try: c.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
            except: pass
        cur = c.execute("""
            INSERT INTO trades (symbol, direction, entry_ts, entry_price, qty, tag, source,
                                breakeven_moved, sl, tp, score, adj_rr, ch24, atr_pct, quality_info)
            VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)
        """, (symbol, direction, int(time.time()), entry, qty, setup_tag, source,
              sl, tp, score, adj_rr, ch24, atr_pct,
              json.dumps(quality_info) if quality_info else None))
        return cur.lastrowid


def journal_close(trade_id, exit_price, pnl, fees, be_moved, notes=""):
    with conn() as c:
        c.execute("""
            UPDATE trades SET exit_ts=?, exit_price=?, pnl=?, fees=?, breakeven_moved=?, notes=?
            WHERE id=?
        """, (int(time.time()), exit_price, pnl, fees, int(be_moved), notes, trade_id))


def journal_be_moved(trade_id):
    with conn() as c:
        c.execute("UPDATE trades SET breakeven_moved=1 WHERE id=?", (trade_id,))


def get_stats():
    """Win rate, средний R, разбивка по setup_tag."""
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM trades WHERE exit_ts IS NOT NULL
        """).fetchall()
    if not rows:
        return None
    total = len(rows)
    wins = sum(1 for r in rows if r["pnl"] and r["pnl"] > 0)
    losses = total - wins
    pnl_sum = sum(r["pnl"] or 0 for r in rows)
    fees_sum = sum(r["fees"] or 0 for r in rows)
    avg_pnl = pnl_sum / total if total else 0

    # по сетапам
    by_setup = {}
    for r in rows:
        tag = r["tag"] or "no_tag"
        d = by_setup.setdefault(tag, {"n": 0, "wins": 0, "pnl": 0})
        d["n"] += 1
        if r["pnl"] and r["pnl"] > 0:
            d["wins"] += 1
        d["pnl"] += r["pnl"] or 0

    return {
        "total": total, "wins": wins, "losses": losses,
        "win_rate": wins / total * 100 if total else 0,
        "pnl_total": pnl_sum, "fees_total": fees_sum,
        "avg_pnl": avg_pnl, "by_setup": by_setup,
    }


def get_recent(n=10):
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM trades ORDER BY id DESC LIMIT ?
        """, (n,)).fetchall()
    return [dict(r) for r in rows]


def init_rejections_table():
    with conn() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS rejections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT,
            reason TEXT NOT NULL,
            score INTEGER,
            entry REAL,
            sl REAL,
            tp REAL,
            info TEXT
        )
        """)


def log_rejection(symbol, direction, reason, score=None, entry=None, sl=None, tp=None, info=None):
    import time, json
    with conn() as c:
        c.execute(
            "INSERT INTO rejections (ts, symbol, direction, reason, score, entry, sl, tp, info) VALUES (?,?,?,?,?,?,?,?,?)",
            (int(time.time()), symbol, direction, reason, score, entry, sl, tp, json.dumps(info) if info else None)
        )


def rejection_stats(hours=24):
    import time
    cutoff = int(time.time()) - hours*3600
    with conn() as c:
        rows = c.execute(
            "SELECT reason, COUNT(*) as n FROM rejections WHERE ts >= ? GROUP BY reason ORDER BY n DESC",
            (cutoff,)
        ).fetchall()
    return [{"reason": r["reason"], "count": r["n"]} for r in rows]


def get_stats_extended():
    """Расширенная статистика: средняя длительность, % дошедших до +1R."""
    with conn() as c:
        rows = c.execute("""
            SELECT * FROM trades WHERE exit_ts IS NOT NULL
        """).fetchall()
    if not rows:
        return None

    rows = [dict(r) for r in rows]
    by_tag = {}
    for r in rows:
        tag = r.get("tag") or "no_tag"
        d = by_tag.setdefault(tag, {
            "n":0, "wins":0, "pnl":0, "durations":[], "be_count":0
        })
        d["n"] += 1
        if (r.get("pnl") or 0) > 0:
            d["wins"] += 1
        d["pnl"] += r.get("pnl") or 0
        if r.get("entry_ts") and r.get("exit_ts"):
            d["durations"].append(r["exit_ts"] - r["entry_ts"])
        if r.get("breakeven_moved"):
            d["be_count"] += 1
    out = {}
    for tag, d in by_tag.items():
        avg_dur_min = (sum(d["durations"]) / len(d["durations"]) / 60) if d["durations"] else 0
        out[tag] = {
            "n": d["n"],
            "wr": d["wins"] / d["n"] * 100 if d["n"] else 0,
            "pnl": d["pnl"],
            "avg_duration_min": round(avg_dur_min, 1),
            "reached_1R_pct": round(d["be_count"] / d["n"] * 100, 1) if d["n"] else 0,
        }
    return out

# инициализация таблиц при импорте (если их нет)
try:
    with conn() as _c:
        _c.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, direction TEXT,
            entry_ts INTEGER, exit_ts INTEGER,
            entry_price REAL, exit_price REAL,
            qty REAL, pnl REAL, fees REAL,
            breakeven_moved INTEGER DEFAULT 0,
            tag TEXT, source TEXT, notes TEXT
        )""")
except Exception as _e:
    pass
