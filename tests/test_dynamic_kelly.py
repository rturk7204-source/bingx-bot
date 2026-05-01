"""tests/test_dynamic_kelly.py — Block 7 (E): apply_dynamic_kelly_penalty().

Проверяет:
  - НОВЫЕ символы (нет graveyard, нет lifetime) — НЕ режутся (×1.0)
  - Графьярд-история weak/underperform/low_apr/negative → ×0.5
  - Negative lifetime PnL → ×0.7
  - Cumulative: оба пенальти → ×0.5×0.7=0.35
  - Lifetime PnL >= 0 → НЕ режет (даже если запись есть)
  - Старая graveyard-история (>7 дней) — игнорируется
  - Хорошие reasons (api_vol_lock, basis_high) — НЕ режут
  - _load_recent_graveyard_history — фильтрация по reason и времени
"""
from tests._bootstrap import cleanup_test_dir  # noqa: F401

import os
import json
import tempfile
from datetime import datetime, timezone, timedelta

import rotation


def _tmp_lifetime_pnl(symbol_earned_pairs, bot_name="arb_bot"):
    """Создаёт временный lifetime_pnl.json с заданными {symbol: earned} парами."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="lpnl_test_")
    os.close(fd)
    history = []
    for sym, earned in symbol_earned_pairs:
        history.append({
            "ts": "2026-04-30T10:00:00Z",
            "symbol": sym,
            "earned": earned,
            "cycles": 5,
            "reason": "weak",
        })
    data = {
        "version": 1,
        "updated_at": "2026-05-01T00:00:00Z",
        "bots": {
            bot_name: {
                "total_earned_usdt": sum(e for _, e in symbol_earned_pairs),
                "rotations_count": len(symbol_earned_pairs),
                "history": history,
            }
        },
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return path


def _tmp_history_file(entries):
    """Создаёт временный rotation_history.json со списком записей.
    Каждая запись: {timestamp, symbol, reason}."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="hist_test_")
    os.close(fd)
    hist = []
    for ts, sym, reason in entries:
        hist.append({
            "timestamp": ts,
            "decision": {
                "action": "rotate",
                "eject_symbol": sym,
                "eject_reason": reason,
                "new_symbol": "NEW-USDT",
            },
            "applied": True,
        })
    with open(path, "w") as f:
        json.dump(hist, f)
    return path


# ══ apply_dynamic_kelly_penalty ═══════════════════════════════════════════

def test_new_symbol_no_penalty():
    """Новый символ (нет graveyard, нет lifetime) — base_size без изменений."""
    final, notes = rotation.apply_dynamic_kelly_penalty(
        "NEW-USDT", base_size=100.0,
        graveyard_history={},
        lifetime_pnl_path="/nonexistent/path.json",
    )
    assert final == 100.0
    assert notes == []


def test_graveyard_penalty_only():
    """Символ в graveyard за weak → ×0.5."""
    final, notes = rotation.apply_dynamic_kelly_penalty(
        "WEAK-USDT", base_size=100.0,
        graveyard_history={"WEAK-USDT": "weak_apr"},
        lifetime_pnl_path="/nonexistent/path.json",
    )
    assert final == 50.0  # 100 × 0.5
    assert len(notes) == 1
    assert "recent_graveyard" in notes[0]


def test_negative_lifetime_penalty_only():
    """Символ с lifetime PnL < 0 → ×0.7."""
    p = _tmp_lifetime_pnl([("BAD-USDT", -2.50)])
    try:
        final, notes = rotation.apply_dynamic_kelly_penalty(
            "BAD-USDT", base_size=100.0,
            graveyard_history={},
            lifetime_pnl_path=p,
        )
        assert final == 70.0  # 100 × 0.7
        assert len(notes) == 1
        assert "negative_lifetime" in notes[0]
    finally:
        os.unlink(p)


def test_cumulative_penalties():
    """Оба пенальти cumulative: 100 × 0.5 × 0.7 = 35."""
    p = _tmp_lifetime_pnl([("DOUBLE-BAD", -1.0)])
    try:
        final, notes = rotation.apply_dynamic_kelly_penalty(
            "DOUBLE-BAD", base_size=100.0,
            graveyard_history={"DOUBLE-BAD": "negative_funding"},
            lifetime_pnl_path=p,
        )
        assert final == 35.0  # 100 × 0.5 × 0.7
        assert len(notes) == 2
    finally:
        os.unlink(p)


def test_positive_lifetime_no_penalty():
    """Lifetime PnL >= 0 — НЕ режет (даже если запись есть)."""
    p = _tmp_lifetime_pnl([("GOOD-USDT", 5.50)])
    try:
        final, notes = rotation.apply_dynamic_kelly_penalty(
            "GOOD-USDT", base_size=100.0,
            graveyard_history={},
            lifetime_pnl_path=p,
        )
        assert final == 100.0
        assert notes == []
    finally:
        os.unlink(p)


def test_zero_lifetime_no_penalty():
    """Lifetime PnL == 0 (не отрицательный) — НЕ режет."""
    p = _tmp_lifetime_pnl([("ZERO-USDT", 0.0)])
    try:
        final, notes = rotation.apply_dynamic_kelly_penalty(
            "ZERO-USDT", base_size=100.0,
            graveyard_history={},
            lifetime_pnl_path=p,
        )
        assert final == 100.0
        assert notes == []
    finally:
        os.unlink(p)


def test_zero_base_size_returns_zero():
    """base_size=0 — никакая penalty не нужна, возвращаем 0."""
    final, notes = rotation.apply_dynamic_kelly_penalty(
        "ANY-USDT", base_size=0.0,
        graveyard_history={"ANY-USDT": "weak"},
    )
    assert final == 0.0
    assert notes == []


def test_lifetime_summed_across_bots():
    """Сумма lifetime PnL берётся ПО ВСЕМ ботам, не только одному."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="lpnl_test_")
    os.close(fd)
    data = {
        "version": 1,
        "bots": {
            "arb_bot": {"history": [
                {"symbol": "X-USDT", "earned": -2.0},
            ]},
            "arb_bot2": {"history": [
                {"symbol": "X-USDT", "earned": 1.0},  # net = -1.0 (still negative)
            ]},
        },
    }
    with open(path, "w") as f:
        json.dump(data, f)
    try:
        final, notes = rotation.apply_dynamic_kelly_penalty(
            "X-USDT", base_size=100.0,
            graveyard_history={},
            lifetime_pnl_path=path,
        )
        assert final == 70.0  # net -1.0 < 0 → ×0.7
        assert "negative_lifetime" in notes[0]
    finally:
        os.unlink(path)


def test_corrupted_lifetime_file_fails_safe():
    """Битый lifetime_pnl.json — penalty не применяется, base_size сохраняется."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="lpnl_test_")
    os.close(fd)
    with open(path, "w") as f:
        f.write("{not valid json")
    try:
        final, notes = rotation.apply_dynamic_kelly_penalty(
            "ANY-USDT", base_size=100.0,
            graveyard_history={},
            lifetime_pnl_path=path,
        )
        assert final == 100.0  # fail-safe: penalty не применилась
        assert notes == []
    finally:
        os.unlink(path)


# ══ _load_recent_graveyard_history ════════════════════════════════════════

def test_history_filters_bad_reasons():
    """Только bad_reasons (weak/underperform/low_apr/negative) попадают в результат."""
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(hours=2)).isoformat()
    p = _tmp_history_file([
        (recent_ts, "WEAK-USDT", "weak_apr"),
        (recent_ts, "BASIS-USDT", "basis_high"),  # NOT in bad_reasons
        (recent_ts, "API-USDT", "api_vol_lock"),  # NOT in bad_reasons
        (recent_ts, "NEG-USDT", "negative_funding"),
    ])
    try:
        result = rotation._load_recent_graveyard_history(history_file=p)
        assert "WEAK-USDT" in result
        assert "NEG-USDT" in result
        assert "BASIS-USDT" not in result
        assert "API-USDT" not in result
    finally:
        os.unlink(p)


def test_history_filters_old_entries():
    """Записи старше N дней игнорируются."""
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=10)).isoformat()
    recent_ts = (now - timedelta(days=2)).isoformat()
    p = _tmp_history_file([
        (old_ts, "OLD-USDT", "weak_apr"),       # >7 дней — игнор
        (recent_ts, "RECENT-USDT", "weak_apr"),  # 2 дня — попадёт
    ])
    try:
        result = rotation._load_recent_graveyard_history(history_file=p, days=7)
        assert "OLD-USDT" not in result
        assert "RECENT-USDT" in result
    finally:
        os.unlink(p)


def test_history_missing_file_returns_empty():
    """Отсутствующий history.json — fail-open: пустой dict."""
    result = rotation._load_recent_graveyard_history(
        history_file="/nonexistent/history.json"
    )
    assert result == {}


def test_history_corrupted_file_returns_empty():
    """Битый history.json — fail-open: пустой dict."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="hist_test_")
    os.close(fd)
    with open(path, "w") as f:
        f.write("{garbage")
    try:
        result = rotation._load_recent_graveyard_history(history_file=path)
        assert result == {}
    finally:
        os.unlink(path)


def test_history_skips_entries_without_symbol():
    """Записи без eject_symbol (например fill_empty с symbol='—') игнорируются."""
    now_ts = datetime.now(timezone.utc).isoformat()
    fd, path = tempfile.mkstemp(suffix=".json", prefix="hist_test_")
    os.close(fd)
    hist = [
        {"timestamp": now_ts, "decision": {"eject_symbol": "—", "eject_reason": "weak"}},
        {"timestamp": now_ts, "decision": {"eject_symbol": "", "eject_reason": "weak"}},
        {"timestamp": now_ts, "decision": {"eject_symbol": "X-USDT", "eject_reason": "weak"}},
    ]
    with open(path, "w") as f:
        json.dump(hist, f)
    try:
        result = rotation._load_recent_graveyard_history(history_file=path)
        assert "X-USDT" in result
        assert "—" not in result
        assert "" not in result
    finally:
        os.unlink(path)


def test_history_substring_match():
    """Reason matching is substring (case-insensitive). 'weak_apr' matches 'weak'."""
    now_ts = datetime.now(timezone.utc).isoformat()
    p = _tmp_history_file([
        (now_ts, "A-USDT", "weak_apr"),       # match 'weak'
        (now_ts, "B-USDT", "underperformer"), # match 'underperform'
        (now_ts, "C-USDT", "LOW_APR_RATE"),   # match 'low_apr' (case-insensitive)
    ])
    try:
        result = rotation._load_recent_graveyard_history(history_file=p)
        assert "A-USDT" in result
        assert "B-USDT" in result
        assert "C-USDT" in result
    finally:
        os.unlink(p)


def test_history_empty_list_returns_empty():
    """Пустой history.json (`[]`) — пустой dict."""
    fd, path = tempfile.mkstemp(suffix=".json", prefix="hist_test_")
    os.close(fd)
    with open(path, "w") as f:
        json.dump([], f)
    try:
        result = rotation._load_recent_graveyard_history(history_file=path)
        assert result == {}
    finally:
        os.unlink(path)


def test_constants_match_documentation():
    """Sanity-check: константы Block 7 (E)."""
    assert rotation.KELLY_PENALTY_GRAVEYARD_FACTOR == 0.5
    assert rotation.KELLY_PENALTY_NEGATIVE_LIFETIME_FACTOR == 0.7
    assert rotation.KELLY_PENALTY_GRAVEYARD_DAYS == 7
    assert "weak" in rotation.KELLY_PENALTY_GRAVEYARD_REASONS
    assert "underperform" in rotation.KELLY_PENALTY_GRAVEYARD_REASONS
    assert "low_apr" in rotation.KELLY_PENALTY_GRAVEYARD_REASONS
    assert "negative" in rotation.KELLY_PENALTY_GRAVEYARD_REASONS


TESTS = [
    test_new_symbol_no_penalty,
    test_graveyard_penalty_only,
    test_negative_lifetime_penalty_only,
    test_cumulative_penalties,
    test_positive_lifetime_no_penalty,
    test_zero_lifetime_no_penalty,
    test_zero_base_size_returns_zero,
    test_lifetime_summed_across_bots,
    test_corrupted_lifetime_file_fails_safe,
    test_history_filters_bad_reasons,
    test_history_filters_old_entries,
    test_history_missing_file_returns_empty,
    test_history_corrupted_file_returns_empty,
    test_history_skips_entries_without_symbol,
    test_history_substring_match,
    test_history_empty_list_returns_empty,
    test_constants_match_documentation,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_dynamic_kelly] {len(TESTS)} passed")
