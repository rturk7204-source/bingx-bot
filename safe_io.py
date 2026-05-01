#!/usr/bin/env python3
"""
safe_io.py — Block 6: атомарная запись JSON state-файлов.

Защита от:
  - частичных записей при kill -9 / power loss
  - повреждения JSON при concurrent write
  - потери данных при кривом сериализаторе (atomic rename)

Использование:
  from safe_io import safe_write_json, safe_read_json
  safe_write_json("state/foo.json", {"a": 1})
  data = safe_read_json("state/foo.json", default={})
"""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

BACKUP_KEEP = 3  # храним последние 3 .bak версии каждого файла


def safe_write_json(path: str, data, indent: int = 2) -> bool:
    """
    Атомарная запись JSON:
      1. Текущий файл (если есть) копируется в .bak.{ts}
      2. Новые данные пишутся в .tmp в той же директории
      3. fsync + os.replace() (atomic на POSIX)
      4. Старые .bak ротируются (оставляем BACKUP_KEEP последних)

    Возвращает True при успехе.
    """
    path = str(path)
    dirname = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dirname, exist_ok=True)

    # 1. Бэкап существующего файла
    if os.path.exists(path) and os.path.getsize(path) > 0:
        ts = int(time.time())
        bak_path = f"{path}.bak.{ts}"
        try:
            shutil.copy2(path, bak_path)
        except Exception as e:
            print(f"[SAFE_IO] backup warn for {path}: {e}")

    # 2. Запись через tmp
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=os.path.basename(path) + ".",
                                         suffix=".tmp", dir=dirname)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False, default=str)
            f.flush()
            os.fsync(f.fileno())
        # 3. Atomic replace
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"[SAFE_IO] write FAILED for {path}: {e}")
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        return False

    # 4. Ротация .bak — оставляем BACKUP_KEEP свежих
    try:
        baks = sorted(Path(dirname).glob(f"{os.path.basename(path)}.bak.*"))
        for old in baks[:-BACKUP_KEEP]:
            old.unlink(missing_ok=True)
    except Exception:
        pass

    return True


def safe_read_json(path: str, default=None):
    """
    Чтение JSON с авто-восстановлением из .bak при повреждении.
    Если основной файл битый — пробует .bak.* по убыванию ts.
    Возвращает default при полном отказе.
    """
    path = str(path)
    if not os.path.exists(path):
        return default

    # Попытка №1: основной файл
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[SAFE_IO] {path} corrupted: {e}, trying backups")
    except Exception as e:
        print(f"[SAFE_IO] {path} read error: {e}")
        return default

    # Попытка №2: бэкапы
    dirname = os.path.dirname(os.path.abspath(path)) or "."
    baks = sorted(Path(dirname).glob(f"{os.path.basename(path)}.bak.*"),
                  reverse=True)
    for bak in baks:
        try:
            with open(bak) as f:
                data = json.load(f)
            print(f"[SAFE_IO] recovered {path} from {bak.name}")
            # Восстанавливаем основной файл
            shutil.copy2(bak, path)
            return data
        except Exception:
            continue

    print(f"[SAFE_IO] {path} unrecoverable, returning default")
    return default


def integrity_check(paths: list) -> dict:
    """
    Проверяет что список файлов читается как валидный JSON.
    Возвращает {path: True/False/None} (None если файла нет).
    """
    result = {}
    for p in paths:
        if not os.path.exists(p):
            result[p] = None
            continue
        try:
            with open(p) as f:
                json.load(f)
            result[p] = True
        except Exception:
            result[p] = False
    return result


if __name__ == "__main__":
    # Smoke test
    test_path = "/tmp/safe_io_test.json"
    assert safe_write_json(test_path, {"hello": "world", "n": 42})
    assert safe_read_json(test_path) == {"hello": "world", "n": 42}
    # Corrupt and recover
    with open(test_path, "w") as f:
        f.write("{invalid json")
    assert safe_write_json(test_path, {"v": 2})  # creates backup
    # Make it corrupted, ensure read recovers
    recovered = safe_read_json(test_path)
    assert recovered == {"v": 2}
    print("[SAFE_IO] smoke test OK")
