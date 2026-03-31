import os
import requests
from datetime import datetime

class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.enabled = bool(self.bot_token and self.chat_id)
        
    def send_message(self, message):
        """Отправляет сообщение в Telegram"""
        if not self.enabled:
            return
            
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data, timeout=10)
            return response.json()
        except Exception as e:
            print(f"⚠️ Ошибка отправки в Telegram: {e}")
            
    def notify_trade_open(self, symbol, side, price, quantity):
        """Уведомление об открытии сделки"""
        emoji = "🟢" if side == "BUY" else "🔴"
        message = f"""{emoji} <b>Открыта позиция</b>

📊 Пара: <code>{symbol}</code>
📈 Направление: <b>{side}</b>
💰 Цена входа: <code>{price:.6f}</code>
📦 Объем: <code>{quantity:.4f}</code>
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        self.send_message(message)
        
    def notify_trade_close(self, symbol, side, price, quantity, profit=None):
        """Уведомление о закрытии сделки"""
        emoji = "✅" if profit and profit > 0 else "❌"
        profit_text = f"\n💵 P&L: <b>{profit:+.2f} USDT</b>" if profit else ""
        
        message = f"""{emoji} <b>Закрыта позиция</b>

📊 Пара: <code>{symbol}</code>
📉 Направление: <b>{side}</b>
💰 Цена выхода: <code>{price:.6f}</code>
📦 Объем: <code>{quantity:.4f}</code>{profit_text}
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        self.send_message(message)
        
    def notify_sl_tp_set(self, symbol, sl_price, tp_price):
        """Уведомление об установке SL/TP"""
        message = f"""🛡️ <b>Установлены SL/TP</b>

📊 Пара: <code>{symbol}</code>
🔻 Stop-Loss: <code>{sl_price:.6f}</code>
🔺 Take-Profit: <code>{tp_price:.6f}</code>
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        self.send_message(message)
        
    def notify_error(self, error_message):
        """Уведомление об ошибке"""
        message = f"""⚠️ <b>Ошибка в боте</b>

{error_message}
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""
        self.send_message(message)
        
    def notify_daily_stats(self, stats):
        """Ежедневная статистика"""
        total_trades = stats.get('total_trades', 0)
        win_rate = stats.get('win_rate', 0)
        total_profit = stats.get('total_profit', 0)
        
        emoji = "📈" if total_profit > 0 else "📉"
        
        message = f"""{emoji} <b>Дневная статистика</b>

📊 Всего сделок: <b>{total_trades}</b>
✅ Процент побед: <b>{win_rate:.1f}%</b>
💵 Общая прибыль: <b>{total_profit:+.2f} USDT</b>
⏰ Дата: {datetime.now().strftime('%Y-%m-%d')}"""
        self.send_message(message)
        
    def notify_bot_start(self, symbols, position_size):
        """Уведомление о запуске бота"""
        message = f"""🚀 <b>Торговый бот запущен</b>

📊 Торговые пары: <code>{", ".join(symbols)}</code>
💰 Размер позиции: <b>{position_size} USDT</b>
⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

🔄 Бот начал мониторинг рынка..."""
        self.send_message(message)
