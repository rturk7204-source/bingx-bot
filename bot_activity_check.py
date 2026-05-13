"""Что бот делал за сутки и какие монеты смотрит."""
import sys, sqlite3, time, json
sys.path.insert(0, "/root/bingx-bot")

DB = "/root/bingx-bot/assistant/data/trading.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
day_ago = int(time.time() * 1000) - 86400*1000

# Сколько вообще сканов было
n = c.execute("SELECT COUNT(*) FROM scans WHERE ts>?", (day_ago,)).fetchone()[0]
print(f"=== ВСЕГО СКАНОВ ЗА СУТКИ: {n} ===")

# Уникальные символы которые сканировались
syms = c.execute("SELECT symbol, COUNT(*) AS cnt FROM scans WHERE ts>? GROUP BY symbol ORDER BY cnt DESC LIMIT 50", (day_ago,)).fetchall()
print(f"\n=== СИМВОЛЫ В СКАНЕРЕ ({len(syms)} уникальных) ===")
for s in syms:
    has_h = " <-- H!" if "H" == s["symbol"].split("-")[0] else ""
    print(f"  {s['symbol']}: {s['cnt']}{has_h}")

# Сигналы за сутки
sigs = c.execute("SELECT * FROM signals WHERE ts>? ORDER BY ts DESC", (day_ago,)).fetchall()
print(f"\n=== СИГНАЛЫ ЗА СУТКИ: {len(sigs)} ===")
for r in sigs:
    t = time.strftime("%H:%M", time.localtime(int(r["ts"])/1000))
    print(f"  [{t}] {r['symbol']} {r['direction']} entry={r['entry']} SL={r['sl']} TP={r['tp']} RR={r['rr']} status={r['status']}")

# Сделки за сутки
trades = c.execute("SELECT * FROM trades WHERE entry_ts>? ORDER BY entry_ts DESC", (day_ago,)).fetchall()
print(f"\n=== СДЕЛКИ ЗА СУТКИ: {len(trades)} ===")
for r in trades:
    t = time.strftime("%H:%M", time.localtime(int(r["entry_ts"])/1000))
    print(f"  [{t}] {r['symbol']} {r['direction']} {r['entry_price']} -> {r['exit_price']} PnL={r['pnl']} source={r['source']}")
