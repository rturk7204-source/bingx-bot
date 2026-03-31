import json
import os
import time
from datetime import datetime, timezone

class RLLogger:
    """Логирует state + action + reward для будущего обучения RL"""
    
    def __init__(self):
        self.log_file = "/root/bingx-bot/rl_states.json"
        self.pending = {}  # symbol -> state (ждём reward после закрытия)
    
    def log_state(self, symbol, state, action):
        """
        Логируем состояние при входе.
        state: dict с индикаторами
        action: "BUY" / "SELL"
        """
        entry = {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
            "action": action,
            "reward": None  # заполним при закрытии
        }
        self.pending[symbol] = entry
    
    def log_reward(self, symbol, pnl_pct):
        """Записываем reward после закрытия позиции"""
        if symbol not in self.pending:
            return
        
        entry = self.pending.pop(symbol)
        # Reward: профит = +1*pnl, лосс = -2*abs(pnl) (штрафуем потери)
        if pnl_pct >= 0:
            entry["reward"] = round(pnl_pct, 4)
        else:
            entry["reward"] = round(pnl_pct * 2, 4)
        
        # Дописываем в файл
        try:
            data = []
            if os.path.exists(self.log_file):
                with open(self.log_file, "r") as f:
                    data = json.load(f)
            data.append(entry)
            with open(self.log_file, "w") as f:
                json.dump(data, f, indent=1)
            print(f"[RL] {symbol}: state записан (action={entry['action']}, reward={entry['reward']})")
        except Exception as e:
            print(f"[RL] save error: {e}")
    
    def get_stats(self):
        """Сколько записей собрано"""
        try:
            if os.path.exists(self.log_file):
                with open(self.log_file) as f:
                    data = json.load(f)
                with_reward = sum(1 for d in data if d.get("reward") is not None)
                return {"total": len(data), "with_reward": with_reward}
        except:
            pass
        return {"total": 0, "with_reward": 0}
