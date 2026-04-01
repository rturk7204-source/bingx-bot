import os
import time
import json
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
        self.max_budget = 500.0
        self.peak_balance = 0.0
        self.daily_loss_limit = 30.0
        self.consecutive_stops = 0
        self.cooldown_until = None
        self.cooldown_hours = 3
        self.max_consecutive_stops = 3
        self.weekly_pnl = 0.0
        self.week_start = datetime.now()
        self.symbols = [
            'ETH-USDT', 'SUI-USDT', 'DOGE-USDT', 'ADA-USDT',
            'XRP-USDT'
        ]
        self.position_size = 40
        self._pair_cooldown = {}
        self._pair_losses = {}

    def check_symbol(self, symbol):
        try:
            if not self.trading_enabled:
                return
            if self.cooldown_until and datetime.now() < self.cooldown_until:
                return
            try:
                if self.auto_blacklist.is_blocked(symbol):
                    print(f"[BL] {symbol}: blacklisted, skip")
                    return
            except:
                pass
            now = datetime.utcnow()
            if now.hour not in [7,8,9,10,11,12,13,14,15,16,17,18,19,20,21]:
                print(f"[SESSION] {symbol}: outside trading hours UTC={now.hour}")
                return
            ticker = self.api.get_ticker(symbol)
            if not ticker:
                return
            price = float(ticker.get("lastPrice", 0) or 0)
            if price <= 0:
                return
            try:
                strategy_signal = self.strategy.get_signal(symbol, self.api)
            except Exception as se:
                print(f"[SMC] {symbol}: signal error: {se}")
                return
            if isinstance(strategy_signal, (list, tuple)) and len(strategy_signal) >= 2:
                sig, prob = strategy_signal[0], strategy_signal[1]
            else:
                sig, prob = strategy_signal, 0.5
            side = str(sig).upper()
            if side == "BUY":
                side = "LONG"
            elif side == "SELL":
                side = "SHORT"
            elif side not in ("LONG", "SHORT"):
                side = "HOLD"
            print(f"[SMC] {symbol}: signal={sig} side={side} prob={prob:.3f}")
            if side == "HOLD":
                return
            # FNG-фильтр: при экстремальном страхе (<20) блокируем шорты
            # кроме случаев с очень высокой уверенностью (prob >= 0.85)
            try:
                from fear_greed import get_fear_greed
                fng_val = get_fear_greed()
                if fng_val is not None and fng_val < 20 and side == "SHORT":
                    if prob < 0.85:
                        print(f"[FNG] {symbol}: FNG={fng_val} < 20, blocking SHORT (prob={prob:.3f} < 0.85)")
                        return
                    else:
                        print(f"[FNG] {symbol}: FNG={fng_val} < 20, but prob={prob:.3f} >= 0.85 — allowing SHORT")
            except Exception as fng_err:
                print(f"[FNG] {symbol}: error getting FNG: {fng_err}")
            # Кулдаун по паре: после 2 убытков подряд — пауза 2 часа
            if hasattr(self, '_pair_cooldown') and symbol in self._pair_cooldown:
                if datetime.now() < self._pair_cooldown[symbol]:
                    remaining = (self._pair_cooldown[symbol] - datetime.now()).seconds // 60
                    print(f"[COOLDOWN] {symbol}: pair cooldown, {remaining}min left")
                    return
                else:
                    del self._pair_cooldown[symbol]
                    if symbol in self._pair_losses:
                        del self._pair_losses[symbol]
            if self.weekly_pnl <= -5.0:
                print(f"[DRAWDOWN] weekly PnL={self.weekly_pnl:.2f}%, block")
                return
            positions = self.api.get_positions() or []
            current_position = None
            for p in positions:
                psym = str(p.get("symbol", "")).replace("_", "-")
                if psym == symbol:
                    amt = abs(float(p.get("positionAmt", 0) or 0))
                    if amt > 0:
                        current_position = p
                        break
            pos_side = None
            if current_position:
                ps = str(current_position.get("positionSide", "")).upper()
                if ps == "BUY": pos_side = "LONG"
                elif ps == "SELL": pos_side = "SHORT"
                else: pos_side = ps
            if pos_side == side:
                return
            if current_position and pos_side and pos_side != side:
                cq = abs(float(current_position.get("positionAmt", 0) or 0))
                cs = "SELL" if pos_side == "LONG" else "BUY"
                print(f"[CLOSE] {symbol}: closing {pos_side} qty={cq}")
                try:
                    self.api.close_position(symbol=symbol, side=cs, quantity=cq, price=price)
                except Exception as e:
                    print(f"[CLOSE] {symbol}: error: {e}")
                    return
            try:
                bal_data = self.api.get_balance()
                bal = 0
                if bal_data and isinstance(bal_data, dict):
                    inner = bal_data.get('balance', {})
                    bal = float(inner.get('balance', 0)) if isinstance(inner, dict) else float(inner)
                entry_qty = self.kelly.get_size(self.analytics, bal, prob)
                if entry_qty < 5:
                    entry_qty = 5.0
                if entry_qty > 70:
                    entry_qty = 70.0
                print(f"[KELLY] {symbol}: bal={bal:.1f} prob={prob:.3f} qty={entry_qty:.1f}")
            except Exception as ke:
                entry_qty = self.position_size
                print(f"[KELLY] {symbol}: fallback qty={entry_qty}, err={ke}")
            entry_qty = round(entry_qty / price, 6)
            print(f"[OPEN] {symbol}: {side} qty={entry_qty} price={price} prob={prob:.3f}")
            try:
                api_side = "BUY" if side == "LONG" else "SELL"
                order = self.api.open_position(
                    symbol=symbol, side=api_side,
                    quantity=entry_qty, price=price
                )
                print(f"[OPEN] {symbol}: order={order}")
                self.notifier.send_message(
                    f"{'🟢' if side=='LONG' else '🔴'} {side} {symbol}\n"
                    f"Price: {price}\nQty: {entry_qty} USDT\nProb: {prob:.1%}"
                )
            except Exception as e:
                print(f"[OPEN] {symbol}: error: {e}")
            try:
                self.rl_logger.log(symbol, side, price, prob)
            except:
                pass
        except Exception as e:
            print(f"[ERR] check_symbol({symbol}): {e}")

    def manage_positions(self):
        try:
            positions = self.api.get_positions() or []
            for pos in positions:
                symbol = str(pos.get("symbol", "")).replace("_", "-")
                amt = float(pos.get("positionAmt", 0) or 0)
                if amt == 0:
                    continue
                if symbol not in self.symbols:
                    continue
                entry = float(pos.get("avgPrice", 0) or pos.get("entryPrice", 0) or 0)
                if entry <= 0:
                    continue
                ticker = self.api.get_ticker(symbol)
                if not ticker:
                    continue
                price = float(ticker.get("lastPrice", 0) or 0)
                if price <= 0:
                    continue
                ps = str(pos.get("positionSide", "")).upper()
                if ps == "SHORT":
                    pnl_pct = (entry - price) / entry * 100
                    close_side = "BUY"
                else:
                    pnl_pct = (price - entry) / entry * 100
                    close_side = "SELL"
                qty = abs(amt)
                # Stop Loss -3%
                if pnl_pct <= -1.5:
                    print(f"[SL] {symbol}: pnl={pnl_pct:.2f}% -> STOP LOSS")
                    self.api.close_position(symbol=symbol, side=close_side, quantity=qty, price=price)
                    self.notifier.send_message(f"🛑 SL {symbol} pnl={pnl_pct:.2f}%")
                for _d in ('_be_done', '_partial_done', '_avg_done', '_tp1_done'):
                    _dd = getattr(self, _d, {})
                    _dd.pop(symbol, None)
                    _dd.pop(f'avg_{symbol}', None)
                    self.consecutive_stops += 1
                    # Кулдаун по паре: считаем убытки подряд
                    if not hasattr(self, '_pair_losses'):
                        self._pair_losses = {}
                    if not hasattr(self, '_pair_cooldown'):
                        self._pair_cooldown = {}
                    self._pair_losses[symbol] = self._pair_losses.get(symbol, 0) + 1
                    if self._pair_losses[symbol] >= 2:
                        from datetime import timedelta
                        self._pair_cooldown[symbol] = datetime.now() + timedelta(hours=2)
                        print(f"[COOLDOWN] {symbol}: 2 losses in a row -> 2h cooldown")
                        self.notifier.send_message(f"⏸ {symbol}: 2 losses -> cooldown 2h")
                    if self.consecutive_stops >= self.max_consecutive_stops:
                        self.cooldown_until = datetime.now()
                        from datetime import timedelta
                        self.cooldown_until += timedelta(hours=self.cooldown_hours)
                        print(f"[COOLDOWN] {self.consecutive_stops} stops -> cooldown {self.cooldown_hours}h")
                    continue
                # TP1 при +1.5% — закрываем 50% (1:1 RR)
                if pnl_pct >= 1.5:
                    if not hasattr(self, '_tp1_done'):
                        self._tp1_done = {}
                    if symbol not in self._tp1_done:
                        tp1_qty = round(qty * 0.5, 6)
                        if tp1_qty > 0:
                            print(f"[TP1] {symbol}: pnl={pnl_pct:.2f}% -> close 50% (1:1 RR)")
                            try:
                                self.api.close_position(symbol=symbol, side=close_side, quantity=tp1_qty, price=price)
                                self._tp1_done[symbol] = True
                                self.notifier.send_message(f"\U0001f3af TP1 {symbol} 50% at {pnl_pct:.2f}%")
                            except Exception as e:
                                print(f"[TP1] {symbol}: error: {e}")
                # TP2 при +3.0% — закрываем остаток (2:1 RR)
                if pnl_pct >= 3.0:
                    print(f"[TP2] {symbol}: pnl={pnl_pct:.2f}% -> FULL TAKE PROFIT (2:1 RR)")
                    self.api.close_position(symbol=symbol, side=close_side, quantity=qty, price=price)
                    self.notifier.send_message(f"\U0001f3af TP2 {symbol} pnl={pnl_pct:.2f}%")
                    for _d in ('_be_done', '_partial_done', '_avg_done', '_tp1_done'):
                        _dd = getattr(self, _d, {})
                        _dd.pop(symbol, None)
                        _dd.pop(f'avg_{symbol}', None)
                    self.consecutive_stops = 0
                    if hasattr(self, '_pair_losses') and symbol in self._pair_losses:
                        del self._pair_losses[symbol]
                    continue
                # Реальный breakeven: при +1% ставим SL на вход +0.15% (однократно)
                if pnl_pct >= 1.0:
                    if not hasattr(self, '_be_done'):
                        self._be_done = {}
                    if symbol not in self._be_done:
                        try:
                            offset = 0.0015  # 0.15% от входа
                            if ps == "LONG":
                                be_price = round(entry * (1 + offset), 6)
                            else:
                                be_price = round(entry * (1 - offset), 6)
                            # Отменяем старые ордера и ставим новый SL
                            self.api.cancel_open_orders(symbol)
                            result = self.api.set_stop_loss(symbol, ps, be_price)
                            if result and result.get('code') == 0:
                                self._be_done[symbol] = be_price
                                print(f"[BE] {symbol}: pnl={pnl_pct:.2f}% -> REAL SL at {be_price} (entry={entry})")
                                self.notifier.send_message(f"🔒 BE {symbol}: SL -> {be_price}")
                            else:
                                print(f"[BE] {symbol}: failed to set SL: {result}")
                        except Exception as e:
                            print(f"[BE] {symbol}: error setting SL: {e}")
                    else:
                        print(f"[BE] {symbol}: pnl={pnl_pct:.2f}% (SL already at {self._be_done[symbol]})")
                # SMC Averaging: 1 раз при -1.5%, если SMC подтверждает
                if -2.5 <= pnl_pct <= -1.5:
                    avg_key = f"avg_{symbol}"
                    if not hasattr(self, '_avg_done'):
                        self._avg_done = {}
                    if avg_key not in self._avg_done:
                        try:
                            from smc_analyzer import SMCAnalyzer
                            _smc = SMCAnalyzer()
                            kl = self.api.get_klines(symbol, interval="1h", limit=30)
                            if kl and kl.get("code") == 0 and kl.get("data"):
                                smc_r = _smc.analyze(kl["data"][-30:])
                                sc = smc_r.get("score", 0)
                                sig = smc_r.get("signal", "NEUTRAL")
                                need = "BULLISH" if ps == "LONG" else "BEARISH"
                                if sig == need and sc >= 3:
                                    avg_qty = round(qty * 0.5, 6)
                                    avg_side = "BUY" if ps == "LONG" else "SELL"
                                    print(f"[AVG] {symbol}: SMC {sig} sc={sc}, averaging {avg_side} qty={avg_qty}")
                                    self.api.open_position(symbol=symbol, side=avg_side, quantity=avg_qty, price=price)
                                    self._avg_done[avg_key] = True
                                    self.notifier.send_message(f"📉 AVG {symbol} {avg_side} qty={avg_qty} pnl={pnl_pct:.2f}% SMC={sc}")
                                else:
                                    print(f"[AVG] {symbol}: SMC {sig} sc={sc} != {need}, no avg")
                        except Exception as ae:
                            print(f"[AVG] {symbol}: error: {ae}")
                print(f"[POS] {symbol}: pnl={pnl_pct:+.2f}%")
        except Exception as e:
            print(f"[MANAGE] error: {e}")

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
                    print(f"[PAIRS] error: {e}")
                self.manage_positions()
                for symbol in self.symbols:
                    self.check_symbol(symbol)
                    time.sleep(2)
                now = datetime.now()
                if now.hour != last_report_hour:
                    last_report_hour = now.hour
                    try:
                        stats = self.analytics.get_stats()
                        bal = self.api.get_balance()
                        b = 0
                        if bal and isinstance(bal, dict):
                            inner = bal.get('balance', {})
                            if isinstance(inner, dict):
                                b = float(inner.get('balance', 0))
                            else:
                                b = float(inner)
                        rpt = (f"📊 Hourly\n"
                               f"💰 {b:.2f} USDT\n"
                               f"📈 Trades: {stats['total_trades']}\n"
                               f"✅ WR: {stats['win_rate']}%\n"
                               f"💵 PnL: {stats['total_profit']:+.2f}%")
                        self.notifier.send_message(rpt)
                    except:
                        pass
            except Exception as e:
                print(f"[MAIN] Error: {e}")
                time.sleep(5)

if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
