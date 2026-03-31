#!/usr/bin/env python3
"""
Сборщик исторических данных в SQLite.
Запускается по cron каждый час.
Сохраняет 1h свечи для всех активных пар.
"""
import os, sys, sqlite3, time
from datetime import datetime

sys.path.insert(0, "/root/bingx-bot")
from bingx_api import BingXAPI
from dotenv import load_dotenv
load_dotenv("/root/bingx-bot/.env")

DB_PATH = "/root/bingx-bot/market_data.db"
SYMBOLS = ["ETH-USDT", "SUI-USDT", "DOGE-USDT", "ADA-USDT", "XRP-USDT", "OP-USDT", "LINK-USDT", "FET-USDT", "WLD-USDT", "BTC-USDT"]

api = BingXAPI(
    api_key=os.getenv("BINGX_API_KEY"),
    secret_key=os.getenv("BINGX_SECRET_KEY")
)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS klines_1h (
            symbol TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            collected_at TEXT,
            PRIMARY KEY (symbol, timestamp)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_klines_sym_ts
        ON klines_1h (symbol, timestamp)
    """)
    conn.commit()
    return conn

def collect_symbol(conn, symbol):
    """Загружаем последние 100 свечей 1h и вставляем новые"""
    try:
        raw = api.get_klines(symbol, interval="1h", limit=100)
        if not raw or raw.get("code") != 0:
            print(f"[DC] {symbol}: ошибка API")
            return 0

        klines = raw["data"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        inserted = 0

        for k in klines:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO klines_1h (symbol, timestamp, open, high, low, close, volume, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (symbol, int(k["time"]), float(k["open"]), float(k["high"]),
                     float(k["low"]), float(k["close"]), float(k["volume"]), now)
                )
                inserted += conn.total_changes
            except sqlite3.IntegrityError:
                pass

        conn.commit()

        # Считаем общее количество записей для этого символа
        count = conn.execute("SELECT COUNT(*) FROM klines_1h WHERE symbol=?", (symbol,)).fetchone()[0]
        print(f"[DC] {symbol}: +{len(klines)} свечей проверено, всего в БД: {count}")
        return len(klines)

    except Exception as e:
        print(f"[DC] {symbol} ERROR: {e}")
        return 0

def main():
    print(f"[DC] === Сбор данных {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    conn = init_db()

    total = 0
    for sym in SYMBOLS:
        total += collect_symbol(conn, sym)

    # Первый запуск — загружаем максимум истории
    for sym in SYMBOLS:
        count = conn.execute("SELECT COUNT(*) FROM klines_1h WHERE symbol=?", (sym,)).fetchone()[0]
        if count < 500:
            print(f"[DC] {sym}: мало данных ({count}), загружаем максимум...")
            raw = api.get_klines(sym, interval="1h", limit=1440)
            if raw and raw.get("code") == 0:
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                for k in raw["data"]:
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO klines_1h VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (sym, int(k["time"]), float(k["open"]), float(k["high"]),
                             float(k["low"]), float(k["close"]), float(k["volume"]), now)
                        )
                    except:
                        pass
                conn.commit()
                new_count = conn.execute("SELECT COUNT(*) FROM klines_1h WHERE symbol=?", (sym,)).fetchone()[0]
                print(f"[DC] {sym}: загружено, теперь {new_count} записей")

    # Статистика
    print(f"\n[DC] === Итого в БД ===")
    for sym in SYMBOLS:
        count = conn.execute("SELECT COUNT(*) FROM klines_1h WHERE symbol=?", (sym,)).fetchone()[0]
        oldest = conn.execute("SELECT MIN(timestamp) FROM klines_1h WHERE symbol=?", (sym,)).fetchone()[0]
        newest = conn.execute("SELECT MAX(timestamp) FROM klines_1h WHERE symbol=?", (sym,)).fetchone()[0]
        if oldest and newest:
            days = (newest - oldest) / 1000 / 86400
            print(f"  {sym}: {count} свечей, {days:.1f} дней")
        else:
            print(f"  {sym}: {count} свечей")

    db_size = os.path.getsize(DB_PATH) / 1024 / 1024
    print(f"\n[DC] Размер БД: {db_size:.2f} MB")
    conn.close()

if __name__ == "__main__":
    main()
