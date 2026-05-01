# DR Runbook — bingx-bot

> Disaster recovery procedure для полного восстановления флота из бэкапа.
> Цель: <5 минут от чистого VPS до работающих ботов.

## Что бэкапится

`state_backup.py` сохраняет:

- `state/*` — все файлы state-каталога (hedge_health.json, pause_*, safe_mode, watchdog_alerts.json)
- `arb_bot*_state.json`, `arb_state*.json` — состояния каждого бота
- `trades.json`, `balance_history.json`, `blacklist.json` — журнал и блок-лист
- `rl_states.json`, `pairs_state.json`, `oi_history.json` — RL и парные данные
- `feature_importance_history.json`

Что **НЕ** бэкапится:

- `.env` / `env_loader.py` — секреты (API keys, TG token). Восстанавливаются вручную из менеджера паролей.
- `models/` — большие, отдельный `backup_models.sh`
- Код — он в git (rturk7204-source/bingx-bot)

## Локации бэкапов

1. **Local hourly:** `/root/bingx-state-backups/state_YYYYMMDD_HHMM.tar.gz` (хранится 48ч)
2. **Remote daily:** приватный git repo `rturk7204-source/bingx-bot-state` → клон в `/root/bingx-bot-state/`
   - `state_latest.tar.gz` — pointer на свежий
   - `state_YYYYMMDD_HHMM.tar.gz` — daily snapshots (14 дней)

## Сценарий 1: VPS жив, state-файл повреждён

Один бот не стартует, остальные работают. Битый JSON в `arb_bot3_state.json`.

```bash
cd /root/bingx-bot
systemctl stop arb_bot3
# Посмотреть что в latest backup
python3 state_backup.py list
# Восстановить только этот файл
python3 state_backup.py restore --dry-run        # показать что будет
python3 state_backup.py restore                  # полное восстановление
systemctl start arb_bot3
journalctl -u arb_bot3 -n 30 --no-pager
```

`safe_io.safe_read_json` уже умеет авто-восстановление из `.bak.{ts}` — этот шаг нужен только если все бэкапы битые.

## Сценарий 2: Полный реcтор из бэкапа на новый VPS

Старый сервер мёртв, поднимаем чистый Ubuntu.

### Шаг 1: System prep (~30s)

```bash
apt update && apt install -y python3 python3-pip git
```

### Шаг 2: Код (~30s)

```bash
cd /root && git clone git@github.com:rturk7204-source/bingx-bot.git bingx-bot
cd /root/bingx-bot && pip3 install -r requirements.txt
```

### Шаг 3: Секреты (~30s)

```bash
cat > .env << 'EOF'
BINGX_API_KEY=...
BINGX_SECRET_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
EOF
# env_loader.py из менеджера паролей или скопировать со старого VPS
```

### Шаг 4: State из remote backup (~30s)

```bash
cd /root && git clone git@github.com:rturk7204-source/bingx-bot-state.git bingx-bot-state
cd /root/bingx-bot
python3 state_backup.py restore --archive /root/bingx-bot-state/state_latest.tar.gz
```

### Шаг 5: Smoke-тесты (~30s)

```bash
cd /root/bingx-bot && python3 run_all_tests.py
# Должно: "RESULT: 43 passed, 0 failed"
```

### Шаг 6: Запустить ботов (~30s)

```bash
# Systemd units должны быть в репо или скопированы вручную
systemctl daemon-reload
systemctl start arb_bot{1..6} arb_commander watchdog
systemctl status arb_bot{1..6} --no-pager
```

### Шаг 7: Verify в Telegram

`/report` → должен показать те же позиции что были до сбоя.

**Total: ~3 минуты от чистой машины до живого флота.**

## Сценарий 3: Telegram чат потерян, нужен ручной /resume

```bash
cd /root/bingx-bot
python3 -c "import pause_check as pc; print(pc.resume_all())"
# Удалит safe_mode, pause_global, все pause_bot*
```

## Сценарий 4: Нужно остановить весь флот сейчас (capital protection)

```bash
cd /root/bingx-bot
python3 -c "import pause_check as pc; pc.enter_safe_mode('manual emergency')"
# Все боты увидят safe_mode при следующем pre-flight (rotation pause check)
# Для немедленной остановки также:
systemctl stop arb_bot{1..6}
```

Resume:

```bash
python3 -c "import pause_check as pc; pc.clear_safe_mode(); print(pc.resume_all())"
systemctl start arb_bot{1..6}
```

## Сценарий 5: Один бот в loop / зависание

```bash
# 1. Pause только этот бот через pause_check
python3 -c "import pause_check as pc; pc.pause_bot(3, hours=24, reason='manual: loop')"
# 2. Hard restart
systemctl restart arb_bot3
# 3. Через 30 сек снять паузу (если решена проблема)
python3 -c "import pause_check as pc; import os; os.unlink(pc.pause_bot_path(3))"
```

## Smoke test: dr_smoke.sh

```bash
bash dr_smoke.sh
```

Проверяет:

1. ✓ `python3 run_all_tests.py` (все тесты зелёные)
2. ✓ `state_backup.py local` создаёт архив без ошибок
3. ✓ `state_backup.py list` читает архив
4. ✓ `state_backup.py restore --dry-run` парсит без падения
5. ✓ `pause_check.py` self-test проходит
6. ✓ Все ключевые модули импортируются без ошибок

Должен завершиться за <60 секунд.

## RTO / RPO

- **RTO (Recovery Time Objective):** <5 минут (manual recovery с remote backup)
- **RPO (Recovery Point Objective):**
  - Через `state_backup.py local` → ≤1 час (hourly cron)
  - Через `state_backup.py remote` → ≤24 часа (daily)

Для критичных событий (manual ручной снапшот перед опасным изменением) — запустить `python3 state_backup.py local` непосредственно.

## Контакты / эскалация

- BingX API: https://bingx.com/en/support/
- VPS provider: srv1499924 (через панель)
- Repo: rturk7204-source/bingx-bot (private), bingx-bot-state (private)
