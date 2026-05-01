#!/usr/bin/env python3
"""
BingX Universal Transfer wrapper — production-ready.

Основной факт (после BingX Spot Upgrade 2025-10-16):
- FUND = основной кошелёк, его видит Perp futures как margin source
- SPOT = изолированный кошелёк для spot trading
- type=SPOT_PFUTURES переводит SPOT → FUND (perp margin автоматически растёт)

Использование:
    from bingx_transfer import transfer_usdt, get_wallet_balances

    balances = get_wallet_balances()
    # {'spot': 3.07, 'fund': 250.27, 'perp_equity': 268.22}

    ok, tran_id = transfer_usdt(0.5, direction='spot_to_perp')
    # переведёт 0.5 USDT с SPOT на FUND (доступно как perp margin)
"""
import os
import time
import hmac
import hashlib
import json
import logging
from urllib.parse import urlencode
from pathlib import Path
import requests

log = logging.getLogger("bingx_transfer")

# Load .env
_env_path = Path("/root/bingx-bot/.env")
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

API_KEY = os.environ.get("BINGX_API_KEY", "")
SECRET = os.environ.get("BINGX_SECRET_KEY", "")
BASE = "https://open-api.bingx.com"

TRANSFER_ENDPOINT = "/openApi/api/v3/post/asset/transfer"

# Верифицированные рабочие типы
DIRECTIONS = {
    "spot_to_perp": "SPOT_PFUTURES",     # ✅ TESTED: SPOT → FUND (perp margin растёт)
    "perp_to_spot": "PFUTURES_SPOT",     # обратный
    "fund_to_perp": "FUND_PFUTURES",     # FUND → perp (если FUND ≠ perp kошелёк)
    "perp_to_fund": "PFUTURES_FUND",
    "spot_to_fund": "SPOT_FUND",
    "fund_to_spot": "FUND_SPOT",
}


def _sign(params: dict) -> str:
    qs = urlencode(sorted(params.items()))
    sig = hmac.new(SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + "&signature=" + sig


def transfer_usdt(amount: float, direction: str = "spot_to_perp",
                  asset: str = "USDT", dry_run: bool = False) -> tuple:
    """
    Выполняет transfer между кошельками.

    Args:
        amount: сумма (для USDT округляется до 2 знаков)
        direction: 'spot_to_perp' | 'perp_to_spot' | и т.д. из DIRECTIONS
        asset: тикер (USDT по умолчанию)
        dry_run: если True, не выполняет запрос

    Returns:
        (success: bool, tran_id_or_error: str)
    """
    if direction not in DIRECTIONS:
        return False, f"invalid direction: {direction}"
    if amount <= 0:
        return False, f"invalid amount: {amount}"
    if not API_KEY or not SECRET:
        return False, "no API key / secret"

    ttype = DIRECTIONS[direction]
    amount = round(float(amount), 2)

    params = {
        "type": ttype,
        "asset": asset,
        "amount": amount,
        "timestamp": int(time.time() * 1000),
        "recvWindow": 5000,
    }

    log.info(f"transfer {direction} ({ttype}): {amount} {asset}")

    if dry_run:
        log.info(f"[DRY-RUN] would POST {TRANSFER_ENDPOINT}")
        return True, "dry-run"

    try:
        url = f"{BASE}{TRANSFER_ENDPOINT}?{_sign(params)}"
        r = requests.post(url, headers={"X-BX-APIKEY": API_KEY}, timeout=15)

        if r.status_code != 200:
            log.error(f"HTTP {r.status_code}: {r.text[:200]}")
            return False, f"HTTP {r.status_code}"

        try:
            data = r.json()
        except Exception:
            log.error(f"non-JSON response: {r.text[:200]}")
            return False, "non-JSON response"

        # Успех: либо {tranId}, либо {data: {tranId}}, либо {code: 0}
        tran_id = data.get("tranId")
        if not tran_id and isinstance(data.get("data"), dict):
            tran_id = data["data"].get("tranId")

        if tran_id:
            log.info(f"✅ transfer OK, tranId={tran_id}")
            return True, str(tran_id)

        if data.get("code") == 0:
            log.info(f"✅ transfer OK (code=0)")
            return True, "code0"

        # Неявный успех — у нас есть прецедент, когда ответ пустой но баланс менялся
        # Безопаснее сверять по балансам в caller
        if not data.get("code") and not data.get("msg"):
            log.warning(f"empty response but HTTP 200 — verify balance manually")
            return True, "empty-ok"

        log.error(f"transfer failed: {data}")
        return False, json.dumps(data)[:200]

    except Exception as e:
        log.exception(f"transfer exception")
        return False, str(e)


def get_wallet_balances(asset: str = "USDT") -> dict:
    """
    Возвращает dict с балансами: {'spot': 3.07, 'fund': 250.27, 'perp_balance': 250, 'perp_avail': 0.8, 'perp_equity': 268.22}
    """
    result = {"spot": 0.0, "fund": 0.0, "perp_balance": 0.0,
              "perp_avail": 0.0, "perp_equity": 0.0}

    paths = [
        ("/openApi/spot/v1/account/balance", "spot"),
        ("/openApi/fund/v1/account/balance", "fund"),
        ("/openApi/swap/v2/user/balance", "perp"),
    ]

    for path, key in paths:
        params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
        url = f"{BASE}{path}?{_sign(params)}"
        try:
            r = requests.get(url, headers={"X-BX-APIKEY": API_KEY}, timeout=10).json()
            if key == "spot":
                balances = r.get("data", {}).get("balances", [])
                u = next((b for b in balances if b["asset"] == asset), None)
                if u:
                    result["spot"] = float(u["free"])
            elif key == "fund":
                assets = r.get("data", {}).get("assets", [])
                u = next((a for a in assets if a["asset"] == asset), None)
                if u:
                    result["fund"] = float(u["free"])
            else:
                b = r.get("data", {}).get("balance", {})
                result["perp_balance"] = float(b.get("balance", 0))
                result["perp_avail"] = float(b.get("availableMargin", 0))
                result["perp_equity"] = float(b.get("equity", 0))
        except Exception as e:
            log.error(f"balance fetch {key}: {e}")

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "balance":
        b = get_wallet_balances()
        print(f"SPOT:  {b['spot']:.4f} USDT")
        print(f"FUND:  {b['fund']:.4f} USDT")
        print(f"PERP:  balance={b['perp_balance']:.4f}  avail={b['perp_avail']:.4f}  equity={b['perp_equity']:.4f}")
    elif len(sys.argv) > 3 and sys.argv[1] == "transfer":
        amount = float(sys.argv[2])
        direction = sys.argv[3]
        dry = "--apply" not in sys.argv
        ok, info = transfer_usdt(amount, direction, dry_run=dry)
        print(f"{'✅' if ok else '❌'} {info}")
    else:
        print("usage:")
        print(f"  {sys.argv[0]} balance")
        print(f"  {sys.argv[0]} transfer 0.5 spot_to_perp          # dry-run")
        print(f"  {sys.argv[0]} transfer 0.5 spot_to_perp --apply  # live")
        print(f"\nDirections: {list(DIRECTIONS.keys())}")
