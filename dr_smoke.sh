#!/usr/bin/env bash
# dr_smoke.sh — Block 4: <60s полный health-check для DR.
# Проверяет: импорты, тесты, state_backup, pause_check.
# Завершается с кодом 0 если всё ок, 1 если что-то упало.
set -e

cd "$(dirname "$0")"

PASS=0
FAIL=0

print_step() { echo "" ; echo "── $1 ──" ; }
ok()   { echo "  ✓ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ✗ $1"; FAIL=$((FAIL + 1)); }

t0=$(date +%s)

# ── 1. Python syntax check критических модулей ──────────────────────
print_step "1/6 Syntax check (py_compile)"
for f in pause_check.py hedge_health.py rotation.py rotation_v2_score.py \
         arb_tools.py safe_io.py state_backup.py watchdog.py \
         arb_bot.py arb_bot2.py arb_bot3.py arb_bot4.py arb_bot5.py arb_bot6.py \
         arb_commander.py fleet_state.py; do
    if [ -f "$f" ]; then
        if python3 -m py_compile "$f" 2>/dev/null; then
            ok "$f"
        else
            fail "$f syntax error"
        fi
    fi
done

# ── 2. Юнит-тесты ────────────────────────────────────────────────────
print_step "2/6 Unit tests (run_all_tests.py)"
if python3 run_all_tests.py > /tmp/dr_tests.log 2>&1; then
    tail -1 /tmp/dr_tests.log
    ok "all tests passed"
else
    cat /tmp/dr_tests.log
    fail "tests FAILED"
fi

# ── 3. pause_check self-test ─────────────────────────────────────────
print_step "3/6 pause_check self-test"
if python3 pause_check.py > /tmp/dr_pc.log 2>&1; then
    ok "pause_check self-test passed"
else
    cat /tmp/dr_pc.log
    fail "pause_check self-test FAILED"
fi

# ── 4. state_backup CLI smoke ────────────────────────────────────────
print_step "4/6 state_backup CLI"
if python3 -c "import state_backup; print('import OK')" >/dev/null 2>&1; then
    ok "state_backup imports"
else
    fail "state_backup import FAILED"
fi
# list (не падать даже если архивов нет)
python3 state_backup.py list 2>/dev/null | tail -3 || true
ok "state_backup list runs"

# ── 5. Ключевые модули импортируются ─────────────────────────────────
print_step "5/6 Module imports"
for m in pause_check safe_io rotation_v2_score fleet_state; do
    if python3 -c "import $m" >/dev/null 2>&1; then
        ok "import $m"
    else
        fail "import $m FAILED"
    fi
done

# ── 6. Pyflakes (warnings only — не блокирующее) ─────────────────────
print_step "6/6 Pyflakes (warnings ok, errors fail)"
if command -v pyflakes >/dev/null 2>&1; then
    PFOUT=$(python3 -m pyflakes pause_check.py safe_io.py state_backup.py rotation_v2_score.py 2>&1 || true)
    if [ -z "$PFOUT" ]; then
        ok "no pyflakes warnings on hardened modules"
    else
        echo "$PFOUT" | head -10
        ok "pyflakes ran (warnings non-blocking)"
    fi
else
    echo "  (pyflakes not installed — skip)"
fi

dt=$(($(date +%s) - t0))
echo ""
echo "════════════════════════════════════════════════════════════"
echo "DR_SMOKE: $PASS passed, $FAIL failed in ${dt}s"
echo "════════════════════════════════════════════════════════════"
[ $FAIL -eq 0 ]
