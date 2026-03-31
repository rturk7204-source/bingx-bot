#!/usr/bin/env python3
import subprocess
import time
import requests
import os
from dotenv import load_dotenv
load_dotenv()

BOT_SERVICE = "bingx-bot"
CHECK_INTERVAL = 300  # 5 минут
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_alert(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except:
        pass

def is_bot_running():
    result = subprocess.run(["systemctl", "is-active", BOT_SERVICE],
        capture_output=True, text=True)
    return result.stdout.strip() == "active"

def restart_bot():
    subprocess.run(["systemctl", "restart", BOT_SERVICE])

def check_bot_activity():
    """Проверяем что бот реально работает (есть свежие логи)"""
    result = subprocess.run(
        ["journalctl", "-u", BOT_SERVICE, "-n", "5", "--no-pager", "-o", "short"],
        capture_output=True, text=True)
    lines = result.stdout.strip().split("\n")
    if not lines:
        return False
    last_line = lines[-1]
    # Проверяем что последняя запись была не более 5 минут назад
    import re
    from datetime import datetime
    try:
        match = re.search(r"(\w{3}\s+\d+\s+\d+:\d+:\d+)", last_line)
        if match:
            log_time = datetime.strptime(f"2026 {match.group(1)}", "%Y %b %d %H:%M:%S")
            diff = (datetime.now() - log_time).total_seconds()
            return diff < 600  # не более 10 минут
    except:
        pass
    return True

print("[WATCHDOG] Запущен — проверяю бота каждые 5 минут")
send_alert("🔍 <b>Watchdog запущен</b>\nМониторинг бота каждые 5 минут")

restart_count = 0
while True:
    try:
        if not is_bot_running():
            print("[WATCHDOG] Бот не запущен — перезапускаю...")
            restart_bot()
            time.sleep(10)
            if is_bot_running():
                restart_count += 1
                send_alert(f"🔄 <b>Watchdog перезапустил бота</b>\nПерезапуск #{restart_count}")
                print(f"[WATCHDOG] Бот перезапущен (#{restart_count})")
            else:
                send_alert("🚨 <b>Watchdog: не удалось перезапустить бота!</b>")
                print("[WATCHDOG] ОШИБКА: не удалось перезапустить!")
        else:
            print(f"[WATCHDOG] Бот работает нормально ({time.strftime('%H:%M:%S')})")

    except Exception as e:
        print(f"[WATCHDOG] Ошибка: {e}")

    time.sleep(CHECK_INTERVAL)
