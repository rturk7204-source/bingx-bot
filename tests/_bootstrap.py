"""Bootstrap для тестов: добавляет родительскую директорию в sys.path
и стабает модули, которые требуют env vars / запись в /root."""
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Стаб env_loader (на VPS реальный, в репо gitignored)
if "env_loader" not in sys.modules:
    sys.modules["env_loader"] = types.ModuleType("env_loader")

# Изолированная BOT_DIR для тестов pause_check / state_backup
TEST_BOT_DIR = os.path.join(_HERE, "_tmp_bot_dir")
os.makedirs(os.path.join(TEST_BOT_DIR, "state"), exist_ok=True)
os.makedirs(os.path.join(TEST_BOT_DIR, "logs"), exist_ok=True)
os.environ["BOT_DIR"] = TEST_BOT_DIR


def cleanup_test_dir():
    """Удаляет содержимое тестового BOT_DIR (но не саму директорию)."""
    import shutil
    for sub in ("state", "logs"):
        p = os.path.join(TEST_BOT_DIR, sub)
        if os.path.exists(p):
            shutil.rmtree(p)
        os.makedirs(p, exist_ok=True)
    # Чистим JSON файлы в корне (не сам каталог)
    for f in os.listdir(TEST_BOT_DIR):
        full = os.path.join(TEST_BOT_DIR, f)
        if os.path.isfile(full):
            os.unlink(full)
