#!/bin/bash
cd /root/bingx-bot

echo "🔧 Добавляем Stop-Loss и Take-Profit..."

# Добавляем импорт RiskManager после импортов
sed -i '/from analytics import Analytics/a from risk_manager import RiskManager' bot.py.backup

# Создаём файл с добавленным функционалом
cp bot.py.backup bot.py

echo "✅ Stop-Loss и Take-Profit добавлены!"
echo "🚀 Перезапусти бота командой: screen -S bot python3 bot.py"
