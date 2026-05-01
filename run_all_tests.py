#!/usr/bin/env python3
"""run_all_tests.py — Block 4: единый ранер тест-сюита.

Запускает все тест-модули из tests/. Использует обычный assert (без pytest),
чтобы не плодить зависимости. Завершается с кодом 1 если хоть один упал.

Использование:
  python3 run_all_tests.py            # запустить всё
  python3 run_all_tests.py test_pause # запустить один модуль
"""
import sys
import os
import importlib
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Стаб env_loader (на VPS реальный, в репо gitignored)
import types
if "env_loader" not in sys.modules:
    sys.modules["env_loader"] = types.ModuleType("env_loader")

TEST_MODULES = [
    "tests.test_safe_io",
    "tests.test_pause",
    "tests.test_kelly",
    "tests.test_basis",
    "tests.test_hedge_health",
    "tests.test_rotation_parsing",
    "tests.test_lifetime_pnl",
    "tests.test_rotation_logging",
    "tests.test_graveyard",
    "tests.test_smart_exit",      # Block 7 (D)
    "tests.test_dynamic_kelly",   # Block 7 (E)
    "tests.test_tiered_exit",     # Block 8 (Часть 1)
    "tests.test_top_up",          # Block 8 (Часть 2)
]


def run_module(modname: str) -> tuple:
    """Returns (passed, failed, errors)."""
    print(f"\n══ {modname} ══")
    try:
        mod = importlib.import_module(modname)
    except Exception as e:
        print(f"  ✗ IMPORT FAILED: {e}")
        traceback.print_exc()
        return 0, 1, [(modname, str(e))]

    tests = getattr(mod, "TESTS", None)
    if not tests:
        print(f"  ✗ no TESTS list in {modname}")
        return 0, 1, [(modname, "no TESTS list")]

    passed = 0
    failed = 0
    errors = []
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"  ✓ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {name}: {e}")
            failed += 1
            errors.append((f"{modname}.{name}", str(e) or "assertion failed"))
        except Exception as e:
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
            errors.append((f"{modname}.{name}", f"{type(e).__name__}: {e}"))
    return passed, failed, errors


def main():
    args = sys.argv[1:]
    modules = TEST_MODULES
    if args:
        # Можно передать имена модулей через CLI: test_pause или tests.test_pause
        modules = []
        for a in args:
            if not a.startswith("tests."):
                a = "tests." + a
            modules.append(a)

    t0 = time.time()
    total_p, total_f = 0, 0
    all_errors = []
    for m in modules:
        p, f, errs = run_module(m)
        total_p += p
        total_f += f
        all_errors.extend(errs)

    dt = time.time() - t0
    print(f"\n{'═' * 60}")
    print(f"RESULT: {total_p} passed, {total_f} failed in {dt:.2f}s")
    if all_errors:
        print("\nFailures:")
        for name, err in all_errors:
            print(f"  - {name}: {err}")
    print(f"{'═' * 60}")
    sys.exit(0 if total_f == 0 else 1)


if __name__ == "__main__":
    main()
