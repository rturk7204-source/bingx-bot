# 📣 Настройка Telegram уведомлений

## Шаг 1: Создайте Telegram бота

1. Откройте Telegram и найдите **@BotFather**
2. Отправьте команду: `/newbot`
3. Введите имя бота (например: `My Trading Bot`)
4. Введите username бота (например: `my_trading_bot`)
5. Скопируйте полученный **API Token**

## Шаг 2: Получите Chat ID

1. Найдите вашего бота в Telegram и нажмите **Start**
2. Отправьте любое сообщение боту (например: `Hello`)
3. Откройте в браузере:
   ```
   https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
   ```
4. Найдите значение `"chat":{"id":12345678}` - это ваш Chat ID

## Шаг 3: Обновите .env файл

Откройте файл `.env` и замените:

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here    # Ваш токен из BotFather
TELEGRAM_CHAT_ID=your_chat_id_here        # Ваш Chat ID
```

## Шаг 4: Перезапустите бота

```bash
pkill -f bot.py
nohup python3 bot.py &
```

## 🎉 Готово!

Теперь вы будете получать уведомления о:
- ✅ Запуске бота
- 🟢 Открытии позиций
- 🟥 Закрытии позиций
- 🛡️ Установке SL/TP
- ⚠️ Ошибках
