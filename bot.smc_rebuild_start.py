import os
import time
from datetime import datetime
from dotenv import load_dotenv
from bingx_api import BingXAPI
from strategy_ml import MLStrategy
from analytics import Analytics
from risk_manager import RiskManager
from telegram_notifier import TelegramNotifier
from telegram_commander import TelegramCommander
from kelly import KellyCriterion
from econ_calendar import EconCalendar
import sqlite3
from liquidation_map import LiquidationMap
from volume_profile import VolumeProfile
from rl_logger import RLLogger
from auto_blacklist import AutoBlacklist
from pairs_trading import PairsTrader

load_dotenv()

class TradingBot:
    def __init__(self):
        self.api = BingXAPI(
            api_key=os.getenv('BINGX_API_KEY'),
            secret_key=os.getenv('BINGX_SECRET_KEY')
        )
        self.strategy = MLStrategy()
        self.analytics = Analytics()
        self.risk_manager = RiskManager(stop_loss_pct=3.0, take_profit_pct=999.0, trailing_pct=1.0)
        self.notifier = TelegramNotifier()
        self.commander = TelegramCommander(self)
        self.kelly = KellyCriterion(max_position=70.0, min_position=20.0)
        self.econ_calendar = EconCalendar()
        self.liq_map = LiquidationMap()
        self.volume_profile = VolumeProfile(self.api)
        self.rl_logger = RLLogger()
        self.auto_blacklist = AutoBlacklist()
        self.pairs_trader = PairsTrader(self.api, self.notifier, self.analytics)

        self.trading_enabled = True
        self.daily_loss = 0.0
        self.last_trade_time = {}
        self.max_same_direction = 2
        self.pyramid_positions = {}
        self.max_budget = 500.0
        self.peak_balance = 0.0
        self.drawdown_half = 10.0
        self.drawdown_stop = 15.0
        self.max_margin_pct = 40.0
        self.averaging_enabled = True
        self.averaging_drop_pct = 3.5
        self.averaging_max_times = 1
        self.averaging_size_mult = 1.5
        self.averaging_counts = {}
        self.symbol_performance = {}
        self.perf_check_day = datetime.now().day
        self.daily_loss_limit = 30.0
        self.consecutive_stops = 0
        self.cooldown_until = None
        self.cooldown_hours = 3
        self.max_consecutive_stops = 3

        # ПАТЧ 23: Equity curve trading
        self.equity_history = []
        self.equity_reduction = False
        self.last_reset_day = datetime.now().day

        self.symbols = ['ETH-USDT', 'SUI-USDT', 'DOGE-USDT', 'ADA-USDT', 'XRP-USDT', 'OP-USDT', 'LINK-USDT', 'FET-USDT', 'WLD-USDT']
        self.position_size = 40
        self._restore_positions()

    def _restore_positions(self):
        """Восстанавливаем позиции из биржи при старте"""
        try:
            positions = self.api.get_positions()
            restored = 0
            skipped = 0
            for p in positions:
                symbol = p.get('symbol')
                amt = float(p.get('positionAmt', 0))
                if amt == 0:
                    continue
                if symbol in self.symbols:
                    restored += 1
                else:
                    skipped += 1
            print(f"[RESTORE] Восстановлено {restored} позиций из биржи")
            if skipped > 0:
                print(f"[RESTORE] Пропущено {skipped} позиций (не в списке пар)")
            if restored == 0 and skipped == 0:
                print("[RESTORE] Открытых позиций не найдено")
        except Exception as e:
            print(f"[RESTORE] Ошибка: {e}")

    def check_symbol_performance(self):
        """Автоотключение убыточных пар если WR < 40% за 10+ сделок"""
        try:
            trades = []
            import json
            with open("/root/bingx-bot/trades.json") as f:
                trades = json.load(f)
        except:
            return

        from collections import defaultdict
        sym_stats = defaultdict(lambda: {"wins":0,"losses":0,"total":0})

        for t in trades[-200:]:
            sym = t.get("symbol","")
            pnl = t.get("pnl")
            if pnl is None:
                continue
            sym_stats[sym]["total"] += 1
            if float(pnl) > 0:
                sym_stats[sym]["wins"] += 1
            else:
                sym_stats[sym]["losses"] += 1

        disabled = []
        for sym, stats in sym_stats.items():
            if stats["total"] >= 10:
                wr = stats["wins"] / stats["total"] * 100
                if wr < 40 and sym in self.symbols:
                    self.symbols.remove(sym)
                    disabled.append(f"{sym} WR={wr:.1f}%")
                    print(f"[PERF] ❌ Отключена пара {sym} — WR={wr:.1f}% за {stats['total']} сделок")

        if disabled:
            msg = "⚠️ <b>Performance фильтр</b>\\n" + "\\n".join(f"❌ {d}" for d in disabled)
            self.notifier.send_message(msg)

    def check_scalp(self, symbol):
        return  # SCALP DISABLED

        """Скальпинг — быстрые сделки на 5m в Kill Zones"""
        try:
            signal, score, details = self.scalper.get_scalp_signal(symbol, self.api)
            if signal == "HOLD":
                return

            ticker = self.api.get_ticker(symbol)
            if not ticker:
                return
            price = float(ticker.get("lastPrice", 0))
            if price <= 0:
                return

            liq_signal, liq_data = self.liq_map.get_signal(symbol, price, signal)
            if liq_signal in ("AVOID_LONG",) and signal == "BUY":
                print(f"[SCALP-LIQ] {symbol}: блокируем скальп LONG — близко к зоне ликвидации")
                return
            if liq_signal in ("AVOID_SHORT",) and signal == "SELL":
                print(f"[SCALP-LIQ] {symbol}: блокируем скальп SHORT — близко к зоне ликвидации")
                return

            if symbol in self.scalp_positions:
                scalp_pos = self.scalp_positions[symbol]
                entry = scalp_pos["entry"]
                side = scalp_pos["side"]
                qty = scalp_pos["qty"]
                pnl_pct = ((price - entry) / entry * 100) if side == "BUY" else ((entry - price) / entry * 100)

                trail_activate = self.scalper.trailing_activate_pct
                trail_step = self.scalper.trailing_step_pct
                if pnl_pct >= trail_activate:
                    if side == "BUY":
                        new_trail = price * (1 - trail_step / 100)
                        cur_trail = scalp_pos.get("trail_stop", 0)
                        if new_trail > cur_trail:
                            scalp_pos["trail_stop"] = new_trail
                    else:
                        new_trail = price * (1 + trail_step / 100)
                        cur_trail = scalp_pos.get("trail_stop", float("inf"))
                        if new_trail < cur_trail:
                            scalp_pos["trail_stop"] = new_trail

                trail_stop = scalp_pos.get("trail_stop")
                trail_hit = False
                if trail_stop:
                    if side == "BUY" and price <= trail_stop:
                        trail_hit = True
                    elif side == "SELL" and price >= trail_stop:
                        trail_hit = True

                close_side = "SELL" if side == "BUY" else "BUY"

                if trail_hit:
                    # здесь логика закрытия скальп позиции (опущено)
                    pass

        except Exception as e:
            print(f"[SCALP] Error {symbol}: {e}")

    def run(self):
        print("🤖 Trading Bot Started")
        print(f"📊 Symbols: {', '.join(self.symbols)}")
        print(f"💰 Position Size: {self.position_size} USDT")
        self.notifier.notify_bot_start(self.symbols, self.position_size)
        self.commander.start()

        last_report_hour = -1

        while True:
            try:
                try:
                    self.pairs_trader.run_cycle()
                except Exception as e:
                    print(f"[PAIRS] run_cycle error: {e}")

                for symbol in self.symbols:
                    self.check_symbol(symbol)
                    # self.check_scalp(symbol)  # SCALP DISABLED
                    time.sleep(2)

                current_hour = datetime.now().hour
                current_day = datetime.now().day

                if current_hour == 23 and current_day != self.last_reset_day:
                    stats = self.analytics.get_stats()
                    bal_data = self.api.get_balance()
                    bal = 0
                    if bal_data and isinstance(bal_data, dict):
                        inner = bal_data.get('balance', {})
                        bal = float(inner.get('balance', 0)) if isinstance(inner, dict) else float(inner)
                    positions = self.api.get_positions()
                    open_pnl = sum(float(p.get('unrealizedProfit', 0)) for p in positions)
                    daily_report = (f"📅 <b>Дневной отчёт</b>\\n"
                        f"💰 Баланс: <b>{bal:.2f} USDT</b>\\n"
                        f"📈 Открытых позиций: <b>{len(positions)}</b>\\n"
                        f"💹 Unrealized PnL: <b>{open_pnl:+.4f} USDT</b>\\n"
                        f"✅ Сделок за всё время: <b>{stats['total_trades']}</b>\\n"
                        f"🎯 Win Rate: <b>{stats['win_rate']}%</b>\\n"
                        f"💵 Общий PnL: <b>{stats['total_profit']:+.2f}%</b>\\n"
                        f"⭐ Лучшая: <b>{stats['best_trade']:+.2f}%</b>\\n"
                        f"💔 Худшая: <b>{stats['worst_trade']:+.2f}%</b>\\n"
                        f"📉 Дневные потери: <b>{self.daily_loss:.2f} USDT</b>\\n"
                        f"🕐 {datetime.now().strftime('%Y-%m-%d')}")
                    self.notifier.send_message(daily_report)
                    print(f"[DAILY] Отчёт отправлен")

                if current_hour != last_report_hour:
                    last_report_hour = current_hour
                    stats = self.analytics.get_stats()
                    balance = self.api.get_balance()
                    balance_usdt = 0
                    if balance and isinstance(balance, dict):
                        inner = balance.get('balance', {})
                        if isinstance(inner, dict):
                            balance_usdt = float(inner.get('balance', 0))
                        else:
                            balance_usdt = float(inner)
                    report = f"""📊 <b>Ежечасный отчёт</b>

💰 Баланс: <b>{balance_usdt:.2f} USDT</b>
📈 Всего сделок: <b>{stats['total_trades']}</b>
✅ Win Rate: <b>{stats['win_rate']}%</b>
💵 Общая прибыль: <b>{stats['total_profit']:+.2f}%</b>
⭐ Лучшая сделка: <b>{stats['best_trade']:+.2f}%</b>
💔 Худшая сделка: <b>{stats['worst_trade']:+.2f}%</b>
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M')}"""
                    self.notifier.send_message(report)
                    try:
                        # здесь может быть логика сохранения equity_history
                        pass
                    except Exception:
                        pass

            except Exception as e:
                print(f"[MAIN] Error in main loop: {e}")
                time.sleep(5)


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
