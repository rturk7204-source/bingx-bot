import requests
import time
from datetime import datetime, timedelta

class NewsAnalyzer:
    def __init__(self):
        self.cache = {}
        self.cache_time = 0
        self.cache_ttl = 1800  # 30 минут

        # Ключевые слова для анализа тональности
        self.bullish_words = [
            'bullish', 'surge', 'rally', 'breakout', 'adoption', 'partnership',
            'launch', 'upgrade', 'bull', 'growth', 'record', 'high', 'pump',
            'buy', 'long', 'positive', 'optimistic', 'recovery', 'bounce',
            'institutional', 'etf', 'approval', 'listing', 'integration'
        ]
        self.bearish_words = [
            'bearish', 'crash', 'dump', 'hack', 'ban', 'regulation', 'sec',
            'lawsuit', 'sell', 'short', 'negative', 'fear', 'panic', 'drop',
            'fall', 'decline', 'risk', 'warning', 'fraud', 'scam', 'exploit',
            'vulnerability', 'liquidation', 'bankrupt', 'collapse'
        ]
        self.high_impact_words = [
            'sec', 'fed', 'fomc', 'etf', 'ban', 'hack', 'exploit',
            'bankruptcy', 'crash', 'emergency', 'breaking', 'urgent'
        ]

    def fetch_cryptopanic(self, symbol=None):
        """Получаем новости с CryptoPanic RSS (без API ключа)"""
        try:
            # Используем публичный RSS без ключа
            url = 'https://cryptopanic.com/api/v1/posts/?auth_token=free&kind=news&public=true'
            if symbol:
                coin = symbol.replace('-USDT', '').replace('-BTC', '')
                url += f'&currencies={coin}'
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return data.get('results', [])
        except: pass
        return []

    def fetch_rss_coindesk(self):
        """Получаем новости с CoinDesk RSS"""
        try:
            import xml.etree.ElementTree as ET
            r = requests.get('https://www.coindesk.com/arc/outboundfeeds/rss/', timeout=10)
            root = ET.fromstring(r.content)
            items = []
            for item in root.findall('.//item')[:10]:
                title = item.findtext('title', '')
                desc = item.findtext('description', '')
                items.append({'title': title, 'description': desc})
            return items
        except: pass
        return []

    def analyze_text(self, text):
        """Анализируем тональность текста"""
        text_lower = text.lower()
        bull_score = sum(1 for w in self.bullish_words if w in text_lower)
        bear_score = sum(1 for w in self.bearish_words if w in text_lower)
        high_impact = any(w in text_lower for w in self.high_impact_words)
        return bull_score, bear_score, high_impact

    def get_market_sentiment(self, symbol=None):
        """Получаем общий новостной сентимент рынка"""
        now = time.time()
        cache_key = symbol or 'general'

        # Проверяем кеш
        if cache_key in self.cache and (now - self.cache_time) < self.cache_ttl:
            return self.cache[cache_key]

        total_bull = 0
        total_bear = 0
        high_impact_count = 0
        news_count = 0

        # Получаем новости
        cp_news = self.fetch_cryptopanic(symbol)
        for item in cp_news[:20]:
            title = item.get('title', '')
            b, be, hi = self.analyze_text(title)
            total_bull += b
            total_bear += be
            if hi: high_impact_count += 1
            news_count += 1

        # Если мало новостей — берём общие
        if news_count < 5:
            rss_news = self.fetch_rss_coindesk()
            for item in rss_news:
                text = item.get('title', '') + ' ' + item.get('description', '')
                b, be, hi = self.analyze_text(text)
                total_bull += b
                total_bear += be
                if hi: high_impact_count += 1
                news_count += 1

        # Определяем сигнал
        if news_count == 0:
            result = {"signal": "NEUTRAL", "score": 0, "news_count": 0,
                     "bull": 0, "bear": 0, "high_impact": False}
        else:
            net_score = total_bull - total_bear
            high_impact = high_impact_count >= 2

            if high_impact and total_bear > total_bull:
                signal = "STRONG_BEARISH"
            elif high_impact and total_bull > total_bear:
                signal = "STRONG_BULLISH"
            elif net_score >= 3:
                signal = "BULLISH"
            elif net_score <= -3:
                signal = "BEARISH"
            else:
                signal = "NEUTRAL"

            result = {
                "signal": signal,
                "score": net_score,
                "news_count": news_count,
                "bull": total_bull,
                "bear": total_bear,
                "high_impact": high_impact
            }

        self.cache[cache_key] = result
        self.cache_time = now
        return result

    def get_signal_for_trade(self, symbol, side):
        """Проверяем можно ли открывать сделку с учётом новостей"""
        try:
            sentiment = self.get_market_sentiment(symbol)
            signal = sentiment['signal']
            high_impact = sentiment['high_impact']

            # Блокируем при высоком негативном воздействии
            if high_impact and signal in ('STRONG_BEARISH', 'BEARISH') and side == 'BUY':
                print(f"[NEWS] {symbol}: блокируем LONG — негативные новости ({signal})")
                return False, signal

            if high_impact and signal in ('STRONG_BULLISH', 'BULLISH') and side == 'SELL':
                print(f"[NEWS] {symbol}: блокируем SHORT — позитивные новости ({signal})")
                return False, signal

            if sentiment['news_count'] > 0:
                print(f"[NEWS] {symbol}: {signal} (bull={sentiment['bull']}, bear={sentiment['bear']}, news={sentiment['news_count']})")

            return True, signal
        except Exception as e:
            print(f"[NEWS] Error: {e}")
            return True, "NEUTRAL"
