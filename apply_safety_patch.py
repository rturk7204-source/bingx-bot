#!/usr/bin/env python3
"""
Safety patch: защита от split-execution (спот куплен, перп не открыт из-за timeout/API lock).

Для каждого бота:
  1. open_short_perp: timeout=30s + retry 3 раза
  2. cmd_enter: если perp_res["code"] != 0 → АВТО-ОТКАТ (продать спот обратно)

Применяется к: arb_bot.py, arb_bot2.py, arb_bot3.py, arb_bot4.py, arb_bot5.py, arb_bot6.py
Пропускает файлы которые уже пропатчены (ищет маркер PATCH_V1_ANTI_SPLIT).
"""
import os, re, sys, shutil
from datetime import datetime
from pathlib import Path

BOT_DIR = Path("/root/bingx-bot")
BOTS = ["arb_bot.py", "arb_bot2.py", "arb_bot3.py", "arb_bot4.py", "arb_bot5.py", "arb_bot6.py"]

PATCH_MARKER = "# PATCH_V1_ANTI_SPLIT"

# Новый open_short_perp с retry
NEW_OPEN_SHORT = '''# PATCH_V1_ANTI_SPLIT
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
'''

# Новый fallback в cmd_enter — откат если перп не открылся
# Заменяет блок "!!! СПОТ КУПЛЕН, ПЕРП НЕ ОТКРЫТ !!!"
ROLLBACK_BLOCK = '''    if perp_res.get("code") != 0:
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
'''

# Старый блок который надо заменить (регулярка — несколько форм существуют)
OLD_BLOCK_PATTERNS = [
    # Форма 1 (arb_bot3 style): f-string "Продай {spot_qty:.6f} LYN/RIVER вручную"
    re.compile(
        r'    if perp_res\.get\("code"\) != 0:\n'
        r'        log\.error\(f"ОШИБКА ПЕРПА: \{perp_res\}"\)\n'
        r'        log\.error\("!!! СПОТ КУПЛЕН, ПЕРП НЕ ОТКРЫТ — ПОЗИЦИЯ НЕ НЕЙТРАЛЬНА !!!"\)\n'
        r'        log\.error\(f"    Продай \{spot_qty:\.6f\} \w+ вручную или запусти --exit"\)\n'
        r'        return\n',
        re.MULTILINE
    ),
    # Форма 2 (arb_bot2 new): с {TOKEN} f-string
    re.compile(
        r'    if perp_res\.get\("code"\) != 0:\n'
        r'        log\.error\(f"ОШИБКА ПЕРПА: \{perp_res\}"\)\n'
        r'        log\.error\("!!! СПОТ КУПЛЕН, ПЕРП НЕ ОТКРЫТ — ПОЗИЦИЯ НЕ НЕЙТРАЛЬНА !!!"\)\n'
        r'        log\.error\(f"    Продай \{spot_qty:\.6f\} \{TOKEN\} вручную или запусти --exit"\)\n'
        r'        return\n',
        re.MULTILINE
    ),
]

# Старый open_short_perp
OLD_OPEN_SHORT_PAT = re.compile(
    r'def open_short_perp\(notional\):\n'
    r'    price = get_mark_price\(\)\n'
    r'    if price <= 0:\n'
    r'        return \{"code": -1\}\n'
    r'    qty = round\(notional / price, 4\)\n'
    r'    d = _post\("/openApi/swap/v2/trade/order",\n'
    r'              \{"symbol": SYMBOL, "side": "SELL", "positionSide": "SHORT",\n'
    r'               "type": "MARKET", "quantity": str\(qty\)\}\)\n'
    r'    log\.info\(f"Перп SHORT \{qty\} @ ~\$\{price:\.[46]f\}: code=\{d\.get\(\'code\'\)\} \{d\.get\(\'msg\',\'\'\)\}"\)\n'
    r'    return d\n',
    re.MULTILINE
)


def patch_file(path: Path) -> str:
    """Возвращает статус: 'patched' / 'already_patched' / 'skipped_no_match' / 'error'."""
    try:
        src = path.read_text()
    except Exception as e:
        return f"error: cannot read ({e})"

    if PATCH_MARKER in src:
        return "already_patched"

    # 1. Заменяем open_short_perp на версию с retry
    if not OLD_OPEN_SHORT_PAT.search(src):
        return "skipped: open_short_perp pattern not found"
    new_src = OLD_OPEN_SHORT_PAT.sub(NEW_OPEN_SHORT.rstrip() + "\n", src)

    # 2. Заменяем блок "СПОТ КУПЛЕН, ПЕРП НЕ ОТКРЫТ" на откат
    matched = False
    for pat in OLD_BLOCK_PATTERNS:
        if pat.search(new_src):
            new_src = pat.sub(ROLLBACK_BLOCK, new_src)
            matched = True
            break
    if not matched:
        return "skipped: error block pattern not found"

    # Sanity: синтаксис должен быть валидным
    try:
        import ast
        ast.parse(new_src)
    except SyntaxError as e:
        return f"error: syntax broken after patch ({e})"

    # Backup + запись
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".prepatch_{ts}")
    shutil.copy2(path, backup)
    path.write_text(new_src)
    return f"patched (backup: {backup.name})"


def main():
    print(f"Safety patch v1 — anti-split-execution")
    print(f"Target dir: {BOT_DIR}")
    print("=" * 60)
    for bot in BOTS:
        p = BOT_DIR / bot
        if not p.exists():
            print(f"  [SKIP] {bot:15s} — не существует")
            continue
        result = patch_file(p)
        mark = "✓" if "patched" in result and "already" not in result else ("=" if "already" in result else "✗")
        print(f"  [{mark}] {bot:15s} — {result}")
    print("=" * 60)
    print("Готово. Для отката: `cp <bot>.py.prepatch_* <bot>.py`")


if __name__ == "__main__":
    main()
