"""GitHub API — используется только вебхуком (Render) для config.json.

config.json читаем через Contents API (нужен sha, чтобы потом записать),
пишем тем же sha. state.json вебхук читает как raw (read_raw) — там sha не нужен.
Сканер (Actions) коммитит state.json обычным git, а не этим модулем.
"""
from __future__ import annotations

import base64
from typing import Any, Optional

import requests

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"


def get_file(repo: str, path: str, token: str) -> "tuple[Optional[str], Optional[str]]":
    """Вернуть (текст, sha) файла из ветки main. Нет файла — (None, None)."""
    r = requests.get(
        f"{API}/repos/{repo}/contents/{path}",
        headers=_headers(token),
        params={"ref": "main"},
        timeout=30,
    )
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def put_file(repo: str, path: str, content: str, sha: Optional[str],
             message: str, token: str) -> dict:
    """Записать/обновить файл в main. sha=None — создать новый."""
    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(
        f"{API}/repos/{repo}/contents/{path}",
        headers=_headers(token), json=payload, timeout=30,
    )
    r.raise_for_status()
    return r.json()


def read_raw(repo: str, path: str) -> Optional[str]:
    """Прочитать файл через raw CDN (кэш ~минуты). Нет файла — None."""
    r = requests.get(f"{RAW}/{repo}/main/{path}", timeout=30)
    if r.status_code == 200:
        return r.text
    return None


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
