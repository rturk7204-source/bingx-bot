#!/usr/bin/env python3
"""
Health check — проверяет что бот жив.
Если последний hourly report > 2 часов назад → Telegram алерт.
Запускается по cron каждые 30 минут.
"""
import json, os, time, requests
from datetime import datetime, timezone

BOT_DIR = "/root/bingx-bot"
ALERT_FILE = f"{BOT_DIR}/last_health_alert.txt"
MAX_SILENCE_SEC = 7200  # 2 часа

def get_telegram_creds():
    env_file = f"{BOT_DIR}/.env"
    creds = {}
    if os.path.exists(env_file):
        for line in open(env_file):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds.get("TELEGRAM_BOT_TOKEN", creds.get("TELEGRAM_TOKEN")), creds.get("TELEGRAM_CHAT_ID")

def send_alert(msg):
    token, chat_id = get_telegram_creds()
    if not token or not chat_id:
        print(f"[HEALTH] No telegram creds")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}, timeout=10)
        print(f"[HEALTH] Alert sent: {msg}")
    except Exception as e:
        print(f"[HEALTH] Send error: {e}")

def check():
    # Проверяем systemd
    status = os.popen("systemctl is-active bingx-bot").read().strip()
    if status != "active":
        send_alert(f"🚨 <b>BOT DOWN!</b>\nStatus: {status}\nTime: {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
        return

    # Проверяем balance_history — последняя запись
    bh_file = f"{BOT_DIR}/balance_history.json"
    if os.path.exists(bh_file):
        try:
            with open(bh_file) as f:
                bh = json.load(f)
            if bh:
                # Проверяем по mtime файла (надёжнее)
                age = time.time() - os.path.getmtime(bh_file)
                
                if age > MAX_SILENCE_SEC:
                    # Не спамим — проверяем когда последний алерт
                    if os.path.exists(ALERT_FILE):
                        last_alert = float(open(ALERT_FILE).read().strip())
                        if time.time() - last_alert < 3600:  # Не чаще раза в час
                            return
                    
                    hours = age / 3600
                    send_alert(
                        f"⚠️ <b>BOT SILENT</b>\n"
                        f"Последний отчёт: {hours:.1f}ч назад\n"
                        f"Бот активен но не торгует\n"
                        f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
                    )
                    with open(ALERT_FILE, "w") as f:
                        f.write(str(time.time()))
                    return
        except:
            pass
    
    # Проверяем журнал на ошибки за последний час
    errors = os.popen("journalctl -u bingx-bot --since '1 hour ago' --no-pager 2>/dev/null | grep -c 'FAILURE\\|exit-code\\|IndentationError\\|SyntaxError'").read().strip()
    if errors and int(errors) > 3:
        send_alert(
            f"⚠️ <b>BOT CRASH LOOP</b>\n"
            f"Ошибок за час: {errors}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    print(f"[HEALTH] OK — bot active, no issues")

if __name__ == "__main__":
    check()
