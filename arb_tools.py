#!/usr/bin/env python3
import env_loader  # noqa: F401  (auto-loads .env)
"""
Авто-компаундинг + Телеграм уведомления для ARB ботов.
Крон: раз в неделю (компаундинг) + каждые 4 часа (отчёт).

Команды:
  --report    Отчёт по обеим позициям (в Телеграм + консоль)
  --compound  Доливает накопленный фандинг в позиции
"""
import os, sys, time, json, hmac, hashlib, requests, logging
from datetime import datetime, timezone
from urllib.parse import urlencode

BOT_DIR = "/root/bingx-bot"
BASE_URL = "https://open-api.bingx.com"

def _load_env():
    p = f"{BOT_DIR}/.env"
    if os.path.exists(p):
        with open(p) as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()

AK = os.getenv("BINGX_API_KEY", "")
SK = os.getenv("BINGX_SECRET_KEY", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

LOG_FILE = f"{BOT_DIR}/arb_compound.log"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [COMP] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("compound")

# ── API ──
def _sign(p):
    return hmac.new(SK.encode(), urlencode(list(p.items())).encode(), hashlib.sha256).hexdigest()
def _ts():
    return int(time.time() * 1000)
def _get(path, p=None):
    p = dict(p or {}); p["timestamp"] = _ts(); p["signature"] = _sign(p)
    try: return requests.get(f"{BASE_URL}{path}", params=p, headers={"X-BX-APIKEY": AK}, timeout=10).json()
    except: return {"code": -1}

def _post(path, p=None):
    p = dict(p or {}); p["timestamp"] = _ts(); p["signature"] = _sign(p)
    try: return requests.post(f"{BASE_URL}{path}", params=p, headers={"X-BX-APIKEY": AK}, timeout=10).json()
    except Exception as e: return {"code": -1, "msg": str(e)}

# ── Telegram ──
def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        log.warning("Телеграм не настроен")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML"},
            timeout=10)
    except Exception as e:
        log.error(f"Телеграм ошибка: {e}")

# ── Данные ──
def get_balance():
    d = _get("/openApi/swap/v2/user/balance")
    if d.get("code") == 0:
        b = d["data"]["balance"]
        return {
            "balance": float(b["balance"]),
            "equity": float(b["equity"]),
            "rp": float(b["realisedProfit"]),
            "available": float(b["availableMargin"]),
        }
    return None

def load_state(n):
    """n=1 → arb_state.json (якорь RIVER)
       n=2..6 → arb_state{n}.json (ротация + клоны LYN/APR/PTB/SKYAI)"""
    path = f"{BOT_DIR}/arb_state.json" if n == 1 else f"{BOT_DIR}/arb_state{n}.json"
    if os.path.exists(path):
        with open(path) as f: return json.load(f)
    return {"position_open": False}

def get_rate(sym):
    d = _get("/openApi/swap/v2/quote/premiumIndex", {"symbol": sym})
    if d.get("code") == 0 and d.get("data"):
        return float(d["data"].get("lastFundingRate", 0))
    return 0.0

def get_price(sym):
    d = _get("/openApi/swap/v2/quote/premiumIndex", {"symbol": sym})
    if d.get("code") == 0 and d.get("data"):
        return float(d["data"].get("markPrice", 0))
    return 0.0

# ── ОТЧЁТ ──

SNAPSHOT_FILE = f"{BOT_DIR}/arb_rp_snapshot.json"

def load_snapshot():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE) as f: return json.load(f)
    return {"rp": 0, "time": ""}

def save_snapshot(rp):
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump({"rp": rp, "time": datetime.now(timezone.utc).isoformat()}, f)

def cmd_sync():
    """Синхронизирует заработок по обеим позициям через realisedProfit"""
    bal = get_balance()
    if not bal:
        log.error("Нет баланса"); return

    snap = load_snapshot()
    old_rp = snap["rp"]
    new_rp = bal["rp"]
    diff = new_rp - old_rp

    # Шесть ботов: arb_bot.py (n=1), arb_bot2.py..arb_bot6.py (n=2..6)
    states = {n: load_state(n) for n in range(1, 7)}

    # Считаем номиналы только открытых позиций
    budgets = {n: (states[n].get("spot_budget", 0) if states[n].get("position_open") else 0) for n in states}
    total_n = sum(budgets.values())

    if diff > 0 and total_n > 0:
        parts = []
        for n, bud in budgets.items():
            if bud <= 0:
                continue
            share = diff * (bud / total_n)
            st = states[n]
            st["total_earned_usdt"] = round(st.get("total_earned_usdt", 0) + share, 4)
            st["payments_received"] = st.get("payments_received", 0) + 1
            path = f"{BOT_DIR}/arb_state.json" if n == 1 else f"{BOT_DIR}/arb_state{n}.json"
            with open(path, "w") as f:
                json.dump(st, f, indent=2, ensure_ascii=False)
            parts.append(f"{st.get('symbol','?')} +${share:.4f}")

        save_snapshot(new_rp)
        log.info(f"Синхр: +${diff:.4f} → " + ", ".join(parts))
    elif diff == 0:
        log.info("Синхр: без изменений")
    else:
        # rp уменьшился (смк убыток или другое) — просто обновляем снимок
        save_snapshot(new_rp)
        log.info(f"Синхр: rp снизился на ${abs(diff):.4f} — обновлён снимок")

def cmd_report():
    # Шесть ботов: якорь + ротация + 4 клона (LYN/APR/PTB/SKYAI)
    states = [(n, load_state(n)) for n in range(1, 7)]
    bal = get_balance()

    lines = []
    lines.append("<b>ARB Отчёт</b>")
    lines.append(f"{datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}")
    lines.append("")

    total_earned = 0
    total_budget = 0

    tags = {1: "Якорь-RIVER", 2: "Ротация", 3: "Клон-LYN", 4: "Клон-APR", 5: "Клон-PTB", 6: "Клон-SKYAI"}
    for n, s in states:
        if not s.get("position_open"):
            continue
        sym = s["symbol"]
        rate = get_rate(sym)
        price = get_price(sym)
        earned = s.get("total_earned_usdt", 0)
        budget = s.get("spot_budget", 0) + s.get("perp_margin", 0)
        total_earned += earned
        total_budget += budget

        tag = tags.get(n, f"Бот-{n}")
        lines.append(f"<b>{sym}</b> [{tag}]")
        lines.append(f"  Ставка: {rate*100:+.4f}%")
        lines.append(f"  Номинал: ${s.get('spot_budget',0):.0f}")
        lines.append(f"  Заработано: ${earned:.4f}")
        lines.append("")

    if bal:
        lines.append(f"<b>Баланс:</b> ${bal['balance']:.2f}")
        lines.append(f"<b>Капитал:</b> ${bal['equity']:.2f}")
        lines.append(f"<b>Реализ. прибыль:</b> ${bal['rp']:.4f}")

    lines.append("")
    lines.append(f"<b>Общий доход ARB:</b> ${total_earned:.4f}")
    lines.append(f"<b>Общий бюджет:</b> ${total_budget:.0f}")

    # Проверяем алерты
    alerts = []
    if bal and bal["available"] < 20:
        alerts.append("Мало свободной маржи")
    for n, s in states:
        if s.get("position_open"):
            r = get_rate(s["symbol"])
            if r < 0.0001:
                alerts.append(f"{s['symbol']}: ставка критически низкая")

    if alerts:
        lines.append("")
        lines.append("<b>Алерты:</b>")
        for a in alerts:
            lines.append(f"  {a}")

    text = "\n".join(lines)
    print(text.replace("<b>","").replace("</b>",""))
    tg_send(text)
    log.info("Отчёт отправлен")

# ── КОМПАУНДИНГ ──
def cmd_compound():
    bal = get_balance()
    if not bal:
        log.error("Не удалось получить баланс")
        return

    available = bal["available"]
    rp = bal["rp"]

    log.info(f"Баланс: ${bal['balance']:.2f}, доступно: ${available:.2f}, прибыль: ${rp:.4f}")

    # Компаундим только если накопилось больше $5
    MIN_COMPOUND = 5.0
    if rp < MIN_COMPOUND:
        msg = f"Компаундинг: накоплено ${rp:.2f}, минимум ${MIN_COMPOUND}. Ждём."
        log.info(msg)
        tg_send(msg)
        return

    # Доливаем в якорную позицию (РИВЕР) — она стабильнее
    s1 = load_state(1)
    if not s1.get("position_open"):
        log.info("Якорная позиция не открыта — компаундинг пропущен")
        return

    sym = s1["symbol"]
    price = get_price(sym)
    if price <= 0:
        log.error("Не удалось получить цену")
        return

    # Проверяем маржу всех активных позиций — если где-то рискует, доливаем только в якорь
    MARGIN_LIMIT = 45.0
    risky = False
    for n in range(2, 7):
        sx = load_state(n)
        if sx.get("position_open"):
            mx = sx.get("margin_ratio", 0)
            if mx > MARGIN_LIMIT:
                risky = True
                log.info(f"Маржа {sx.get('symbol','?')} = {mx:.1f}% > {MARGIN_LIMIT}% — доливаем только в якорь")

    # Делим: 2/3 на спот, 1/3 на маржу
    SAFE_FREE_MARGIN = 90.0
    compound_amount = min(rp, max(0, available - SAFE_FREE_MARGIN))  # оставляем минимум $90 свободной маржи
    spot_add = round(compound_amount * 0.667, 2)
    margin_add = round(compound_amount * 0.333, 2)

    if compound_amount <= 0:
        deficit = SAFE_FREE_MARGIN - available
        msg = f"Компаундинг: доступно ${available:.2f}, до безопасного порога ${SAFE_FREE_MARGIN:.2f} не хватает ${deficit:.2f}. Ждём."
        log.info(msg)
        tg_send(msg)
        return

    log.info(f"Компаундинг ${compound_amount:.2f}: спот +${spot_add}, маржа +${margin_add}")

    msg = f"Авто-компаундинг: +${compound_amount:.2f} в {sym}\nСпот: +${spot_add}, Маржа: +${margin_add}"
    tg_send(msg)

    # Примечание: реальная доливка требует перевода на спот
    # и покупки токенов. Пока только уведомляем.
    # Полная автоматизация будет когда починим API переводов.
    log.info("Компаундинг: уведомление отправлено. Перевод вручную.")
    tg_send(f"Переведи ${spot_add:.2f} на спот и запусти:\npython3 /root/bingx-bot/arb_bot.py --topup {spot_add}")

# ─────────────────────────────────────────────────────────
#  P1-B: DELTA-DRIFT REBALANCER
# ─────────────────────────────────────────────────────────

DRIFT_THRESHOLD_PCT = 1.0  # % — если spot vs perp расходятся больше — ребалансим
DRIFT_MAX_ABS_USD = 5.0    # Максимальный абсолютный дисбаланс в USD, который мы терпим

def get_spot_balance(symbol):
    token = symbol.split("-")[0]
    d = _get("/openApi/spot/v1/account/balance")
    if d.get("code") == 0:
        for b in d.get("data", {}).get("balances", []):
            if b.get("asset") == token:
                return float(b.get("free", 0))
    return 0.0

def get_perp_position_qty(symbol):
    """Возвращает размер перп-позиции (положительный если SHORT, отрицательный если LONG)."""
    d = _get("/openApi/swap/v2/user/positions", {"symbol": symbol})
    if d.get("code") == 0:
        for p in d.get("data", []):
            amt = float(p.get("positionAmt", 0))
            if abs(amt) > 0:
                # positionSide SHORT → positionAmt может быть положительным, но это short
                if p.get("positionSide") == "SHORT":
                    return abs(amt)
                return amt
    return 0.0

def cmd_rebalance():
    """Проверяет delta-drift по всем открытым позициям и выравнивает если > threshold."""
    states = [(n, load_state(n)) for n in range(1, 7)]  # до 6 ботов
    total_adjusted = 0
    for n, s in states:
        if not s.get("position_open"):
            continue
        sym = s["symbol"]
        spot_qty = get_spot_balance(sym)
        perp_qty = get_perp_position_qty(sym)
        price = get_price(sym)
        if price <= 0 or spot_qty <= 0:
            continue

        drift_qty = spot_qty - perp_qty
        drift_usd = drift_qty * price
        drift_pct = abs(drift_qty) / spot_qty * 100 if spot_qty > 0 else 0

        log.info(f"[DRIFT] {sym}: spot={spot_qty:.4f}, perp_short={perp_qty:.4f}, drift={drift_qty:+.4f} ({drift_pct:.2f}%, ${drift_usd:+.2f})")

        if drift_pct < DRIFT_THRESHOLD_PCT and abs(drift_usd) < DRIFT_MAX_ABS_USD:
            continue  # в допуске

        # Ребаланс: если spot > perp_short → добавляем к perp short; если spot < perp_short → уменьшаем perp short
        if drift_qty > 0:
            # spot больше — наращиваем short
            qty_to_add = round(abs(drift_qty), 4)
            log.warning(f"[DRIFT] {sym}: наращиваю SHORT на {qty_to_add}")
            r = _post("/openApi/swap/v2/trade/order",
                      {"symbol": sym, "side": "SELL", "positionSide": "SHORT",
                       "type": "MARKET", "quantity": str(qty_to_add)})
            if r.get("code") == 0:
                total_adjusted += 1
                tg_send(f"⚖️ Drift-rebalance {sym}: +{qty_to_add} short (spot > perp)")
            else:
                log.error(f"[DRIFT] {sym} rebalance FAIL: {r}")
        else:
            # perp_short больше — уменьшаем short
            qty_to_cover = round(abs(drift_qty), 4)
            log.warning(f"[DRIFT] {sym}: прикрываю SHORT на {qty_to_cover}")
            r = _post("/openApi/swap/v2/trade/order",
                      {"symbol": sym, "side": "BUY", "positionSide": "SHORT",
                       "type": "MARKET", "quantity": str(qty_to_cover)})
            if r.get("code") == 0:
                total_adjusted += 1
                tg_send(f"⚖️ Drift-rebalance {sym}: -{qty_to_cover} short (perp > spot)")
            else:
                log.error(f"[DRIFT] {sym} rebalance FAIL: {r}")

        time.sleep(2)

    if total_adjusted == 0:
        log.info("[DRIFT] Все позиции в пределах допуска — ребаланс не нужен")
    else:
        log.info(f"[DRIFT] Ребаланс выполнен для {total_adjusted} позиций")

# ─────────────────────────────────────────────────────────
#  P1-C: PANIC KILL-SWITCH — закрыть ВСЕ позиции
# ─────────────────────────────────────────────────────────

def cmd_panic():
    """Последовательно закрывает все открытые позиции ARB-ботов."""
    log.error("🚨 PANIC MODE: закрываю все ARB позиции")
    tg_send("🚨 PANIC MODE активирован. Закрываю все позиции...")

    closed = []
    failed = []
    for n in range(1, 7):
        s = load_state(n)
        if not s.get("position_open"):
            continue
        sym = s["symbol"]
        log.info(f"[PANIC] Закрываю {sym}...")

        # 1. Закрываем перп SHORT
        perp_qty = get_perp_position_qty(sym)
        perp_ok = True
        if perp_qty > 0:
            r = _post("/openApi/swap/v2/trade/order",
                      {"symbol": sym, "side": "BUY", "positionSide": "SHORT",
                       "type": "MARKET", "quantity": str(round(perp_qty, 4))})
            perp_ok = r.get("code") == 0
            log.info(f"  Перп закрытие: code={r.get('code')} {r.get('msg','')}")
        time.sleep(2)

        # 2. Продаём спот
        spot_qty = get_spot_balance(sym)
        spot_ok = True
        if spot_qty > 0:
            sold = False
            for dec in [4, 2, 1, 0]:
                qty_str = str(round(spot_qty * 0.999, dec)) if dec > 0 else str(int(spot_qty * 0.999))
                r = _post("/openApi/spot/v1/trade/order",
                          {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": qty_str})
                log.info(f"  Спот SELL qty={qty_str}: code={r.get('code')}")
                if r.get("code") == 0:
                    sold = True; break
                time.sleep(1)
            spot_ok = sold

        # 3. Обновляем state
        s["position_open"] = False
        s["exit_time"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        s["panic_close"] = True
        path = f"{BOT_DIR}/arb_state.json" if n == 1 else f"{BOT_DIR}/arb_state{n}.json"
        with open(path, "w") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)

        if perp_ok and spot_ok:
            closed.append(sym)
        else:
            failed.append(f"{sym} (perp={'OK' if perp_ok else 'FAIL'}, spot={'OK' if spot_ok else 'FAIL'})")

        time.sleep(5)  # пауза 5 сек между ботами

    summary = f"PANIC завершён. Закрыто: {len(closed)}, ошибок: {len(failed)}"
    log.info(summary)
    msg = f"🚨 PANIC результат:\n✅ Закрыто: {', '.join(closed) if closed else 'нет'}\n"
    if failed:
        msg += f"❌ Ошибки: {', '.join(failed)}"
    tg_send(msg)

# ─────────────────────────────────────────────────────────
#  P1-D: AUTO-TOP-UP MARGIN при margin ratio < 60%
# ─────────────────────────────────────────────────────────

MARGIN_TOPUP_THRESHOLD_PCT = 60.0  # при margin ratio < 60% — пополняем
MARGIN_TOPUP_AMOUNT = 15.0          # USDT — размер разовой доливки
MARGIN_TOPUP_MAX_PER_DAY = 3        # максимум доливок в день на пару
TOPUP_HISTORY_FILE = f"{BOT_DIR}/arb_topup_history.json"

def load_topup_history():
    if os.path.exists(TOPUP_HISTORY_FILE):
        with open(TOPUP_HISTORY_FILE) as f: return json.load(f)
    return {}

def save_topup_history(h):
    with open(TOPUP_HISTORY_FILE, "w") as f: json.dump(h, f, indent=2)

def transfer_spot_to_perp(amount):
    """Перевод USDT: Spot (Fund) → Perpetual Futures через BingX API.
    Подтверждено на проде 29.04.2026: возвращает {tranId, transferId}.
    GET /openApi/api/v3/asset/transfer — это просмотр истории.
    POST /openApi/api/v3/post/asset/transfer — это исполнение.
    type: FUND_PFUTURES (spot->perp), PFUTURES_FUND (perp->spot).
    """
    r = _post("/openApi/api/v3/post/asset/transfer", {
        "type": "FUND_PFUTURES",
        "asset": "USDT",
        "amount": str(round(amount, 2)),
    })
    if isinstance(r, dict) and "tranId" in r:
        return {"ok": True, "tranId": r.get("tranId"), "transferId": r.get("transferId")}
    # ошибка вида {'code': ..., 'msg': ...}
    return {"ok": False, "raw": r}

def transfer_perp_to_spot(amount):
    """Обратный перевод: Perpetual → Spot (Fund). Для exit/rebalance."""
    r = _post("/openApi/api/v3/post/asset/transfer", {
        "type": "PFUTURES_FUND",
        "asset": "USDT",
        "amount": str(round(amount, 2)),
    })
    if isinstance(r, dict) and "tranId" in r:
        return {"ok": True, "tranId": r.get("tranId"), "transferId": r.get("transferId")}
    return {"ok": False, "raw": r}

def cmd_topup():
    """Проверяет маржу всех позиций и автоматически пополняет из Spot USDT если нужно."""
    hist = load_topup_history()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today not in hist:
        hist[today] = {}

    topped = []
    for n in range(1, 7):
        s = load_state(n)
        if not s.get("position_open"):
            continue
        sym = s["symbol"]

        d = _get("/openApi/swap/v2/user/positions", {"symbol": sym})
        if d.get("code") != 0:
            continue
        pos = None
        for p in d.get("data", []):
            if abs(float(p.get("positionAmt", 0))) > 0:
                pos = p; break
        if not pos:
            continue

        margin_now = float(pos.get("margin", 0))
        pm = s.get("perp_margin", 27)
        mpct = (margin_now / pm) * 100 if pm > 0 else 100

        log.info(f"[TOPUP] {sym}: margin ${margin_now:.2f}/{pm} = {mpct:.1f}%")

        if mpct >= MARGIN_TOPUP_THRESHOLD_PCT:
            continue

        # Лимит доливок в день
        done_today = hist[today].get(sym, 0)
        if done_today >= MARGIN_TOPUP_MAX_PER_DAY:
            log.warning(f"[TOPUP] {sym}: достигнут дневной лимит доливок ({done_today})")
            tg_send(f"⚠️ {sym}: margin {mpct:.1f}% < {MARGIN_TOPUP_THRESHOLD_PCT}% но уже {done_today} доливок сегодня — ручное вмешательство")
            continue

        # Проверяем что на Spot USDT достаточно
        bal = get_balance()
        if not bal:
            continue
        # Смотрим спот USDT
        d2 = _get("/openApi/spot/v1/account/balance")
        spot_usdt = 0
        if d2.get("code") == 0:
            for b in d2.get("data", {}).get("balances", []):
                if b.get("asset") == "USDT":
                    spot_usdt = float(b.get("free", 0))
        # Block 1: если spot < нужно — пробуем автоматически перевести из PERP/FUND.
        # Раньше любая нехватка = стоп и ручное вмешательство через BingX UI.
        if spot_usdt < MARGIN_TOPUP_AMOUNT:
            try:
                from auto_balance import ensure_spot_balance
                log.warning(f"[TOPUP] {sym}: spot=${spot_usdt:.2f} < ${MARGIN_TOPUP_AMOUNT} → пробую auto-transfer")
                ok_auto = ensure_spot_balance(MARGIN_TOPUP_AMOUNT, buffer=2.0)
                if ok_auto:
                    # Перечитываем spot после перевода
                    d2b = _get("/openApi/spot/v1/account/balance")
                    if d2b.get("code") == 0:
                        for b in d2b.get("data", {}).get("balances", []):
                            if b.get("asset") == "USDT":
                                spot_usdt = float(b.get("free", 0))
                    log.info(f"[TOPUP] auto-transfer OK → spot=${spot_usdt:.2f}")
            except Exception as e:
                log.error(f"[TOPUP] auto_balance error: {e}")
        if spot_usdt < MARGIN_TOPUP_AMOUNT:
            log.error(f"[TOPUP] Spot USDT ${spot_usdt:.2f} < ${MARGIN_TOPUP_AMOUNT} — доливка невозможна (auto-transfer тоже не спас)")
            tg_send(f"🔴 {sym}: margin {mpct:.1f}% критическая, но на Spot только ${spot_usdt:.2f} (auto-bal fail)")
            continue

        # Доливаем
        log.warning(f"[TOPUP] {sym}: доливаю ${MARGIN_TOPUP_AMOUNT} в Futures")
        r = transfer_spot_to_perp(MARGIN_TOPUP_AMOUNT)
        if r.get("code") == 0:
            hist[today][sym] = done_today + 1
            topped.append(f"{sym} +${MARGIN_TOPUP_AMOUNT}")
            tg_send(f"✅ Auto-top-up {sym}: margin {mpct:.1f}% → +${MARGIN_TOPUP_AMOUNT}")
        else:
            log.error(f"[TOPUP] Перевод FAIL: {r}")
            tg_send(f"❌ Auto-top-up {sym} FAIL: {r.get('msg','')}")

    save_topup_history(hist)
    if topped:
        log.info(f"[TOPUP] Выполнено: {', '.join(topped)}")
    else:
        log.info("[TOPUP] Никому не нужно")

# === Block 2: pause / resume / status ===========================
STATE_DIR = f"{BOT_DIR}/state"
SAFE_MODE_FILE = f"{STATE_DIR}/safe_mode"
PAUSE_GLOBAL = f"{STATE_DIR}/pause_global"

def cmd_pause(scope="global", hours=4, reason="manual"):
    """Пауза на вход новых позиций на N часов."""
    os.makedirs(STATE_DIR, exist_ok=True)
    from datetime import timedelta
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    payload = {"until": until.isoformat(), "reason": reason, "scope": "new_entries"}
    if scope == "global":
        with open(PAUSE_GLOBAL, "w") as f:
            json.dump(payload, f, indent=2)
        log.warning(f"[PAUSE] global new_entries until {until.strftime('%Y-%m-%d %H:%M UTC')}: {reason}")
        tg_send(f"⏸ PAUSE {hours}h: {reason}")
    else:
        # bot-specific
        try:
            n = int(scope)
            with open(f"{STATE_DIR}/pause_bot{n}", "w") as f:
                json.dump(payload, f, indent=2)
            log.warning(f"[PAUSE] bot{n} until {until.strftime('%Y-%m-%d %H:%M UTC')}")
            tg_send(f"⏸ bot{n} paused {hours}h: {reason}")
        except ValueError:
            log.error(f"[PAUSE] unknown scope: {scope}")

def cmd_resume():
    """Снимает safe-mode и все pause-файлы."""
    os.makedirs(STATE_DIR, exist_ok=True)
    removed = []
    if os.path.exists(SAFE_MODE_FILE):
        try:
            with open(SAFE_MODE_FILE) as f:
                info = json.load(f)
            log.warning(f"[RESUME] clearing safe-mode (entered {info.get('entered_at')}, reason: {info.get('reason')})")
        except Exception:
            pass
        os.remove(SAFE_MODE_FILE)
        removed.append("safe_mode")
    if os.path.exists(PAUSE_GLOBAL):
        os.remove(PAUSE_GLOBAL)
        removed.append("pause_global")
    for n in range(1, 7):
        p = f"{STATE_DIR}/pause_bot{n}"
        if os.path.exists(p):
            os.remove(p)
            removed.append(f"pause_bot{n}")
    if removed:
        msg = f"✅ RESUME: cleared {', '.join(removed)}"
        log.info(msg)
        tg_send(msg)
    else:
        log.info("[RESUME] nothing to clear (no active pause/safe-mode)")
        print("Ничего не очищено — пауз и safe-mode нет")

def cmd_status_protection():
    """Показать текущее состояние Block 2 защит (safe-mode, pauses)."""
    print("=== Block 2 Protection Status ===")
    if os.path.exists(SAFE_MODE_FILE):
        try:
            with open(SAFE_MODE_FILE) as f:
                info = json.load(f)
            print(f"🛑 SAFE-MODE active since {info.get('entered_at')}")
            print(f"   Reason: {info.get('reason')}")
        except Exception:
            print("🛑 SAFE-MODE file present (corrupted)")
    else:
        print("✅ safe-mode: clear")
    if os.path.exists(PAUSE_GLOBAL):
        try:
            with open(PAUSE_GLOBAL) as f:
                p = json.load(f)
            print(f"⏸ GLOBAL PAUSE until {p.get('until')} ({p.get('reason')})")
        except Exception:
            print("⏸ GLOBAL PAUSE (corrupted)")
    else:
        print("✅ global pause: clear")
    for n in range(1, 7):
        p = f"{STATE_DIR}/pause_bot{n}"
        if os.path.exists(p):
            try:
                with open(p) as f:
                    info = json.load(f)
                print(f"⏸ bot{n} paused until {info.get('until')} ({info.get('reason')})")
            except Exception:
                pass

if __name__ == "__main__":
    import argparse
    pa = argparse.ArgumentParser()
    pa.add_argument("--report", action="store_true")
    pa.add_argument("--compound", action="store_true")
    pa.add_argument("--sync", action="store_true")
    pa.add_argument("--rebalance", action="store_true", help="P1-B: Delta-drift rebalancer")
    pa.add_argument("--panic", action="store_true", help="P1-C: Panic kill-switch — закрыть ВСЁ")
    pa.add_argument("--topup", action="store_true", help="P1-D: Auto-top-up margin")
    pa.add_argument("--rotate-smart", action="store_true", help="P2-G/H + P3-I: Smart rotation (dry-run)")
    pa.add_argument("--apply", action="store_true", help="Apply rotation decision (use with --rotate-smart)")
    pa.add_argument("--pause", nargs="?", const="global", help="Block 2: pause new entries (--pause [global|1-6])")
    pa.add_argument("--pause-hours", type=int, default=4, help="Pause duration (default 4h)")
    pa.add_argument("--pause-reason", type=str, default="manual", help="Pause reason")
    pa.add_argument("--resume", action="store_true", help="Block 2: снять safe-mode и все паузы")
    pa.add_argument("--protection-status", action="store_true", help="Block 2: показать состояние защит")
    a = pa.parse_args()
    if a.compound: cmd_compound()
    elif a.sync: cmd_sync()
    elif a.rebalance: cmd_rebalance()
    elif a.panic: cmd_panic()
    elif a.topup: cmd_topup()
    elif a.rotate_smart:
        from rotation import cmd_rotate_smart
        cmd_rotate_smart(apply_changes=a.apply)
    elif a.resume: cmd_resume()
    elif a.protection_status: cmd_status_protection()
    elif a.pause: cmd_pause(scope=a.pause, hours=a.pause_hours, reason=a.pause_reason)
    elif a.report: cmd_report()
    else: cmd_report()
