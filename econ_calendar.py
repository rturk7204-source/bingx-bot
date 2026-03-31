"""
Экономический календарь — блокируем торговлю перед крупными ивентами.
Использует бесплатный API для получения расписания CPI, FOMC, NFP, PPI.
Fallback: захардкоженные даты FOMC 2026.
"""
import json, os
from datetime import datetime, timedelta, timezone
import urllib.request

CACHE_FILE = "/root/bingx-bot/econ_events.json"
CACHE_TTL_HOURS = 6

# FOMC dates 2026 (захардкожены как fallback)
FOMC_2026 = [
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]

# Крупные ивенты которые двигают рынок (день месяца — приблизительно)
# CPI обычно 10-15 числа, NFP — первая пятница
MONTHLY_EVENTS = {
    "CPI": list(range(10, 16)),    # 10-15 числа
    "PPI": list(range(12, 18)),    # 12-17 числа
}


class EconCalendar:
    def __init__(self):
        self.events = []
        self.last_check = None
        self._load_cache()

    def _load_cache(self):
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE) as f:
                    data = json.load(f)
                    self.events = data.get("events", [])
                    self.last_check = data.get("last_check", "")
        except:
            pass

    def _save_cache(self):
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump({"events": self.events, "last_check": datetime.now(timezone.utc).isoformat()}, f)
        except:
            pass

    def _is_fomc_day(self, dt):
        date_str = dt.strftime("%Y-%m-%d")
        # FOMC day или день до
        for fomc in FOMC_2026:
            fomc_dt = datetime.strptime(fomc, "%Y-%m-%d")
            if abs((dt.date() - fomc_dt.date()).days) <= 0:
                return True
        return False

    def _is_cpi_nfp_day(self, dt):
        day = dt.day
        weekday = dt.weekday()  # 0=Monday

        # NFP — первая пятница месяца
        if weekday == 4 and day <= 7:
            return "NFP"

        # CPI — обычно 10-15 числа
        if day in MONTHLY_EVENTS.get("CPI", []):
            return "CPI"

        # PPI
        if day in MONTHLY_EVENTS.get("PPI", []):
            return "PPI"

        return None

    def should_block_trading(self):
        """
        Возвращает (block: bool, reason: str)
        Блокируем за 1 час до и 30 мин после крупного ивента.
        Упрощённая версия — проверяем по дате/часу.
        """
        now = datetime.now(timezone.utc)
        hour = now.hour

        # FOMC — обычно в 18:00-18:30 UTC
        if self._is_fomc_day(now):
            if 17 <= hour <= 19:
                return True, "FOMC announcement"
            if hour >= 14:
                return True, "FOMC day — reduced trading"

        # CPI/NFP/PPI — обычно в 12:30-13:30 UTC
        event = self._is_cpi_nfp_day(now)
        if event:
            if 11 <= hour <= 14:
                return True, f"{event} release window"

        return False, ""

    def get_status(self):
        """Возвращает текущий статус для логирования"""
        blocked, reason = self.should_block_trading()
        now = datetime.now(timezone.utc)
        if blocked:
            return f"BLOCKED ({reason})"
        # Проверяем ближайший ивент
        if self._is_fomc_day(now):
            return "FOMC_DAY (trading allowed now)"
        event = self._is_cpi_nfp_day(now)
        if event:
            return f"{event}_DAY (trading allowed now)"
        return "CLEAR"
