import json
import os
import time
from datetime import datetime, timezone

class AutoBlacklist:
    """
    Автоматически отключает пары с WR < 40% за последние 20 сделок.
    Блокировка на 48 часов, потом пара снова доступна.
    """
    
    def __init__(self, trades_file="/root/bingx-bot/trades.json"):
        self.trades_file = trades_file
        self.blacklist_file = "/root/bingx-bot/blacklist.json"
        self.min_trades = 20
        self.min_wr = 40.0
        self.block_hours = 48
        self.blacklist = self._load_blacklist()
    
    def _load_blacklist(self):
        try:
            if os.path.exists(self.blacklist_file):
                with open(self.blacklist_file) as f:
                    return json.load(f)
        except:
            pass
        return {}
    
    def _save_blacklist(self):
        try:
            with open(self.blacklist_file, "w") as f:
                json.dump(self.blacklist, f, indent=2)
        except:
            pass
    
    def is_blocked(self, symbol):
        """Проверяет заблокирована ли пара"""
        if symbol not in self.blacklist:
            return False
        blocked_until = self.blacklist[symbol].get("until", 0)
        now = time.time()
        if now >= blocked_until:
            # Разблокируем
            del self.blacklist[symbol]
            self._save_blacklist()
            print(f"[BLACKLIST] {symbol}: разблокирован (48ч истекли)")
            return False
        hours_left = (blocked_until - now) / 3600
        return True
    
    def check_and_update(self, symbol):
        """Проверяет WR пары и блокирует если нужно"""
        if self.is_blocked(symbol):
            hours_left = (self.blacklist[symbol]["until"] - time.time()) / 3600
            print(f"[BLACKLIST] {symbol}: заблокирован ещё {hours_left:.1f}ч")
            return True
        
        try:
            with open(self.trades_file) as f:
                all_trades = json.load(f)
        except:
            return False
        
        # Фильтруем сделки по паре с PnL
        pair_trades = [t for t in all_trades if t.get("symbol") == symbol and t.get("pnl") is not None]
        
        if len(pair_trades) < self.min_trades:
            return False
        
        # Берём последние N сделок
        recent = pair_trades[-self.min_trades:]
        wins = sum(1 for t in recent if float(t.get("pnl", 0)) > 0)
        wr = wins / len(recent) * 100
        
        if wr < self.min_wr:
            blocked_until = time.time() + self.block_hours * 3600
            self.blacklist[symbol] = {
                "until": blocked_until,
                "reason": f"WR={wr:.1f}% за последние {len(recent)} сделок",
                "blocked_at": datetime.now(timezone.utc).isoformat()
            }
            self._save_blacklist()
            print(f"[BLACKLIST] {symbol}: ЗАБЛОКИРОВАН на {self.block_hours}ч — WR={wr:.1f}% < {self.min_wr}%")
            return True
        
        return False
