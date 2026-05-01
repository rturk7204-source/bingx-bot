"""
auto_blacklist.py — Автоматическая блокировка пар с низким WR.

Исправления аудита v2.0:
- Убран мусор (root@srv...) из конца файла
- Конфигурируемые пути через env
- check_and_update() интегрирован в bot.py → _register_loss()
- Добавлено логирование
"""

import json
import os
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TRADES_FILE = os.getenv("TRADES_LOG_FILE", "/root/bingx-bot/trades.json")
BLACKLIST_FILE = os.getenv("BLACKLIST_FILE", "/root/bingx-bot/blacklist.json")


class AutoBlacklist:
    """
    Автоматически отключает пары с WR < 40% за последние 20 сделок.
    Блокировка на 48 часов, потом пара снова доступна.
    """

    def __init__(self, trades_file=None, blacklist_file=None):
        self.trades_file = trades_file or TRADES_FILE
        self.blacklist_file = blacklist_file or BLACKLIST_FILE
        self.min_trades = 20
        self.min_wr = 40.0
        self.block_hours = 48
        self.blacklist = self._load_blacklist()

    def _load_blacklist(self):
        """Загружает блэклист из файла."""
        try:
            if os.path.exists(self.blacklist_file):
                with open(self.blacklist_file) as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[BLACKLIST] ошибка загрузки: {e}")
        return {}

    def _save_blacklist(self):
        """Сохраняет блэклист в файл."""
        try:
            os.makedirs(os.path.dirname(self.blacklist_file), exist_ok=True)
            with open(self.blacklist_file, "w") as f:
                json.dump(self.blacklist, f, indent=2)
        except Exception as e:
            logger.error(f"[BLACKLIST] ошибка сохранения: {e}")

    def is_blocked(self, symbol):
        """Проверяет заблокирована ли пара."""
        if symbol not in self.blacklist:
            return False
        blocked_until = self.blacklist[symbol].get("until", 0)
        now = time.time()
        if now >= blocked_until:
            del self.blacklist[symbol]
            self._save_blacklist()
            logger.info(f"[BLACKLIST] {symbol}: разблокирован (48ч истекли)")
            return False
        hours_left = (blocked_until - now) / 3600
        logger.info(f"[BLACKLIST] {symbol}: заблокирован ещё {hours_left:.1f}ч")
        return True

    def check_and_update(self, symbol):
        """
        Проверяет WR пары и блокирует если нужно.
        Вызывается из bot.py → _register_loss() после каждого убытка.
        """
        if self.is_blocked(symbol):
            return True

        try:
            if not os.path.exists(self.trades_file):
                return False
            with open(self.trades_file) as f:
                all_trades = json.load(f)
        except Exception as e:
            logger.warning(f"[BLACKLIST] ошибка чтения trades: {e}")
            return False

        # Фильтруем сделки по паре с PnL
        pair_trades = [
            t for t in all_trades
            if t.get("symbol") == symbol and t.get("pnl") is not None
        ]

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
                "blocked_at": datetime.now(timezone.utc).isoformat(),
            }
            self._save_blacklist()
            logger.warning(
                f"[BLACKLIST] {symbol}: ЗАБЛОКИРОВАН на {self.block_hours}ч — "
                f"WR={wr:.1f}% < {self.min_wr}%"
            )
            return True

        return False
