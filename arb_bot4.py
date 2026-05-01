#!/usr/bin/env python3
import env_loader  # noqa: F401  (auto-loads .env)
"""
Funding Rate Arbitrage Bot — APR-USDT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ИЗОЛЯЦИЯ ОТ SMC-БОТА (bot.py):
  ✓ Символ:    APR-USDT (не входит в SYMBOLS бота)
  ✓ Стейт:     arb_state4.json  (бот пишет trades.json)
  ✓ Лог:       arb_bot4.log     (бот пишет bot.log)
  ✓ Никаких обращений к файлам SMC-бота
  ✓ Один API-ключ — разные символы → нет нейтрализации позиций

Команды:
  python3 arb_bot.py            → статус
  python3 arb_bot.py --enter    → открыть позицию
  python3 arb_bot.py --monitor  → проверка (cron каждые 30 мин)
  python3 arb_bot.py --exit     → закрыть всё
"""

import os, sys, time, json, hmac, hashlib, argparse, requests, logging
from datetime import datetime, timezone
from urllib.parse import urlencode

# ─────────────────────────────────────────────────────────
#  КОНФИГУРАЦИЯ
# ─────────────────────────────────────────────────────────

# SMC-бот торгует: BTC ETH SOL SUI DOGE ADA XRP LINK BNB HYPE
# ARB-бот торгует: APR-USDT  ← нет пересечений

SYMBOL      = "APR-USDT"
TOKEN       = SYMBOL.replace("-USDT", "")  # автоматический label, синхронен с SYMBOL
SPOT_BUDGET = 80.0    # USDT на спот (compact v2: notional $80 = margin × 3x)
PERP_MARGIN = 27.0    # USDT маржи (3x плечо → notional $80)
LEVERAGE    = 3       # 3x → notional $80 = совпадает со спотом → delta=0

# P2-E Hysteresis thresholds
MIN_RATE    = 0.00030      # вход при rate ≥ 0.030%/8ч
EXIT_RATE   = -0.00010     # выход при rate ≤ −0.010%/8ч (мёртвая зона между)
# P2-F Slippage guards
MAX_SLIPPAGE_ENTER = 0.005 # 0.5% — отказ от входа при высоком slippage
MAX_SLIPPAGE_EXIT  = 0.010 # 1.0% — алерт при выходе, но выход всё равно выполняется
BAD_PERIODS = 5       # Плохих периодов подряд → принудительный выход
MARGIN_WARN = 0.50    # Предупреждение при марже < 50%
MARGIN_EXIT = 0.30    # Аварийный выход при марже < 30%

BOT_DIR    = "/root/bingx-bot"
STATE_FILE = f"{BOT_DIR}/arb_state4.json"   # НЕ trades.json !
LOG_FILE   = f"{BOT_DIR}/arb_bot4.log"      # НЕ bot.log !
BASE_URL   = "https://open-api.bingx.com"

# ─────────────────────────────────────────────────────────
#  ЗАГРУЗКА КЛЮЧЕЙ (из .env SMC-бота — тот же аккаунт BingX)
# ─────────────────────────────────────────────────────────

with open(f"{BOT_DIR}/.env") as _f:
    for _line in _f:
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

API_KEY    = os.getenv("BINGX_API_KEY", "")
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "")

# ─────────────────────────────────────────────────────────
#  ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ARB4] %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("arb4")

# ─────────────────────────────────────────────────────────
#  API
# ─────────────────────────────────────────────────────────

def _sign(p):
    q = urlencode(p)
    return hmac.new(SECRET_KEY.encode(), q.encode(), hashlib.sha256).hexdigest()

def _ts():
    return int(time.time() * 1000)

def _get(path, params=None):
    p = params or {}
    p["timestamp"] = _ts()
    p["signature"] = _sign(p)
    try:
        r = requests.get(f"{BASE_URL}{path}", params=p,
                         headers={"X-BX-APIKEY": API_KEY}, timeout=10)
        return r.json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}

def _post(path, params=None):
    p = params or {}
    p["timestamp"] = _ts()
    p["signature"] = _sign(p)
    try:
        r = requests.post(f"{BASE_URL}{path}", params=p,
                          headers={"X-BX-APIKEY": API_KEY}, timeout=10)
        return r.json()
    except Exception as e:
        return {"code": -1, "msg": str(e)}

# ─────────────────────────────────────────────────────────
#  СТЕЙТ
# ─────────────────────────────────────────────────────────


TG_TOKEN = os.getenv("TG_BOT_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TG_CHAT_ID", "") or os.getenv("TELEGRAM_CHAT_ID", "")
def tg_send(text):
    if not TG_TOKEN or not TG_CHAT:
        log.warning(f"TG not configured: {text}")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": text}, timeout=5)
    except Exception as e:
        log.error(f"TG error: {e}")

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"position_open": False}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

# ─────────────────────────────────────────────────────────
#  РЫНОЧНЫЕ ДАННЫЕ
# ─────────────────────────────────────────────────────────

def get_premium():
    d = _get("/openApi/swap/v2/quote/premiumIndex", {"symbol": SYMBOL})
    if d.get("code") == 0 and d.get("data"):
        return d["data"]
    return {}

def get_funding_rate():
    return float(get_premium().get("lastFundingRate", 0))

def get_mark_price():
    return float(get_premium().get("markPrice", 0))

def get_next_funding_time():
    ts = int(get_premium().get("nextFundingTime", 0)) / 1000
    if ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")
    return "N/A"

def get_spot_usdt():
    d = _get("/openApi/spot/v1/account/balance")
    if d.get("code") == 0:
        for b in d.get("data", {}).get("balances", []):
            if b.get("asset") == "USDT":
                return float(b.get("free", 0))
    return 0.0

def get_spot_token():
    token = SYMBOL.split("-")[0]
    d = _get("/openApi/spot/v1/account/balance")
    if d.get("code") == 0:
        for b in d.get("data", {}).get("balances", []):
            if b.get("asset") == token:
                return float(b.get("free", 0))
    return 0.0

def get_futures_usdt():
    d = _get("/openApi/swap/v2/user/balance")
    if d.get("code") == 0:
        bal = d.get("data", {}).get("balance", [])
        lst = bal if isinstance(bal, list) else [bal]
        for b in lst:
            if b.get("asset") == "USDT":
                return float(b.get("availableMargin", 0))
    return 0.0

def get_perp_position():
    d = _get("/openApi/swap/v2/user/positions", {"symbol": SYMBOL})
    if d.get("code") == 0:
        for p in d.get("data", []):
            if abs(float(p.get("positionAmt", 0))) > 0:
                return p
    return None

# ─────────────────────────────────────────────────────────
#  ОРДЕРА
# ─────────────────────────────────────────────────────────


def get_funding_income(since_ms):
    d = _get("/openApi/swap/v2/user/income", {
        "symbol": SYMBOL, "incomeType": "FUNDING_FEE",
        "startTime": str(since_ms), "endTime": str(_ts()), "limit": "50",
    })
    if d.get("code") == 0:
        entries = d.get("data") or []
        if isinstance(entries, list) and entries:
            total = sum(float(e.get("income", 0)) for e in entries)
            log.info(f"  Income API: {len(entries)} записей, сумма ${total:+.4f}")
            return total
        return 0.0
    log.warning(f"  Income API недоступен: {d.get('code')} {d.get('msg','')}")
    return 0.0

def set_leverage():
    d = _post("/openApi/swap/v2/trade/leverage",
              {"symbol": SYMBOL, "side": "SHORT", "leverage": str(LEVERAGE)})
    ok = d.get("code") == 0
    log.info(f"Плечо {LEVERAGE}x: {'OK' if ok else 'ОШИБКА — ' + str(d)}")
    return ok

def buy_spot(usdt):
    d = _post("/openApi/spot/v1/trade/order",
              {"symbol": SYMBOL, "side": "BUY", "type": "MARKET",
               "quoteOrderQty": str(round(usdt, 2))})
    log.info(f"Спот BUY ${usdt}: code={d.get('code')} {d.get('msg','')}")
    return d

def sell_spot(qty):
    d = _post("/openApi/spot/v1/trade/order",
              {"symbol": SYMBOL, "side": "SELL", "type": "MARKET",
               "quantity": str(round(qty, 6))})
    log.info(f"Спот SELL {qty:.6f}: code={d.get('code')} {d.get('msg','')}")
    return d

# PATCH_V1_ANTI_SPLIT
def _post_with_retry(path, params, attempts=3, timeout=30):
    """POST с retry и увеличенным timeout для критичных операций (открытие/закрытие перпа)."""
    import requests as _rq
    last = {"code": -1, "msg": "no attempts"}
    for i in range(1, attempts + 1):
        p = dict(params)
        p["timestamp"] = _ts()
        p["signature"] = _sign(p)
        try:
            r = _rq.post(f"{BASE_URL}{path}", params=p,
                         headers={"X-BX-APIKEY": API_KEY}, timeout=timeout)
            last = r.json()
        except Exception as e:
            last = {"code": -1, "msg": f"attempt {i} network error: {e}"}
        if last.get("code") == 0:
            return last
        # код 109400 = API временно заблокирован биржей — retry бесполезен
        if last.get("code") == 109400:
            return last
        if i < attempts:
            import time as _t; _t.sleep(2)
    return last


def open_short_perp(notional):
    price = get_mark_price()
    if price <= 0:
        return {"code": -1, "msg": "no price"}
    qty = round(notional / price, 4)
    d = _post_with_retry("/openApi/swap/v2/trade/order", {
        "symbol": SYMBOL, "side": "SELL", "positionSide": "SHORT",
        "type": "MARKET", "quantity": str(qty),
    }, attempts=3, timeout=30)
    log.info(f"Перп SHORT {qty} @ ~${price:.6f}: code={d.get('code')} {d.get('msg','')}")
    return d

def close_short_perp():
    pos = get_perp_position()
    if not pos:
        log.warning("Перп-позиция не найдена")
        return {"code": 0}
    qty = abs(float(pos.get("positionAmt", 0)))
    d = _post("/openApi/swap/v2/trade/order",
              {"symbol": SYMBOL, "side": "BUY", "positionSide": "SHORT",
               "type": "MARKET", "quantity": str(round(qty, 4))})
    log.info(f"Закрытие перп {qty}: code={d.get('code')} {d.get('msg','')}")
    return d

# ─────────────────────────────────────────────────────────
#  КОМАНДЫ
# ─────────────────────────────────────────────────────────

def cmd_status():
    s     = load_state()
    rate  = get_funding_rate()
    price = get_mark_price()
    next_t = get_next_funding_time()

    print("=" * 58)
    print(f"  ARB4 BOT — {SYMBOL}  |  {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"  [SMC торгует BTC ETH SOL SUI DOGE ADA XRP LINK BNB HYPE]")
    print(f"  [ARB4 торгует: APR-USDT ← клон #4]")
    print("=" * 58)
    print(f"  Rate: {rate*100:+.4f}%/8ч  (~{rate*3*365*100:.1f}% APY)")
    print(f"  Цена: ${price:.4f}  |  Выплата: {next_t}")
    print()

    if s.get("position_open"):
        pos  = get_perp_position() or {}
        mnow = float(pos.get("margin", PERP_MARGIN))
        upnl = float(pos.get("unrealizedProfit", 0))
        liq  = float(pos.get("liquidationPrice", 0))
        mpct = (mnow / PERP_MARGIN) * 100
        sq   = s.get("spot_qty", 0)
        spnl = sq * price - sq * s.get("entry_price", price)
        warn = " ⚠️ НИЗКАЯ" if mpct < MARGIN_WARN * 100 else " ✅"

        print(f"  ✅ ПОЗИЦИЯ ОТКРЫТА  (с {s.get('entry_time','')})")
        print(f"  Спот LONG : {sq:.6f} APR  PnL ${spnl:+.4f}")
        print(f"  Перп SHORT: notional ~${s.get('spot_budget',SPOT_BUDGET):.0f}")
        print(f"  Маржа     : ${mnow:.2f} ({mpct:.1f}%){warn}")
        print(f"  Перп PnL  : ${upnl:+.4f}  |  Liq: ${liq:.4f}")
        print(f"  Нетто     : ${spnl+upnl:+.4f}")
        print()
        print(f"  Выплат: {s.get('payments_received',0)}  Заработано: ${s.get('total_earned_usdt',0):.4f}")
        print(f"  Плохих периодов: {s.get('bad_periods',0)}/{BAD_PERIODS}")
    else:
        print(f"  ⏸  Позиция не открыта")
        if rate >= MIN_RATE:
            print(f"  ✅ Условие входа выполнено — запусти --enter")
        else:
            print(f"  ❌ Rate {rate*100:.4f}% < порога {MIN_RATE*100:.2f}%")
    print("=" * 58)



# === P2-F: Pre-trade slippage check ===
def check_slippage(side, usdt_amount=None, token_amount=None):
    """
    Simulate a market order against the current spot orderbook.
    side: "BUY" (consume asks) or "SELL" (consume bids)
    usdt_amount:  for BUY — total USDT to spend
    token_amount: for SELL — total tokens to sell
    Returns: dict(ok=bool, slippage_pct=float, avg_price=float, best_price=float, reason=str)
    """
    try:
        d = _get("/openApi/spot/v1/market/depth", {"symbol": SYMBOL, "limit": 20})
        ob = d.get("data", {}) if isinstance(d, dict) else {}
        if side == "BUY":
            levels = ob.get("asks", [])
            levels = sorted([(float(p), float(q)) for p, q in levels], key=lambda x: x[0])
        else:
            levels = ob.get("bids", [])
            levels = sorted([(float(p), float(q)) for p, q in levels], key=lambda x: -x[0])
        if not levels:
            return {"ok": False, "slippage_pct": 0, "avg_price": 0, "best_price": 0,
                    "reason": "empty orderbook"}
        best = levels[0][0]
        filled_tokens = 0.0
        filled_usdt   = 0.0
        if side == "BUY":
            remaining = float(usdt_amount)
            for price, qty in levels:
                level_usdt = price * qty
                if level_usdt >= remaining:
                    filled_tokens += remaining / price
                    filled_usdt   += remaining
                    remaining = 0
                    break
                filled_tokens += qty
                filled_usdt   += level_usdt
                remaining     -= level_usdt
            if remaining > 0:
                return {"ok": False, "slippage_pct": 99.0, "avg_price": 0, "best_price": best,
                        "reason": f"тонкая книга: {remaining:.2f} USDT не покрыты top-20"}
            avg = filled_usdt / filled_tokens
            slip = (avg - best) / best
        else:
            remaining = float(token_amount)
            for price, qty in levels:
                if qty >= remaining:
                    filled_tokens += remaining
                    filled_usdt   += remaining * price
                    remaining = 0
                    break
                filled_tokens += qty
                filled_usdt   += qty * price
                remaining     -= qty
            if remaining > 0:
                return {"ok": False, "slippage_pct": 99.0, "avg_price": 0, "best_price": best,
                        "reason": f"тонкая книга: {remaining:.4f} токенов не покрыты top-20"}
            avg = filled_usdt / filled_tokens
            slip = (best - avg) / best
        return {"ok": True, "slippage_pct": slip, "avg_price": avg, "best_price": best,
                "reason": "ok"}
    except Exception as e:
        return {"ok": False, "slippage_pct": 0, "avg_price": 0, "best_price": 0,
                "reason": f"API error: {e}"}

def cmd_enter():
    # # ENTER_LOCK_V1  Prevent concurrent --enter (cron + manual)
    import fcntl
    _lock_path = "/tmp/arb_bot4.enter.lock"
    _lock_fh = open(_lock_path, "w")
    try:
        fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error(f"ENTER уже выполняется другим процессом (lock: {_lock_path}). Выход.")
        return
    _lock_fh.write(str(os.getpid()) + "\n"); _lock_fh.flush()
    s = load_state()
    if s.get("position_open"):
        log.warning("Позиция уже открыта!")
        return

    rate = get_funding_rate()
    log.info(f"Rate: {rate*100:+.4f}%")
    if rate < MIN_RATE:
        log.error(f"Rate {rate*100:.4f}% < {MIN_RATE*100:.2f}%. Вход отменён.")
        return

    spot_usdt  = get_spot_usdt()
    perp_usdt  = get_futures_usdt()
    log.info(f"Балансы → Спот USDT: {spot_usdt:.2f} | Фьючерс USDT: {perp_usdt:.2f}")

    if spot_usdt < SPOT_BUDGET:
        log.error(f"Мало USDT на споте: {spot_usdt:.2f} < {SPOT_BUDGET}")
        log.error("▶ BingX → Assets → Transfer → Spot Wallet ← Fund Wallet")
        return
    if perp_usdt < PERP_MARGIN:
        log.error(f"Мало USDT на фьючерсах: {perp_usdt:.2f} < {PERP_MARGIN}")
        log.error("▶ BingX → Assets → Transfer → Futures Wallet ← Fund Wallet")
        return

    if not set_leverage():
        return

    price = get_mark_price()
    # P2-F slippage gate
    sl = check_slippage("BUY", usdt_amount=SPOT_BUDGET)
    if not sl["ok"]:
        log.error(f"Slippage check FAIL: {sl['reason']}. Вход отменён.")
        return
    if sl["slippage_pct"] > MAX_SLIPPAGE_ENTER:
        log.error(f"Slippage {sl['slippage_pct']*100:.3f}% > {MAX_SLIPPAGE_ENTER*100:.2f}% — книга тонкая. Вход отменён.")
        return
    log.info(f"Slippage check OK: {sl['slippage_pct']*100:+.3f}% на ${SPOT_BUDGET} (best=${sl['best_price']:.6f}, avg=${sl['avg_price']:.6f})")

    log.info(f"Покупаем APR: ${SPOT_BUDGET} USDT (symbol=APR-USDT)...")
    if buy_spot(SPOT_BUDGET).get("code") != 0:
        log.error("Ошибка спот ордера. Выход.")
        return

    time.sleep(3)
    spot_qty = get_spot_token()
    log.info(f"Куплено: {spot_qty:.6f} {TOKEN}")
    if spot_qty <= 0:
        log.error("Токены не зачислены! Проверь вручную.")
        return

    log.info(f"Открываем SHORT на перпе (notional ${SPOT_BUDGET})...")
    perp_res = open_short_perp(SPOT_BUDGET)
    if perp_res.get("code") != 0:
        log.error(f"ОШИБКА ПЕРПА: {perp_res}")
        log.error("!!! СПОТ КУПЛЕН, ПЕРП НЕ ОТКРЫТ — ДЕЛАЮ АВТО-ОТКАТ !!!")
        tg_send(f"⚠️ {SYMBOL}: перп не открылся ({perp_res.get('msg','')[:50]}). Откат спота...")
        # PATCH_V1_ANTI_SPLIT: авто-продажа спота для устранения дельта-риска
        try:
            import time as _t; _t.sleep(2)
            sell_qty = get_spot_token()
            if sell_qty > 0:
                sell_r = _post("/openApi/spot/v1/trade/order", {
                    "symbol": SYMBOL, "side": "SELL", "type": "MARKET",
                    "quantity": str(round(sell_qty * 0.9999, 4)),
                })
                if sell_r.get("code") == 0:
                    log.info(f"✓ Спот откачен: продано {sell_qty:.4f} {SYMBOL.split('-')[0]}")
                    tg_send(f"✅ {SYMBOL}: откат выполнен, спот продан.")
                else:
                    log.error(f"❌ ОТКАТ НЕ УДАЛСЯ: {sell_r}")
                    tg_send(f"🚨 {SYMBOL}: ПЕРП не открыт И СПОТ не откачен! Ручное вмешательство.")
        except Exception as _e:
            log.error(f"Exception при откате: {_e}")
            tg_send(f"🚨 {SYMBOL}: exception при откате: {_e}")
        return

    state = {
        "position_open":     True,
        "symbol":            SYMBOL,
        "entry_time":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "entry_rate":        rate,
        "entry_price":       price,
        "spot_qty":          spot_qty,
        "spot_budget":       SPOT_BUDGET,
        "perp_margin":       PERP_MARGIN,
        "leverage":          LEVERAGE,
        "payments_received": 0,
        "total_earned_usdt": 0.0,
        "bad_periods":       0,
        "last_check":        datetime.now(timezone.utc).isoformat(),
    }
    save_state(state)

    log.info("=" * 58)
    log.info("✅ DELTA-NEUTRAL ПОЗИЦИЯ ОТКРЫТА")
    log.info(f"   Спот LONG  : {spot_qty:.6f} APR (${SPOT_BUDGET:.0f})")
    log.info(f"   Перп SHORT : notional ~${SPOT_BUDGET:.0f} (маржа ${PERP_MARGIN:.0f}, {LEVERAGE}x)")
    log.info(f"   Rate       : {rate*100:+.4f}%/8ч → ожидаемо ~${SPOT_BUDGET*rate:.4f}/выплату")
    log.info(f"   Следующая выплата: {get_next_funding_time()}")
    log.info("=" * 58)


def cmd_monitor():
    s = load_state()
    if not s.get("position_open"):
        log.info("Позиция не открыта — monitor пропущен")
        return

    rate  = get_funding_rate()
    price = get_mark_price()
    pos   = get_perp_position() or {}
    mnow  = float(pos.get("margin", PERP_MARGIN))
    upnl  = float(pos.get("unrealizedProfit", 0))
    liq   = float(pos.get("liquidationPrice", 0))
    mpct  = (mnow / PERP_MARGIN) * 100
    sq    = s.get("spot_qty", 0)
    spnl  = sq * price - sq * s.get("entry_price", price)

    log.info("─" * 58)
    log.info(f"MONITOR | {SYMBOL} | rate={rate*100:+.4f}% | price=${price:.4f} | next={get_next_funding_time()}")
    log.info(f"  Маржа: ${mnow:.2f} ({mpct:.1f}%) | PnL перп=${upnl:+.4f} | Liq=${liq:.4f}")
    log.info(f"  PnL спот: ${spnl:+.4f} | Нетто: ${spnl+upnl:+.4f}")
    log.info(f"  Заработано: ${s.get('total_earned_usdt',0):.4f} за {s.get('payments_received',0)} выплат")

    # === ДЕТЕКТОР ЛИКВИДАЦИИ ===
    if s.get("position_open") and mnow==0 and liq==0 and upnl==0:
        log.warning(f"ЛИКВИДАЦИЯ ОБНАРУЖЕНА! Перп {SYMBOL} закрыт биржей. Продаём спот...")
        tg_send(f"🚨 ЛИКВИДАЦИЯ {SYMBOL}! Перп ликвидирован. Продаю спот для фиксации.")
        try:
            sq_sell=s.get("spot_qty",0)
            if sq_sell>0:
                qty_str=str(round(sq_sell*0.999,4))
                r=_post("/openApi/spot/v1/trade/order",{"symbol":SYMBOL,"side":"SELL","type":"MARKET","quantity":qty_str})
                if r.get("code")==0:
                    log.info(f"Спот {SYMBOL} продан: {qty_str}")
                    tg_send(f"✅ Спот {SYMBOL} продан ({qty_str} шт). Позиция закрыта.")
                else:
                    log.error(f"Ошибка продажи спота: {r}")
                    tg_send(f"❌ Не удалось продать спот {SYMBOL}: {r.get('msg','')}")
            s["position_open"]=False
            s["liquidated"]=True
            s["liquidation_time"]=datetime.now(timezone.utc).isoformat()
            save_state(s)
        except Exception as e:
            log.error(f"Ошибка при обработке ликвидации: {e}")
            tg_send(f"❌ Ошибка обработки ликвидации {SYMBOL}: {e}")
        return
    # === КОНЕЦ ДЕТЕКТОРА ===

    if mpct < MARGIN_EXIT * 100:
        log.error(f"МАРЖА {mpct:.1f}% < {MARGIN_EXIT*100:.0f}% — АВАРИЙНЫЙ ВЫХОД!")
        save_state(s)
        cmd_exit()
        return

    if mpct < MARGIN_WARN * 100:
        log.warning(f"Маржа {mpct:.1f}% — рассмотри пополнение фьючерсного кошелька")

    # Отслеживаем низкую/отрицательную ставку — НЕ закрываем, только алерт
    bad = s.get("bad_periods", 0)
    if rate < EXIT_RATE:
        bad += 1
        s["bad_periods"] = bad
        log.warning(f"Rate {rate*100:.4f}% < {EXIT_RATE*100:.2f}%. Плохих периодов: {bad}/{BAD_PERIODS}")
        if bad >= BAD_PERIODS:
            # Sticky exit с netto-guard: закрываем ТОЛЬКО если реально в минусе
            sq_check = s.get("spot_qty", 0)
            ep_check = s.get("entry_price", price)
            spot_pnl_check = sq_check * (price - ep_check)
            earned_check = s.get("total_earned_usdt", 0)
            netto_check = spot_pnl_check + upnl + earned_check
            NETTO_EXIT_THRESHOLD = -2.0
            if netto_check < NETTO_EXIT_THRESHOLD:
                msg = f"🔴 APR: bad={bad}/{BAD_PERIODS} И нетто ${netto_check:.2f} < ${NETTO_EXIT_THRESHOLD}. АВТОЗАКРЫТИЕ."
                log.error(msg)
                tg_send(msg)
                cmd_exit()
                return
            else:
                msg = f"⚠️ APR: ставка ниже порога {BAD_PERIODS} периодов. Rate={rate*100:.4f}%, нетто ${netto_check:.2f} — держим, только алерт."
                tg_send(msg)
                log.warning(f"Алерт отправлен. Нетто положительное — держим позицию.")
                s["bad_periods"] = 0
                bad = 0
    elif bad > 0:
        log.info(f"Rate восстановился. Сброс счётчика.")
        s["bad_periods"] = 0

    s["last_check"] = datetime.now(timezone.utc).isoformat()
    save_state(s)
    log.info("─" * 58)


def cmd_exit():
    s = load_state()
    if not s.get("position_open"):
        log.info("Позиция не открыта")
        return

    log.info("Закрываем ARB-позицию...")
    log.info("1/2 Закрываем перп SHORT...")
    perp_ok = close_short_perp().get("code") == 0
    time.sleep(3)

    # P2-F slippage warning at exit (non-blocking)
    try:
        _qty_for_check = float(spot_qty) if 'spot_qty' in dir() and spot_qty else get_spot_token()
        slx = check_slippage("SELL", token_amount=_qty_for_check)
        if slx["ok"] and slx["slippage_pct"] > MAX_SLIPPAGE_EXIT:
            log.warning(f"Exit slippage {slx['slippage_pct']*100:.3f}% > {MAX_SLIPPAGE_EXIT*100:.2f}% — книга тонкая, но закрываем всё равно")
            tg_send(f"⚠️ {SYMBOL} exit: slippage {slx['slippage_pct']*100:.2f}%")
    except Exception as _e:
        pass

    log.info("2/2 Продаём APR на споте...")
    qty = get_spot_token()
    spot_ok = True
    if qty > 0:
        spot_ok = sell_spot(qty).get("code") == 0
    else:
        log.warning("APR не найден на споте")

    s["position_open"] = False
    s["exit_time"]     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    s["perp_close_ok"] = perp_ok
    s["spot_close_ok"] = spot_ok
    save_state(s)

    log.info("=" * 58)
    log.info(f"ЗАКРЫТО | Перп: {'✅' if perp_ok else '❌'} | Спот: {'✅' if spot_ok else '❌'}")
    log.info(f"Заработано фандинга: ${s.get('total_earned_usdt',0):.4f} за {s.get('payments_received',0)} выплат")
    log.info("=" * 58)
    if not perp_ok:
        log.error("❌ Перп не закрыт! BingX → Futures → Positions → Close manually")
    if not spot_ok:
        log.error("❌ Спот не продан! BingX → Spot → Orders → Sell manually")

# ─────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not API_KEY:
        print("API ключ не найден!")
        sys.exit(1)

    ap = argparse.ArgumentParser()
    ap.add_argument("--enter",   action="store_true")
    ap.add_argument("--monitor", action="store_true")
    ap.add_argument("--exit",    action="store_true")
    ap.add_argument("--status",  action="store_true")
    args = ap.parse_args()

    if   args.enter:   cmd_enter()
    elif args.monitor: cmd_monitor()
    elif args.exit:    cmd_exit()
    else:              cmd_status()
