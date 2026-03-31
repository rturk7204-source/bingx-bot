#!/bin/bash
# Авто-бэкап ML моделей перед переобучением

MODELS_DIR="/root/bingx-bot/models"
BACKUP_DIR="/root/bingx-bot/models_backup"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Копируем текущие модели
cp -r $MODELS_DIR $BACKUP_DIR/models_$DATE

# Оставляем только последние 5 бэкапов
ls -dt $BACKUP_DIR/models_* | tail -n +6 | xargs rm -rf

echo "Бэкап создан: models_$DATE"

# Уведомление в Telegram
python3 -c "
import os
from dotenv import load_dotenv
import requests
load_dotenv()
token = os.getenv('TELEGRAM_BOT_TOKEN')
chat_id = os.getenv('TELEGRAM_CHAT_ID')
requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
    data={'chat_id': chat_id, 'text': '💾 Бэкап ML моделей создан: models_$DATE', 'parse_mode': 'HTML'})
"
