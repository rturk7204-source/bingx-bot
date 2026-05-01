# Auto-Rotation (Block 5.x)

Автоматическая ежечасная ротация — раз в час cron вызывает `rotation.py --apply`,
который перебирает кандидатов, выбирает лучший swap (если он улучшает APR на
≥70% и проходит фильтры), и выполняет его без участия человека. TG-нотификация
приходит после каждого запуска.

## Зачем

- При глушении интернета пользователь может не увидеть TG-алерт о слабой паре.
- Ручной запуск ротации = задержка в часы, теряем funding-периоды.
- Все safety guards уже встроены в `rotation.py` (pause_check, basis, slippage,
  Kelly sizing, graveyard cooldown), так что автоматизация безопасна.

## Установка на VPS

```bash
# 1. Pull
cd /root/bingx-bot && git pull

# 2. Прогнать тесты
python3 run_all_tests.py
# Ожидается: 58 passed

# 3. Убедиться что auto_rotate.sh исполняемый
chmod +x /root/bingx-bot/auto_rotate.sh

# 4. Установить cron — раз в час, в 0 минут
( crontab -l 2>/dev/null | grep -v auto_rotate.sh; \
  echo "0 * * * * /root/bingx-bot/auto_rotate.sh >> /var/log/bingx-rotation-cron.log 2>&1" \
) | crontab -

# 5. Проверить что встал
crontab -l | grep auto_rotate

# 6. (опционально) дёрнуть руками для теста
/root/bingx-bot/auto_rotate.sh
tail -30 /var/log/bingx-rotation.log
```

## Как это работает

1. Cron вызывает `auto_rotate.sh` каждый час в `:00`.
2. Скрипт:
   - проверяет lock-файл `/tmp/bingx-rotation.lock` (защита от параллельных запусков),
   - вызывает `python3 rotation.py --apply`,
   - всё пишет в `/var/log/bingx-rotation.log`,
   - если rc != 0 — отдельный TG-алерт «auto_rotate.sh упал rc=...».
3. Внутри `rotation.py --apply`:
   - `pause_check.can_act()` — если SAFE-MODE или GLOBAL pause → skip + TG.
   - `analyze_rotation()` — выбирает решение или None.
   - При наличии решения: pre-flight (basis, spot balance), exit, lifetime_pnl.record_exit,
     graveyard, patch SYMBOL, enter с retry, post-entry verify.
   - TG-сводка после успеха.

## Контроль

```bash
# Логи последнего часа
tail -100 /var/log/bingx-rotation.log

# Cron-логи (от самого cron)
tail -50 /var/log/bingx-rotation-cron.log

# Lifetime PnL отчёт
python3 /root/bingx-bot/tools/show_lifetime.py

# Только lifetime
python3 /root/bingx-bot/lifetime_pnl.py
```

## Остановка

```bash
# Снять cron временно
crontab -l | grep -v auto_rotate.sh | crontab -

# Или включить SAFE-MODE (rotation.py сам пропустит, cron можно оставить):
echo "manual freeze" > /root/bingx-bot/state/safe_mode
# Снять:
rm /root/bingx-bot/state/safe_mode
```

## Файлы

- `auto_rotate.sh` — обёртка для cron (lock, log, TG-fallback при крэше).
- `rotation.py` — основная логика (без изменений интерфейса).
- `lifetime_pnl.py` — накопление PnL через ротации.
- `lifetime_pnl.json` — runtime data, попадает в state_backup.
- `tools/show_lifetime.py` — расширенный отчёт current+lifetime.

## Тесты

`tests/test_lifetime_pnl.py` — 11 тестов:
- запись/накопление/изоляция между ботами,
- битый JSON не валит cron,
- история обрезается до 200 записей на бот,
- atomic write (без .tmp residue),
- schema persisted correctly.

## RPO/RTO

- `lifetime_pnl.json` пишется атомарно (tmp + rename).
- Бэкапится через `state_backup.py` (Block 4) — RPO ≤ 1ч.
- Если файл потерян — теряется только история; реальные позиции не страдают,
  они в `arb_state{N}.json`.
