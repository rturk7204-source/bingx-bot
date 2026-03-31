import re

with open('bot.py', 'r') as f:
    content = f.read()

# Паттерн для поиска блока BUY
buy_pattern = r"(if order:\s+self\.analytics\.log_trade\(\s+symbol=symbol,\s+side='BUY',\s+price=price,\s+quantity=quantity,\s+reason='ML Strategy Signal'\s+\))"

# Замена для BUY - добавляем вызов set_sl_tp
buy_replacement = r"\1\n                    self.risk_manager.set_sl_tp(symbol, price, 'LONG', self.api)"

content = re.sub(buy_pattern, buy_replacement, content, flags=re.MULTILINE)

# Паттерн для поиска блока SELL 
sell_pattern = r"(if order:\s+self\.analytics\.log_trade\(\s+symbol=symbol,\s+side='SELL',\s+price=price,\s+quantity=quantity,\s+reason='ML Strategy Exit'\s+\))"

# Замена для SELL - добавляем вызов set_sl_tp
sell_replacement = r"\1\n                    self.risk_manager.set_sl_tp(symbol, price, 'SHORT', self.api)"

content = re.sub(sell_pattern, sell_replacement, content, flags=re.MULTILINE)

with open('bot.py', 'w') as f:
    f.write(content)

print("✅ Stop-Loss и Take-Profit интеграция завершена!")
