#!/bin/bash
BACKUP_DIR="/root/bingx-backups"
DATE=$(date +%Y%m%d)
TARGET="$BACKUP_DIR/backup_$DATE"

mkdir -p "$TARGET"

# Критические файлы
cp /root/bingx-bot/trades.json "$TARGET/" 2>/dev/null
cp /root/bingx-bot/rl_states.json "$TARGET/" 2>/dev/null
cp /root/bingx-bot/balance_history.json "$TARGET/" 2>/dev/null
cp /root/bingx-bot/blacklist.json "$TARGET/" 2>/dev/null
cp /root/bingx-bot/pairs_state.json "$TARGET/" 2>/dev/null
cp /root/bingx-bot/oi_history.json "$TARGET/" 2>/dev/null
cp /root/bingx-bot/feature_importance_history.json "$TARGET/" 2>/dev/null
cp /root/bingx-bot/.env "$TARGET/" 2>/dev/null

# Модели
cp -r /root/bingx-bot/models/ "$TARGET/" 2>/dev/null

# Ротация — удаляем старше 7 дней
find "$BACKUP_DIR" -maxdepth 1 -name "backup_*" -mtime +7 -exec rm -rf {} \;

echo "[BACKUP] $(date): saved to $TARGET ($(du -sh $TARGET | cut -f1))"
