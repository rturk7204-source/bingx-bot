import sqlite3, os
from .config import DB_PATH
SCHEMA="""
CREATE TABLE IF NOT EXISTS candles(symbol TEXT,timeframe TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,PRIMARY KEY(symbol,timeframe,ts));
CREATE TABLE IF NOT EXISTS scans(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,symbol TEXT,score_short REAL,score_long REAL,factors_json TEXT,price REAL,ema20 REAL,rsi REAL,funding REAL,ls_ratio REAL);
CREATE INDEX IF NOT EXISTS idx_scans_ts ON scans(ts);
CREATE INDEX IF NOT EXISTS idx_scans_symbol ON scans(symbol);
CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY AUTOINCREMENT,scan_id INTEGER,ts INTEGER,symbol TEXT,direction TEXT,entry REAL,sl REAL,tp REAL,qty REAL,leverage INTEGER,rr REAL,expected_duration_h INTEGER,strategy_tags TEXT,status TEXT DEFAULT 'pending');
CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,signal_id INTEGER,symbol TEXT,direction TEXT,entry_ts INTEGER,entry_price REAL,exit_ts INTEGER,exit_price REAL,qty REAL,pnl REAL,fees REAL,breakeven_moved INTEGER DEFAULT 0,tag TEXT,source TEXT,notes TEXT);
CREATE INDEX IF NOT EXISTS idx_trades_entry_ts ON trades(entry_ts);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,type TEXT,severity TEXT,message TEXT,symbol_related TEXT);
CREATE TABLE IF NOT EXISTS onchain_events(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,type TEXT,amount_usd REAL,from_addr TEXT,to_addr TEXT,asset TEXT);
CREATE TABLE IF NOT EXISTS metrics_daily(date TEXT PRIMARY KEY,trades_count INTEGER,win_rate REAL,profit_factor REAL,pnl REAL,drawdown REAL,by_tag_json TEXT);
CREATE TABLE IF NOT EXISTS ml_predictions(id INTEGER PRIMARY KEY AUTOINCREMENT,ts INTEGER,symbol TEXT,model TEXT,prediction REAL,actual REAL,error REAL);
"""
def init_db():
    os.makedirs(os.path.dirname(DB_PATH),exist_ok=True)
    c=sqlite3.connect(DB_PATH); c.executescript(SCHEMA); c.commit(); c.close()
    print(f"DB initialized: {DB_PATH}")
def conn():
    c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; return c
if __name__=="__main__": init_db()
