"""Достать всё что бот знает про H-USDT за последний день."""
import sys, sqlite3, json, time
sys.path.insert(0, "/root/bingx-bot")

DB = "/root/bingx-bot/assistant/data/trading.db"
SYM_VARIANTS = ["H-USDT", "HUSDT", "H/USDT"]

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row
day_ago = int(time.time() * 1000) - 86400*1000

# 1. Сканы (все, не только signals — кандидаты которые крутились в сканере)
print("=== SCANS (последний день) ===")
for s_name in SYM_VARIANTS:
    rows = c.execute("SELECT id, ts, symbol, score_short, score_long, price, rsi, funding, factors_json FROM scans WHERE symbol=? AND ts>? ORDER BY ts DESC", (s_name, day_ago)).fetchall()
    if rows:
        print(f"\nsymbol={s_name}: {len(rows)} сканов")
        for r in rows[:20]:
            t = time.strftime("%H:%M", time.localtime(int(r["ts"])/1000)) if r["ts"] else "?"
            f = ""
            if r["factors_json"]:
                try: f = json.dumps(json.loads(r["factors_json"]), ensure_ascii=False)[:150]
                except: f = str(r["factors_json"])[:150]
            print(f"  [{t}] sL={r['score_short']:.1f} sLong={r['score_long']:.1f} px={r['price']} RSI={r['rsi']} fund={r['funding']}")
            if f: print(f"      factors: {f}")

# 2. Signals (отобранные сигналы)
print("\n=== SIGNALS (последний день) ===")
for s_name in SYM_VARIANTS:
    rows = c.execute("SELECT * FROM signals WHERE symbol=? AND ts>? ORDER BY ts DESC", (s_name, day_ago)).fetchall()
    if rows:
        print(f"\nsymbol={s_name}: {len(rows)}")
        for r in rows:
            t = time.strftime("%H:%M", time.localtime(int(r["ts"])/1000)) if r["ts"] else "?"
            print(f"  [{t}] {r['direction']} entry={r['entry']} SL={r['sl']} TP={r['tp']} RR={r['rr']} tags={r['strategy_tags']} status={r['status']}")

# 3. Trades
print("\n=== TRADES (последний день) ===")
for s_name in SYM_VARIANTS:
    rows = c.execute("SELECT * FROM trades WHERE symbol=? AND entry_ts>? ORDER BY entry_ts DESC", (s_name, day_ago)).fetchall()
    if rows:
        print(f"\nsymbol={s_name}: {len(rows)}")
        for r in rows:
            t_in = time.strftime("%H:%M", time.localtime(int(r["entry_ts"])/1000)) if r["entry_ts"] else "?"
            t_out = time.strftime("%H:%M", time.localtime(int(r["exit_ts"])/1000)) if r["exit_ts"] else "open"
            print(f"  {r['direction']} entry={r['entry_price']} exit={r['exit_price']} qty={r['qty']} PnL={r['pnl']} fees={r['fees']} src={r['source']} tag={r['tag']}")
            print(f"    [{t_in} -> {t_out}] notes={r['notes']}")

# 4. Rejections — может бот не пускал H в whitelist
print("\n=== REJECTIONS (последний день) ===")
for s_name in SYM_VARIANTS:
    try:
        rows = c.execute("SELECT * FROM rejections WHERE symbol=? AND ts>? ORDER BY ts DESC LIMIT 20", (s_name, day_ago)).fetchall()
        if rows:
            print(f"\nsymbol={s_name}: {len(rows)}")
            for r in rows[:10]:
                t = time.strftime("%H:%M", time.localtime(int(r["ts"])/1000)) if r["ts"] else "?"
                # покажем все поля
                d = dict(r)
                print(f"  [{t}] {d}")
    except Exception as e:
        pass

# 5. Active_positions (вдруг бот её записал)
print("\n=== ACTIVE_POSITIONS ===")
rows = c.execute("SELECT * FROM active_positions").fetchall()
for r in rows:
    d = dict(r)
    if any(s in str(d.get("symbol","")) for s in ["H-", "HUSDT"]):
        print(f"  {d}")
print(f"  всего активных: {len(rows)}")
