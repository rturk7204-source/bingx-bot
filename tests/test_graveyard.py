"""tests/test_graveyard.py — Block 6 (A): adaptive graveyard cooldown.

Проверяет:
  - cooldown_for_reason: правильный bucket для каждой причины
  - load_graveyard: учитывает rec.cooldown_h, fallback на default
  - load_graveyard: выкидывает истёкшие записи
  - load_graveyard: толерантна к битым записям
  - add_to_graveyard: автоматически вычисляет cooldown_h из reason
  - add_to_graveyard: записывает rec с правильной структурой
  - обратная совместимость со старыми записями (без cooldown_h)
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401

import os
import json
import tempfile
from datetime import datetime, timezone, timedelta

# импорт rotation требует env_loader stub (он уже в _bootstrap)
import rotation


# ══ cooldown_for_reason ══════════════════════════════════════════════════
def test_api_vol_lock_gets_one_week():
    assert rotation.cooldown_for_reason("api_vol_lock") == 24 * 7


def test_basis_high_gets_six_hours():
    assert rotation.cooldown_for_reason("basis high 1.5%") == 6
    # case-insensitive
    assert rotation.cooldown_for_reason("BASIS_GUARD") == 6
    # подстрока в длинной фразе
    assert rotation.cooldown_for_reason("hedge_health: T4 basis spike 2.1%") == 6


def test_negative_funding_gets_twelve_hours():
    assert rotation.cooldown_for_reason("negative_funding") == 12
    assert rotation.cooldown_for_reason("rate negative for 3 cycles") == 12


def test_weak_apr_gets_twentyfour_hours():
    assert rotation.cooldown_for_reason("weak_apr") == 24
    assert rotation.cooldown_for_reason("weak") == 24
    assert rotation.cooldown_for_reason("low_apr") == 24
    assert rotation.cooldown_for_reason("underperform") == 24


def test_manual_gets_default_48():
    assert rotation.cooldown_for_reason("manual") == 48


def test_dd_and_liquidation_get_48():
    assert rotation.cooldown_for_reason("dd_critical") == 48
    assert rotation.cooldown_for_reason("near liquidation") == 48


def test_unknown_reason_falls_to_default():
    assert rotation.cooldown_for_reason("zzzgarbage") == 48
    assert rotation.cooldown_for_reason("") == 48
    assert rotation.cooldown_for_reason(None) == 48


def test_priority_api_vol_lock_beats_other_substrings():
    """Если в reason есть 'api_vol_lock' — она должна выиграть, даже
    если там есть другие slot-words. Защищает порядок правил."""
    assert rotation.cooldown_for_reason("api_vol_lock weak") == 24 * 7


# ══ load_graveyard / add_to_graveyard ════════════════════════════════════
class _GraveyardCtx:
    """Контекст-менеджер: подменяет GRAVEYARD_FILE на временный."""
    def __init__(self):
        fd, self.path = tempfile.mkstemp(suffix=".json", prefix="gy_test_")
        os.close(fd)
        os.unlink(self.path)
        self._saved = None

    def __enter__(self):
        self._saved = rotation.GRAVEYARD_FILE
        rotation.GRAVEYARD_FILE = self.path
        return self

    def __exit__(self, *exc):
        rotation.GRAVEYARD_FILE = self._saved
        if os.path.exists(self.path):
            os.unlink(self.path)


def test_add_uses_reason_to_compute_cooldown():
    with _GraveyardCtx() as ctx:
        rotation.add_to_graveyard("AAA-USDT", "weak_apr")
        with open(ctx.path) as f:
            data = json.load(f)
        assert "AAA-USDT" in data
        assert data["AAA-USDT"]["cooldown_h"] == 24
        assert data["AAA-USDT"]["reason"] == "weak_apr"


def test_add_explicit_cooldown_overrides_reason():
    with _GraveyardCtx() as ctx:
        rotation.add_to_graveyard("BBB-USDT", "weak_apr", cooldown_h=72)
        with open(ctx.path) as f:
            data = json.load(f)
        assert data["BBB-USDT"]["cooldown_h"] == 72


def test_load_uses_individual_cooldown_h():
    """Запись с cooldown_h=6: через 7 часов должна исчезнуть."""
    with _GraveyardCtx() as ctx:
        old = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
        with open(ctx.path, "w") as f:
            json.dump({
                "BASIS-USDT": {"ejected_at": old, "reason": "basis", "cooldown_h": 6},
                "WEAK-USDT":  {"ejected_at": old, "reason": "weak",  "cooldown_h": 24},
            }, f)
        gv = rotation.load_graveyard()
        assert "BASIS-USDT" not in gv, "BASIS истёк (6ч < 7ч прошло)"
        assert "WEAK-USDT"  in gv,     "WEAK ещё держится (24ч cooldown)"


def test_load_legacy_records_fall_to_default_48h():
    """Старые записи без cooldown_h: используют GRAVEYARD_COOLDOWN_H=48."""
    with _GraveyardCtx() as ctx:
        recent = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        old    = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        with open(ctx.path, "w") as f:
            json.dump({
                "RECENT-USDT": {"ejected_at": recent, "reason": "weak"},  # 24ч прошло, 48 default — alive
                "OLD-USDT":    {"ejected_at": old,    "reason": "weak"},  # 49ч прошло — dead
            }, f)
        gv = rotation.load_graveyard()
        assert "RECENT-USDT" in gv
        assert "OLD-USDT"    not in gv


def test_load_skips_corrupt_entries():
    """Битые записи не должны валить весь graveyard."""
    with _GraveyardCtx() as ctx:
        good = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with open(ctx.path, "w") as f:
            json.dump({
                "GOOD-USDT":     {"ejected_at": good, "reason": "weak", "cooldown_h": 24},
                "NO-TS-USDT":    {"reason": "weak", "cooldown_h": 24},
                "BAD-TS-USDT":   {"ejected_at": "not-a-date", "reason": "x"},
                "WRONG-TYPE":    {"ejected_at": 12345, "reason": "x"},
            }, f)
        gv = rotation.load_graveyard()
        assert "GOOD-USDT" in gv
        assert "NO-TS-USDT" not in gv
        assert "BAD-TS-USDT" not in gv
        assert "WRONG-TYPE" not in gv


def test_add_then_load_round_trip():
    """add_to_graveyard → load_graveyard возвращает запись."""
    with _GraveyardCtx():
        rotation.add_to_graveyard("RT-USDT", "basis high", cooldown_h=6)
        gv = rotation.load_graveyard()
        assert "RT-USDT" in gv
        assert gv["RT-USDT"]["cooldown_h"] == 6
        assert gv["RT-USDT"]["reason"] == "basis high"


def test_add_overwrites_previous():
    """Повторный add_to_graveyard заменяет запись (новый ejected_at)."""
    with _GraveyardCtx():
        rotation.add_to_graveyard("X-USDT", "weak")
        gv1 = rotation.load_graveyard()
        ts1 = gv1["X-USDT"]["ejected_at"]
        # имитируем добавление через секунду
        import time as _t; _t.sleep(0.01)
        rotation.add_to_graveyard("X-USDT", "api_vol_lock")
        gv2 = rotation.load_graveyard()
        assert gv2["X-USDT"]["cooldown_h"] == 24 * 7
        assert gv2["X-USDT"]["reason"] == "api_vol_lock"
        assert gv2["X-USDT"]["ejected_at"] != ts1


TESTS = [
    test_api_vol_lock_gets_one_week,
    test_basis_high_gets_six_hours,
    test_negative_funding_gets_twelve_hours,
    test_weak_apr_gets_twentyfour_hours,
    test_manual_gets_default_48,
    test_dd_and_liquidation_get_48,
    test_unknown_reason_falls_to_default,
    test_priority_api_vol_lock_beats_other_substrings,
    test_add_uses_reason_to_compute_cooldown,
    test_add_explicit_cooldown_overrides_reason,
    test_load_uses_individual_cooldown_h,
    test_load_legacy_records_fall_to_default_48h,
    test_load_skips_corrupt_entries,
    test_add_then_load_round_trip,
    test_add_overwrites_previous,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_graveyard] {len(TESTS)} passed")
