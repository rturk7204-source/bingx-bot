"""Сканер рынка. Собирает топ-N кандидатов по фильтрам."""
import time
from statistics import mean
from ..core import exchange as ex
from ..core.config import (MIN_VOLUME_USD, MAX_SPREAD_PCT, FUNDING_BLACKOUT_MIN)

EXCLUDE = {'BTC','ETH','BNB','SOL','XRP','USDC','DAI','TUSD','USDT','BUSD','FDUSD'}

def get_universe(limit=50):
    """Топ N USDT-перпов по объёму, исключая стейблы и крупняк (он отдельно)."""
    r = ex.get_ticker()
    if r.get('code') != 0:
        return []
    rows = []
    for t in r.get('data', []):
        sym = t.get('symbol', '')
        if not sym.endswith('-USDT'):
            continue
        base = sym.replace('-USDT', '')
        if base in EXCLUDE:
            continue
        # пропускаем токенизированные акции/металлы (NCCO*, NCSK*, CHF*, EUR* и т.п. с USD внутри)
        if 'USD' in base.replace('USDT', '') or len(base) > 15:
            continue
        try:
            vol = float(t['quoteVolume'])
            px = float(t['lastPrice'])
            if vol < MIN_VOLUME_USD or px == 0:
                continue
            rows.append({
                'symbol': sym,
                'price': px,
                'change_24h': float(t['priceChangePercent']),
                'volume_usd': vol,
                'high_24h': float(t['highPrice']),
                'low_24h': float(t['lowPrice']),
            })
        except (KeyError, ValueError):
            continue
    rows.sort(key=lambda x: -x['volume_usd'])
    return rows[:limit]

def get_btc_context():
    """Тренд BTC и ETH для общего контекста."""
    btc = ex.get_ticker('BTC-USDT')
    eth = ex.get_ticker('ETH-USDT')
    ctx = {}
    if btc.get('code') == 0:
        ctx['btc_24h'] = float(btc['data']['priceChangePercent'])
        ctx['btc_price'] = float(btc['data']['lastPrice'])
    if eth.get('code') == 0:
        ctx['eth_24h'] = float(eth['data']['priceChangePercent'])
        ctx['eth_price'] = float(eth['data']['lastPrice'])
    return ctx

def klines(symbol, interval='1h', limit=50):
    """Свечи с правильным порядком (старые → новые)."""
    r = ex.get_klines(symbol, interval, limit)
    if r.get('code') != 0:
        return []
    data = r['data']
    if not data:
        return []
    if float(data[0]['time']) > float(data[-1]['time']):
        data = data[::-1]
    return [{
        'ts': int(x['time']),
        'o': float(x['open']),
        'h': float(x['high']),
        'l': float(x['low']),
        'c': float(x['close']),
        'v': float(x['volume'])
    } for x in data]

def funding_info(symbol):
    """Funding rate + интервал + минут до следующей выплаты."""
    r = ex.get_funding(symbol)
    if r.get('code') != 0:
        return None
    d = r.get('data')
    if isinstance(d, list):
        d = d[0] if d else None
    if not d:
        return None
    rate = float(d.get('lastFundingRate', 0)) * 100
    interval_h = int(d.get('fundingIntervalHours', 8))
    next_ts = int(d.get('nextFundingTime', 0))
    mins_to_next = (next_ts - int(time.time() * 1000)) / 60000 if next_ts else None
    return {
        'rate_pct': rate,
        'daily_pct': rate * (24 / interval_h),
        'interval_h': interval_h,
        'mins_to_next': mins_to_next
    }

def in_funding_blackout(symbol):
    """True если только что прошёл funding (последние FUNDING_BLACKOUT_MIN минут)."""
    f = funding_info(symbol)
    if not f or f['mins_to_next'] is None:
        return False
    elapsed = f['interval_h'] * 60 - f['mins_to_next']
    return 0 <= elapsed <= FUNDING_BLACKOUT_MIN

def orderbook_pressure(symbol):
    """bid/ask соотношение по топ-10 уровням. >1 — покупатели, <1 — продавцы."""
    r = ex.get_depth(symbol, 20)
    if r.get('code') != 0:
        return None
    bids = sum(float(b[1]) for b in r['data']['bids'][:10])
    asks = sum(float(a[1]) for a in r['data']['asks'][:10])
    if asks == 0:
        return None
    return bids / asks

def spread_pct(symbol):
    """Спред в %."""
    r = ex.get_depth(symbol, 5)
    if r.get('code') != 0:
        return None
    try:
        bid = float(r['data']['bids'][0][0])
        ask = float(r['data']['asks'][0][0])
        return (ask - bid) / bid * 100
    except (IndexError, KeyError, ValueError):
        return None


MAJORS = ['BTC-USDT', 'ETH-USDT', 'BNB-USDT', 'XRP-USDT', 'SOL-USDT']


def get_majors():
    """Крупняк — анализируем отдельно на 1h таймфрейме."""
    rows = []
    for sym in MAJORS:
        r = ex.get_ticker(sym)
        if r.get('code') != 0:
            continue
        d = r.get('data', [])
        t = d[0] if isinstance(d, list) and d else d
        if not t:
            continue
        try:
            rows.append({
                'symbol': sym,
                'price': float(t['lastPrice']),
                'change_24h': float(t['priceChangePercent']),
                'volume_usd': float(t['quoteVolume']),
                'high_24h': float(t['highPrice']),
                'low_24h': float(t['lowPrice']),
            })
        except (KeyError, ValueError):
            continue
    return rows
