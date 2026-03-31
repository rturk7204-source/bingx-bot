import os
import requests
import threading
import time
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

class TelegramCommander:
    def __init__(self, bot_ref):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID")
        self.bot = bot_ref
        self.last_update_id = 0
        self.running = True

    def send(self, text):
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            requests.post(url, data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            print(f"[CMD] Send error: {e}")

    def get_updates(self):
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
            r = requests.get(url, params={"offset": self.last_update_id + 1, "timeout": 10}, timeout=15)
            return r.json().get("result", [])
        except:
            return []

    def handle_command(self, text):
        text = text.strip().lower()

        if text == "/status":
            status = "✅ Активен" if self.bot.trading_enabled else "⏸ Остановлен"
            msg = f"""🤖 <b>Статус бота</b>

{status}
📊 Пары: de>{", ".join(self.bot.symbols)}</code>
💰 Базовый размер: <b>{self.bot.position_size} USDT</b>
⏰ Время: {datetime.now().strftime("%Y-%m-%d %H:%M")}"""
            self.send(msg)

        elif text == "/balance":
            try:
                balance = self.bot.api.get_balance()
                inner = balance.get("balance", {})
                usdt = float(inner.get("balance", 0))
                equity = float(inner.get("equity", 0))
                pnl = float(inner.get("unrealizedProfit", 0))
                margin = float(inner.get("availableMargin", 0))
                msg = f"""💰 <b>Баланс</b>

💵 Баланс: <b>{usdt:.2f} USDT</b>
📊 Equity: <b>{equity:.2f} USDT</b>
📈 Unrealized PnL: <b>{pnl:+.2f} USDT</b>
🔓 Доступная маржа: <b>{margin:.2f} USDT</b>"""
                self.send(msg)
            except Exception as e:
                self.send(f"❌ Ошибка: {e}")

        elif text == "/positions":
            try:
                positions = self.bot.api.get_positions()
                if not positions:
                    self.send("📭 Нет открытых позиций")
                else:
                    msg = "📊 <b>Открытые позиции</b>\n\n"
                    for p in positions:
                        symbol = p.get("symbol", "")
                        side = p.get("positionSide", "")
                        amt = float(p.get("positionAmt", 0))
                        entry = float(p.get("avgPrice", 0))
                        pnl = float(p.get("unrealizedProfit", 0))
                        msg += f"• {symbol} {side}: {amt:.4f} @ {entry:.4f} | PnL: {pnl:+.2f}\n"
                    self.send(msg)
            except Exception as e:
                self.send(f"❌ Ошибка: {e}")

        elif text == "/stats":
            try:
                stats = self.bot.analytics.get_stats()
                msg = f"""📈 <b>Статистика</b>

📊 Всего сделок: <b>{stats["total_trades"]}</b>
✅ Win Rate: <b>{stats["win_rate"]}%</b>
💵 Прибыль: <b>{stats["total_profit"]:+.2f}%</b>
⭐ Лучшая: <b>{stats["best_trade"]:+.2f}%</b>
💔 Худшая: <b>{stats["worst_trade"]:+.2f}%</b>"""
                self.send(msg)
            except Exception as e:
                self.send(f"❌ Ошибка: {e}")

        elif text == "/stop":
            self.bot.trading_enabled = False
            self.send("⏸ <b>Торговля остановлена</b>\nБот продолжает мониторинг но не открывает сделки.")

        elif text == "/start":
            self.bot.trading_enabled = True
            self.send("✅ <b>Торговля возобновлена</b>")

        elif text == "/daily":
            loss = self.bot.daily_loss
            limit = self.bot.daily_loss_limit
            remaining = max(0, limit - loss)
            pct = (loss / limit * 100) if limit > 0 else 0
            status = "🔴 Лимит достигнут" if loss >= limit else "🟢 В норме"
            msg = f"""📅 <b>Дневная статистика потерь</b>

{status}
💸 Потери сегодня: <b>{loss:.2f} USDT</b>
🎯 Лимит: <b>{limit:.2f} USDT</b>
✅ Осталось: <b>{remaining:.2f} USDT</b>
📊 Использовано: <b>{pct:.1f}%</b>"""
            self.send(msg)

        elif text == "/backup":
            import subprocess
            result = subprocess.run(['bash', '/root/bingx-bot/backup_models.sh'], capture_output=True, text=True)
            self.send('Backup: ' + result.stdout)

        elif text == "/restore":
            import subprocess, os
            backup_dir = '/root/bingx-bot/models_backup'
            backups = sorted(os.listdir(backup_dir)) if os.path.exists(backup_dir) else []
            if backups:
                latest = backups[-1]
                msg = "Доступные бэкапы:\n"
                for b in backups[-5:]:
                    msg += "• " + b + "\n"
                msg += "\nПоследний: " + latest
                self.send(msg)
            else:
                self.send('❌ Бэкапов нет')

        elif text == "/help":
            msg = """📋 <b>Команды бота</b>

/status — статус бота
/balance — баланс на бирже
/positions — открытые позиции
/stats — статистика сделок
/stop — остановить торговлю
/start — возобновить торговлю
/pause — пауза без закрытия
/resume — продолжить
/pnl — PnL по всем позициям
/risk — параметры риска
/close PAIR — закрыть позицию
/closeall — закрыть все
/help — эта справка"""
            self.send(msg)

        elif text.startswith("/close "):
            symbol = text.split()[1].upper()
            try:
                positions = self.bot.api.get_positions()
                pos = next((p for p in positions if p["symbol"] == symbol), None)
                if not pos:
                    self.send("Poziciya " + symbol + " ne naydena")
                else:
                    side = pos["positionSide"]
                    qty = abs(float(pos["positionAmt"]))
                    close_side = "SELL" if side == "LONG" else "BUY"
                    order = self.bot.api.close_position(symbol=symbol, side=close_side, quantity=qty, price=0)
                    if order and order.get("code") == 0:
                        pnl = float(pos.get("unrealizedProfit", 0))
                        self.bot.risk_manager.remove_position(symbol)
                        self.send(symbol + " zakryta | PnL: " + f"{pnl:+.4f} USDT")
                    else:
                        self.send("Oshibka zakrytiya " + symbol)
            except Exception as e:
                self.send("Oshibka: " + str(e))

        elif text == "/closeall":
            try:
                positions = self.bot.api.get_positions()
                if not positions:
                    self.send("Net otkrytykh poziciy")
                else:
                    closed = 0
                    for pos in positions:
                        sym = pos["symbol"]
                        side = pos["positionSide"]
                        qty = abs(float(pos["positionAmt"]))
                        close_side = "SELL" if side == "LONG" else "BUY"
                        order = self.bot.api.close_position(symbol=sym, side=close_side, quantity=qty, price=0)
                        if order and order.get("code") == 0:
                            self.bot.risk_manager.remove_position(sym)
                            closed += 1
                    self.send(f"Zakryto poziciy: {closed}/{len(positions)}")
            except Exception as e:
                self.send("Oshibka: " + str(e))

        elif text == "/pause":
            self.bot.trading_enabled = False
            self.send("Torgovlya priostanovlena. Pozicii sokhraneny.")

        elif text == "/resume":
            self.bot.trading_enabled = True
            self.send("Torgovlya vozobnovlena")

        elif text == "/pnl":
            try:
                positions = self.bot.api.get_positions()
                bal = self.bot.api.get_balance()
                inner = bal.get("balance", {})
                balance = float(inner.get("balance", 0))
                upnl = float(inner.get("unrealizedProfit", 0))
                stats = self.bot.analytics.get_stats()
                lines = [
                    "<b>PnL Report</b>",
                    f"Balance: <b>{balance:.2f} USDT</b>",
                    f"Unrealized: <b>{upnl:+.4f} USDT</b>",
                    f"Closed PnL: <b>{stats['total_profit']:+.2f}%</b>",
                    f"Win Rate: <b>{stats['win_rate']}%</b>",
                    ""
                ]
                for p in positions:
                    entry = float(p["avgPrice"])
                    mark = float(p["markPrice"])
                    side = p["positionSide"]
                    pct = ((mark-entry)/entry*100) if side=="LONG" else ((entry-mark)/entry*100)
                    usdt = float(p["unrealizedProfit"])
                    sign = "+" if pct >= 0 else ""
                    lines.append(f"{p['symbol']} {side}: <b>{sign}{pct:.2f}%</b> ({usdt:+.4f} USDT)")
                self.send("\n".join(lines))
            except Exception as e:
                self.send("Oshibka: " + str(e))

        elif text == "/risk":
            lines = [
                "<b>Risk Parameters</b>",
                f"Position size: <b>{self.bot.position_size} USDT</b>",
                f"Stop-Loss: <b>{self.bot.risk_manager.stop_loss_pct}%</b>",
                f"Trailing: <b>{self.bot.risk_manager.trailing_pct}%</b>",
                f"Max budget: <b>{self.bot.max_budget} USDT</b>",
                f"Max margin: <b>{self.bot.max_margin_pct}%</b>",
                f"Max positions: <b>{self.bot.max_same_direction}</b>",
                f"Daily loss: <b>{self.bot.daily_loss:.2f}/{self.bot.daily_loss_limit:.2f} USDT</b>"
            ]
            self.send("\n".join(lines))

        else:
            self.send("Neizvestnaya komanda. /help")

    def listen(self):
        print("[CMD] Telegram commander started")
        self.send("🤖 Бот запущен и слушает команды. Напиши /help")
        while self.running:
            try:
                updates = self.get_updates()
                for update in updates:
                    self.last_update_id = update["update_id"]
                    message = update.get("message", {})
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    text = message.get("text", "")
                    if chat_id == str(self.chat_id) and text.startswith("/"):
                        print(f"[CMD] Command received: {text}")
                        self.handle_command(text)
                time.sleep(2)
            except Exception as e:
                print(f"[CMD] Error: {e}")
                time.sleep(5)

    def start(self):
        thread = threading.Thread(target=self.listen, daemon=True)
        thread.start()
