"""tests/test_safe_io.py — атомарный JSON I/O с авто-восстановлением."""
from tests._bootstrap import cleanup_test_dir  # noqa: F401
import os
import tempfile
from safe_io import safe_write_json, safe_read_json, integrity_check


def test_basic_write_read():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.json")
        assert safe_write_json(p, {"hello": "world"})
        assert safe_read_json(p) == {"hello": "world"}


def test_overwrite_creates_backup():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.json")
        assert safe_write_json(p, {"v": 1})
        import time as _t; _t.sleep(1.1)  # bak.{ts} требует разный timestamp (секунды)
        assert safe_write_json(p, {"v": 2})
        baks = [f for f in os.listdir(d) if ".bak." in f]
        assert len(baks) >= 1, f"expected .bak file, got: {os.listdir(d)}"
        assert safe_read_json(p) == {"v": 2}


def test_recovery_from_corruption():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.json")
        safe_write_json(p, {"v": 1})
        import time as _t; _t.sleep(1.1)
        safe_write_json(p, {"v": 2})  # теперь есть .bak с {v:1}
        # Портим основной файл
        with open(p, "w") as f:
            f.write("{INVALID JSON")
        # Должен восстановить из .bak
        recovered = safe_read_json(p, default={"x": "default"})
        # Поскольку .bak содержит v:1 (или v:2 если bak был сделан после второй записи),
        # но не дефолт — главное чтобы это был валидный dict с ключом v
        assert isinstance(recovered, dict) and "v" in recovered, \
            f"recovery failed: {recovered}"


def test_default_when_file_missing():
    p = "/tmp/__nonexistent_safe_io_test__.json"
    if os.path.exists(p):
        os.unlink(p)
    assert safe_read_json(p, default={"d": 1}) == {"d": 1}


def test_unrecoverable_returns_default():
    """Битый файл, нет .bak — должен вернуть default."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.json")
        with open(p, "w") as f:
            f.write("not json")
        # Никаких .bak. → fallback на default
        recovered = safe_read_json(p, default={"fb": True})
        assert recovered == {"fb": True}


def test_integrity_check():
    with tempfile.TemporaryDirectory() as d:
        good = os.path.join(d, "good.json")
        bad = os.path.join(d, "bad.json")
        missing = os.path.join(d, "no.json")
        safe_write_json(good, {"ok": 1})
        with open(bad, "w") as f:
            f.write("garbage")
        result = integrity_check([good, bad, missing])
        assert result[good] is True
        assert result[bad] is False
        assert result[missing] is None


def test_unicode_handling():
    """Русский текст должен сохраняться без ensure_ascii."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.json")
        safe_write_json(p, {"reason": "Сработал T1: близко к ликвидации"})
        data = safe_read_json(p)
        assert data["reason"].startswith("Сработал")


TESTS = [
    test_basic_write_read,
    test_overwrite_creates_backup,
    test_recovery_from_corruption,
    test_default_when_file_missing,
    test_unrecoverable_returns_default,
    test_integrity_check,
    test_unicode_handling,
]


if __name__ == "__main__":
    for t in TESTS:
        t()
        print(f"  ✓ {t.__name__}")
    print(f"[test_safe_io] {len(TESTS)} passed")
