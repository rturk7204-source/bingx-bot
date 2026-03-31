            self.open_pairs[pair_key] = {
                "a": sym_a,
                "b": sym_b,
                "side": side,
                "entry_z": round(float(zscore), 4),
                "corr": round(float(corr), 4),
                "budget_per_leg": self.max_position_size,
                "opened_at": datetime.now(timezone.utc).isoformat()
            }
            self.save_state()
            self.log(f"OPEN {pair_key} {side} z={zscore:.2f} corr={corr:.2f} budget={self.max_position_size}+{self.max_position_size}")
