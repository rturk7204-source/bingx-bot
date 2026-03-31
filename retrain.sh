#!/bin/bash
cd /root/bingx-bot
echo "$(date): Retrain v2..." >> train_log.txt
python3 retrain_v2.py >> train_log.txt 2>&1
systemctl restart bingx-bot
echo "$(date): Done" >> train_log.txt
