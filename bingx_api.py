import hashlib
import hmac
import time
import requests
from urllib.parse import urlencode

class BingXAPI:
    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        self.base_url = 'https://open-api.bingx.com'

    def _request(self, method, endpoint, params=None, signed=True):
        if params is None:
            params = {}
        if signed:
            params['timestamp'] = int(time.time() * 1000)
            query_string = urlencode(params)
            signature = hmac.new(
                self.secret_key.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            params['signature'] = signature
        headers = {'X-BX-APIKEY': self.api_key}
        url = f"{self.base_url}{endpoint}"
        try:
            if method == 'GET':
                response = requests.get(url, params=params, headers=headers)
            elif method == 'POST':
                response = requests.post(url, params=params, headers=headers)
            elif method == 'DELETE':
                response = requests.delete(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get('code') != 0:
                print(f"[API] Full error response: {data}")
            return data
        except Exception as e:
            print(f"API Error: {e}")
            return None

    def get_ticker(self, symbol):
        endpoint = '/openApi/swap/v2/quote/ticker'
        params = {'symbol': symbol}
        result = self._request('GET', endpoint, params, signed=False)
        if result and isinstance(result, dict) and result.get('code') == 0:
            return result.get('data', {})
        return {}

    def get_klines(self, symbol, interval='1h', limit=100):
        endpoint = '/openApi/swap/v3/quote/klines'
        params = {'symbol': symbol, 'interval': interval, 'limit': limit}
        return self._request('GET', endpoint, params, signed=False)

    def get_balance(self):
        endpoint = '/openApi/swap/v2/user/balance'
        result = self._request('GET', endpoint)
        if result and isinstance(result, dict) and result.get('code') == 0:
            return result.get('data', {})
        return {}

    def get_positions(self):
        endpoint = '/openApi/swap/v2/user/positions'
        result = self._request('GET', endpoint)
        if result and isinstance(result, dict) and result.get('code') == 0:
            return result.get('data', [])
        return []

    def get_funding_rate(self, symbol):
        """Получаем текущий funding rate для фьючерсов"""
        endpoint = '/openApi/swap/v2/quote/premiumIndex'
        params = {'symbol': symbol}
        result = self._request('GET', endpoint, params, signed=False)
        if result and isinstance(result, dict) and result.get('code') == 0:
            data = result.get('data', {})
            if isinstance(data, list) and len(data) > 0:
                return float(data[0].get('lastFundingRate', 0))
            elif isinstance(data, dict):
                return float(data.get('lastFundingRate', 0))
        return 0.0


    def get_step_size(self, symbol):
        """Получаем минимальный шаг количества для пары"""
        try:
            endpoint = '/openApi/swap/v2/quote/contracts'
            result = self._request('GET', endpoint, {}, signed=False)
            if result and result.get('code') == 0:
                for contract in result.get('data', []):
                    if contract.get('symbol') == symbol:
                        return float(contract.get('tradeMinQuantity', 0.1))
        except: pass
        return 0.1

    def get_min_quantity(self, symbol):
        """Получаем минимальное количество для пары"""
        try:
            endpoint = '/openApi/swap/v2/quote/contracts'
            result = self._request('GET', endpoint, {}, signed=False)
            if result and result.get('code') == 0:
                for contract in result.get('data', []):
                    if contract.get('symbol') == symbol:
                        return float(contract.get('tradeMinQuantity', 0.1))
        except: pass
        return 0.1

    def round_quantity(self, symbol, quantity):
        """Округляем количество до допустимого шага с проверкой минимума"""
        try:
            step = self.get_step_size(symbol)
            min_qty = self.get_min_quantity(symbol)
            if step <= 0: return round(quantity, 4)
            import math
            rounded = math.floor(quantity / step) * step
            decimals = len(str(step).rstrip('0').split('.')[-1]) if '.' in str(step) else 0
            rounded = round(rounded, decimals)
            if rounded < min_qty:
                print(f"[API] {symbol}: qty {rounded} < min {min_qty} — увеличиваем до минимума")
                rounded = min_qty
            return rounded
        except:
            return round(quantity, 4)

    def set_leverage(self, symbol, leverage=10):
        """Устанавливаем плечо для пары"""
        try:
            endpoint = '/openApi/swap/v2/trade/leverage'
            params = {'symbol': symbol, 'side': 'LONG', 'leverage': leverage}
            self._request('POST', endpoint, params)
            params2 = {'symbol': symbol, 'side': 'SHORT', 'leverage': leverage}
            self._request('POST', endpoint, params2)
        except Exception as e:
            print(f"[LEVERAGE] Error setting leverage for {symbol}: {e}")

    def open_position(self, symbol, side, quantity, price=None, leverage=10):
        # Устанавливаем плечо перед открытием
        self.set_leverage(symbol, leverage)
        # Округляем количество до допустимого шага
        quantity = self.round_quantity(symbol, quantity)
        if quantity <= 0:
            print(f"[API] {symbol}: quantity too small after rounding")
            return None
        endpoint = '/openApi/swap/v2/trade/order'
        params = {'symbol': symbol, 'side': side, 'positionSide': 'LONG' if side == 'BUY' else 'SHORT', 'type': 'MARKET', 'quantity': quantity}
        return self._request('POST', endpoint, params)

    def close_position(self, symbol, side, quantity, price=None):
        # Округляем quantity как при открытии — иначе BingX вернёт ошибку
        quantity = self.round_quantity(symbol, quantity)
        if quantity <= 0:
            print(f'[API] {symbol}: close quantity too small after rounding')
            return None
        endpoint = '/openApi/swap/v2/trade/order'
        params = {'symbol': symbol, 'side': side, 'positionSide': 'LONG' if side == 'SELL' else 'SHORT', 'type': 'MARKET', 'quantity': quantity}
        print(f'[API] close_position {symbol} side={side} positionSide={params["positionSide"]} qty={quantity}')
        return self._request('POST', endpoint, params)

    def set_stop_loss(self, symbol, position_side, stop_price, quantity=None):
        """Выставляет стоп-лосс ордер на бирже для существующей позиции"""
        side = 'SELL' if position_side == 'LONG' else 'BUY'
        # Если quantity не передан, берём из текущей позиции
        if quantity is None:
            positions = self.get_positions()
            for pos in (positions or []):
                if pos.get('symbol') == symbol and pos.get('positionSide') == position_side:
                    quantity = abs(float(pos.get('positionAmt', 0) or 0))
                    break
        if not quantity or quantity <= 0:
            print(f'[API] set_stop_loss {symbol}: no position found')
            return None
        quantity = self.round_quantity(symbol, quantity)
        endpoint = '/openApi/swap/v2/trade/order'
        params = {
            'symbol': symbol,
            'side': side,
            'positionSide': position_side,
            'type': 'STOP_MARKET',
            'stopPrice': str(stop_price),
            'quantity': quantity,
            'workingType': 'MARK_PRICE'
        }
        print(f'[API] set_stop_loss {symbol} {position_side} stopPrice={stop_price} qty={quantity}')
        return self._request('POST', endpoint, params)

    def cancel_open_orders(self, symbol):
        """Отменяет все открытые ордера по символу"""
        endpoint = '/openApi/swap/v2/trade/allOpenOrders'
        params = {'symbol': symbol}
        return self._request('DELETE', endpoint, params)

