# Disaster Recovery — BingX ARB Bot

Время восстановления **<30 минут** на чистом VPS.

## Что нужно для восстановления

| Артефакт | Где хранится |
|----------|--------------|
| Код | `git@github.com:rturk7204-source/bingx-bot.git` (private) |
| State (trades, balance_history, hedge_health, pause, …) | `git@github.com:rturk7204-source/bingx-bot-state.git` (private) — `state_latest.tar.gz` |
| Секреты (.env: API ключи, TG token) | **только в голове / 1Password** — НЕ в git |
| ML модели | `/root/bingx-bot/models/` — отдельный backup через `backup_models.sh` |

---

## Сценарий 1: VPS жив, повредились state-файлы

`safe_io` восстанавливает автоматически из `.bak.*`. Если оба испорчены:

```bash
cd /root/bingx-bot
ls -la state/*.bak.* trades.json.bak.* | tail -20

# восстановить конкретный файл из последнего .bak
LATEST=$(ls -t state/hedge_health.json.bak.* | head -1)
cp "$LATEST" state/hedge_health.json
```

Из последнего локального backup-архива:

```bash
ls -t /root/bingx-state-backups/*.tar.gz | head -3
python3 state_backup.py restore --archive /root/bingx-state-backups/state_YYYYMMDD_HHMM.tar.gz
```

---

## Сценарий 2: VPS умер, поднимаем новый

### 1. Установка базы (5 мин)

```bash
apt update && apt install -y python3 python3-pip git curl
mkdir -p /root && cd /root

# SSH ключ для git (новый или восстановленный)
mkdir -p ~/.ssh && chmod 700 ~/.ssh
# скопируй приватный ключ в ~/.ssh/id_ed25519, добавь публичный в GitHub Settings → Deploy keys
chmod 600 ~/.ssh/id_ed25519

# Клонируем код и state
git clone git@github.com:rturk7204-source/bingx-bot.git
git clone git@github.com:rturk7204-source/bingx-bot-state.git
```

### 2. Восстанавливаем state (3 мин)

```bash
cd /root/bingx-bot
python3 state_backup.py restore --archive /root/bingx-bot-state/state_latest.tar.gz
```

### 3. Создаём .env (2 мин — ручной ввод секретов)

```bash
cat > /root/bingx-bot/.env <<EOF
BINGX_API_KEY=...
BINGX_SECRET_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
EOF
chmod 600 /root/bingx-bot/.env
```

### 4. Зависимости + проверка (5 мин)

```bash
cd /root/bingx-bot
pip install -r requirements.txt

# smoke-проверка API + state
python3 -c "from hedge_health import run_check; run_check()"
python3 watchdog.py
```

### 5. Cron (3 мин)

```bash
# Установить весь crontab из git (если хранишь его там) или вручную:
crontab -l > /tmp/old_cron.bak  # если что-то было
cat > /tmp/new_cron <<'EOF'
*/15 * * * * /root/bingx-bot/auto_enter.sh
*/30 * * * * cd /root/bingx-bot && python3 arb_bot.py --monitor >> logs/arb_bot1.log 2>&1
*/30 * * * * cd /root/bingx-bot && python3 arb_bot2.py --monitor >> logs/arb_bot2.log 2>&1
*/30 * * * * cd /root/bingx-bot && python3 arb_bot3.py --monitor >> logs/arb_bot3.log 2>&1
*/30 * * * * cd /root/bingx-bot && python3 arb_bot4.py --monitor >> logs/arb_bot4.log 2>&1
*/30 * * * * cd /root/bingx-bot && python3 arb_bot5.py --monitor >> logs/arb_bot5.log 2>&1
*/30 * * * * cd /root/bingx-bot && python3 arb_bot6.py --monitor >> logs/arb_bot6.log 2>&1
5 */4 * * * cd /root/bingx-bot && python3 rotation.py --apply >> logs/rotation.log 2>&1
20 * * * * cd /root/bingx-bot && python3 arb_tools.py topup >> logs/arb_topup.log 2>&1
15,45 * * * * cd /root/bingx-bot && python3 arb_tools.py sync >> logs/arb_sync.log 2>&1
*/5 * * * * cd /root/bingx-bot && python3 hedge_health.py >> logs/hedge_health_cron.log 2>&1
*/10 * * * * cd /root/bingx-bot && python3 watchdog.py >> logs/watchdog_cron.log 2>&1
0 * * * * cd /root/bingx-bot && python3 state_backup.py local >> logs/state_backup_cron.log 2>&1
0 3 * * * cd /root/bingx-bot && python3 state_backup.py remote >> logs/state_backup_cron.log 2>&1
EOF
crontab /tmp/new_cron
```

### 6. Проверка (2 мин)

```bash
# Все процессы должны запуститься в течение 15 минут
tail -f logs/hedge_health.log logs/watchdog.log
```

---

## Сценарий 3: Бот в безопасном режиме после Block 2 (safe_mode=True)

**Эта ситуация — НЕ disaster, но описана здесь для полноты:**

```bash
cd /root/bingx-bot
# Посмотреть почему перешёл в safe-mode
python3 arb_tools.py --protection-status

# Проверить что условия нормализовались
python3 hedge_health.py

# Если ок — снять safe-mode
python3 arb_tools.py --resume
```

---

## Настройка remote backup (одноразово)

Для включения ежедневного push state в приватный GitHub-репозиторий:

### 1. Создай приватный репо на GitHub

- name: `bingx-bot-state`
- visibility: **Private**
- НЕ инициализируй с README — пустой

### 2. SSH-ключ на VPS (если нет прямого write-доступа)

```bash
ssh-keygen -t ed25519 -C "bingx-vps-backup" -f /root/.ssh/bingx_state_key -N ""
cat /root/.ssh/bingx_state_key.pub
# Скопируй вывод и добавь в GitHub:
# Settings → Deploy keys → Add deploy key → ✓ Allow write access
```

Настрой SSH как alias:

```bash
cat >> /root/.ssh/config <<EOF
Host bingx-state
  HostName github.com
  User git
  IdentityFile /root/.ssh/bingx_state_key
  IdentitiesOnly yes
EOF
chmod 600 /root/.ssh/config
```

### 3. Пропиши URL в бот

```bash
echo "git@bingx-state:rturk7204-source/bingx-bot-state.git" > /root/bingx-bot/.state_backup_repo
```

### 4. Проверь вручную

```bash
cd /root/bingx-bot && python3 state_backup.py remote
tail -20 logs/state_backup.log
# Должно быть: "remote: pushed backup ..."
```

### 5. Добавь в cron (ежедневно в 03:00 UTC)

```bash
( crontab -l 2>/dev/null; \
  echo "0 3 * * * cd /root/bingx-bot && python3 state_backup.py remote >> logs/state_backup_cron.log 2>&1" \
) | crontab -
```

---

## Чек-лист после восстановления

- [ ] `crontab -l` показывает 14 строк
- [ ] `python3 watchdog.py` без ошибок
- [ ] `python3 hedge_health.py` все T1-T6 OK
- [ ] `state/pause.json` — нет активных пауз
- [ ] `state/protection.json` — `safe_mode=False`
- [ ] BingX API: `python3 -c "from arb_bot import get_perp_balance; print(get_perp_balance())"`
- [ ] Telegram: получено приветственное `[WD] watchdog start` сообщение в течение 10 минут

## Контакт-точки в коде

- Heartbeat-окна и пороги — `watchdog.py` (HEARTBEATS, STALE_LOCK_SEC, DISK_*)
- Что бэкапится — `state_backup.py` (INCLUDE_FILES, INCLUDE_GLOBS)
- Атомарная запись — `safe_io.py` (используется в hedge_health, rotation, auto_balance)
