import re

with open('bot.py', 'r') as f:
    content = f.read()

# 1. Добавляем уведомление при запуске бота (в метод run)
run_start_pattern = r"(def run\(self\):\s+print\(f\"🐞 Trading Bot Started\"\))"
run_start_replacement = r"\1\n        self.notifier.notify_bot_start(self.symbols, self.position_size)"
content = re.sub(run_start_pattern, run_start_replacement, content, flags=re.MULTILINE)

# 2. Добавляем уведомление при открытии LONG позиции
long_open_pattern = r"(self\.risk_manager\.set_sl_tp\(symbol, price, 'LONG', self\.api\))"
long_open_replacement = r"\1\n                    self.notifier.notify_trade_open(symbol, 'BUY', price, quantity)"
content = re.sub(long_open_pattern, long_open_replacement, content, flags=re.MULTILINE)

# 3. Добавляем уведомление при закрытии LONG позиции  
long_close_pattern = r"(print\(f\"🟥CLOSE {symbol} @ {price}\"\))\s+(elif signal == 'SELL' and current_position:)"
long_close_replacement = r"\1\n                    self.notifier.notify_trade_close(symbol, 'SELL', price, quantity)\n\n            \2"
content = re.sub(long_close_pattern, long_close_replacement, content, flags=re.MULTILINE | re.DOTALL)

with open('bot.py', 'w') as f:
    f.write(content)

print("✅ Уведомления Telegram интегрированы!")
