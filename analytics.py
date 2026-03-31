import json
import os
from datetime import datetime
import pandas as pd

LOG_FILE = "/root/bingx-bot/trades.json"

class Analytics:
    def __init__(self):
        self.trades = self.load_trades()
    
    def load_trades(self):
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                return json.load(f)
        return []
    
    def save_trade(self, trade_data):
        trade_data['timestamp'] = datetime.now().isoformat()
        self.trades.append(trade_data)
        with open(LOG_FILE, 'w') as f:
            json.dump(self.trades, f, indent=2)
    
    def log_trade(self, symbol, side, price, quantity, reason="", pnl=None):
        trade = {
            'symbol': symbol,
            'side': side,
            'price': float(price),
            'quantity': float(quantity),
            'reason': reason,
            'timestamp': __import__('datetime').datetime.now().isoformat()
        }
        if pnl is not None:
            pnl_pct = round(float(pnl), 4)
            # Реальный PnL в USDT: pnl% / 100 * notional / leverage
            notional = float(quantity) * float(price)
            pnl_usdt = round(pnl_pct / 100 * notional / 10, 4)
            trade['pnl'] = pnl_pct
            trade['pnl_usdt'] = pnl_usdt
        self.save_trade(trade)
        if pnl is not None:
            pnl_str = f" | PnL: {pnl:+.2f}% ({trade['pnl_usdt']:+.3f} USDT)"
        else:
            pnl_str = ""
        print(f"[ANALYTICS] {side} {symbol} @ {price} | {reason}{pnl_str}")
    
    def get_stats(self):
        if not self.trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'total_profit': 0,
                'avg_profit': 0,
                'best_trade': 0,
                'worst_trade': 0
            }
        
        # Берём только закрывающие сделки с полем pnl
        closing_reasons = ['Trailing Stop', 'Stop-Loss', 'Take-Profit', 
                          'Partial TP', 'Close LONG', 'Close SHORT',
                          'Fib TP 0.382', 'Fib TP 0.618', 'Fib TP 1.0']
        profits = []
        for t in self.trades:
            if 'pnl' in t and t['pnl'] is not None:
                profits.append(float(t['pnl']))
            elif t.get('reason') in closing_reasons:
                # Старые сделки без pnl — пропускаем
                pass

        win_count = sum(1 for p in profits if p > 0)
        loss_count = sum(1 for p in profits if p <= 0)

        # Считаем реальный PnL в USDT (pnl% * позиция / 100)
        pnl_usdt_list = []
        for t in self.trades:
            if 'pnl' in t and t['pnl'] is not None:
                usdt = float(t['pnl']) / 100 * float(t.get('quantity', 0)) * float(t.get('price', 0))
                pnl_usdt_list.append(usdt)

        return {
            'total_trades': len(profits),
            'win_rate': round(win_count / len(profits) * 100, 1) if profits else 0,
            'total_profit_pct': round(sum(profits), 2),
            'total_profit': round(sum(pnl_usdt_list), 2),
            'avg_profit': round(sum(profits) / len(profits), 2) if profits else 0,
            'best_trade': round(max(profits), 2) if profits else 0,
            'worst_trade': round(min(profits), 2) if profits else 0
        }
    
    def print_report(self):
        stats = self.get_stats()
        print("\n" + "="*60)
        print("  ОТЧЁТ ПО ТОРГОВЛЕ")
        print("="*60)
        print(f"  Всего сделок: {stats['total_trades']}")
        print(f"  Закрытых пар: {stats['total_pairs']//2}")
        print(f"  Win Rate: {stats['win_rate']}%")
        print(f"  Общая прибыль: {stats['total_profit']}%")
        print(f"  Средняя прибыль: {stats['avg_profit']}%")
        print(f"  Лучшая сделка: +{stats['best_trade']}%")
        print(f"  Худшая сделка: {stats['worst_trade']}%")
        print("="*60 + "\n")
