"""Атомарное чтение/запись JSON-состояния.

Пишем через временный файл + os.replace (атомарно в пределах одного тома).
Битый файл не теряем молча, а откладываем в .broken — паттерн из
works/broadcaster/src/state.js.
"""
from __future__ import annotations

import json
import os
from typing import Any


def load(path: str, default: Any) -> Any:
    """Прочитать JSON. Нет файла — вернуть default. Битый — отложить в .broken и вернуть default."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        try:
            os.replace(path, path + ".broken")
        except OSError:
            pass
        return default


def save(path: str, data: Any) -> None:
    """Атомарная запись: во временный файл, затем rename поверх целевого."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
