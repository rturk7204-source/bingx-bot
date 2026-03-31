class KellyCriterion:
    """Оптимальный размер позиции по дробной формуле Келли"""

    def __init__(self, max_position=50.0, min_position=15.0, fraction=0.5):
        self.max_position = max_position
        self.min_position = min_position
        self.fraction = fraction

    def calculate(self, win_rate, avg_win_pct, avg_loss_pct, base_balance, dd_factor=1.0):
        try:
            if avg_loss_pct <= 0 or win_rate <= 0:
                return self.min_position

            p = win_rate / 100.0
            q = 1.0 - p
            b = avg_win_pct / avg_loss_pct

            if b <= 0:
                return self.min_position

            kelly_frac = (p * b - q) / b
            kelly_frac = max(0.0, kelly_frac)

            # дробный Kelly
            kelly_frac *= self.fraction

            # штраф за просадку
            kelly_frac *= dd_factor

            # жёсткий потолок — не более 30% депо
            kelly_frac = min(kelly_frac, 0.30)

            position_size = base_balance * kelly_frac
            position_size = max(self.min_position, min(self.max_position, position_size))

            print(f"[KELLY] calc: p={p:.2f} b={b:.2f} frac={kelly_frac*100:.1f}% dd_f={dd_factor:.2f} -> {position_size:.2f} USDT")
            return round(position_size, 2)

        except Exception:
            return self.min_position

    def get_size(self, analytics, balance, ml_confidence, drawdown_pct=None):
        """
        Возвращает РЕАЛЬНЫЙ размер позиции в USDT.
        Учитывает: статистику, дробный Kelly, ML уверенность, просадку.
        """
        try:
            stats = analytics.get_stats()
            win_rate = stats.get("win_rate", 50)
            total_trades = stats.get("total_trades", 0)

            # dd_factor: чем больше просадка, тем меньше размер
            dd_factor = 1.0
            if drawdown_pct is not None:
                if drawdown_pct >= 30.0:
                    dd_factor = 0.0
                elif drawdown_pct >= 20.0:
                    dd_factor = 0.25
                elif drawdown_pct >= 10.0:
                    dd_factor = 0.5
                elif drawdown_pct >= 5.0:
                    dd_factor = 0.8
                else:
                    dd_factor = 1.0

            # Мало сделок — безопасные пресеты
            if total_trades < 5:
                if ml_confidence >= 0.85:
                    kelly_size = min(25.0, self.max_position)
                elif ml_confidence >= 0.75:
                    kelly_size = 20.0
                elif ml_confidence >= 0.65:
                    kelly_size = 15.0
                else:
                    kelly_size = self.min_position
                kelly_size *= dd_factor
                kelly_size = max(self.min_position, min(self.max_position, kelly_size))
                print(f"[KELLY] few trades ({total_trades}): ml={ml_confidence:.3f} dd={drawdown_pct} -> {kelly_size:.2f} USDT")
                return round(kelly_size, 2)

            # Достаточно данных — считаем Kelly
            avg_win = abs(stats.get("best_trade", 2.0))
            avg_loss = abs(stats.get("worst_trade", 1.0))

            kelly_size = self.calculate(win_rate, avg_win, avg_loss, balance, dd_factor=dd_factor)

            # Корректировка на ML уверенность
            if ml_confidence >= 0.90:
                kelly_size *= 1.3
            elif ml_confidence >= 0.80:
                kelly_size *= 1.15
            elif ml_confidence >= 0.70:
                kelly_size *= 1.0
            elif ml_confidence >= 0.55:
                kelly_size *= 0.85
            else:
                kelly_size *= 0.7

            kelly_size = max(self.min_position, min(self.max_position, kelly_size))

            print(f"[KELLY] size: wr={win_rate:.0f}% ml={ml_confidence:.3f} dd={drawdown_pct} -> {kelly_size:.2f} USDT")
            return round(kelly_size, 2)

        except Exception as e:
            print(f"[KELLY] error: {e}, fallback={self.min_position}")
            return self.min_position
