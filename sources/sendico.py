"""Адаптер Sendico — поиск по Mercari через залогиненный аккаунт.

Sendico закрывает /api/mercari/items прикладным гардом Laravel (403 гостю),
поэтому нужен вход. Флоу — Laravel Sanctum SPA:
  1) GET  /api/auth/login  → сервер ставит cookie XSRF-TOKEN и сессию;
  2) POST /api/login с заголовком X-XSRF-TOKEN и учётными данными;
  3) сессия дальше живёт в cookie-jar этого requests.Session.

Токен/сессию в репозиторий НЕ пишем — только память процесса. Точную форму
ответа /api/mercari/items (имена полей, обёртку) видно лишь на первом
авторизованном прогоне (за 403), поэтому _normalize устойчив к разным именам,
а в dry-run печатается первый сырой элемент для сверки и правки маппинга.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from typing import Any, Optional

import requests

BASE = "https://sendico.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class SendicoError(RuntimeError):
    """Ошибка входа или поиска Sendico — с человекочитаемым текстом наверх."""


class Sendico:
    def __init__(self, email: Optional[str] = None, password: Optional[str] = None):
        self.email = email or os.getenv("SENDICO_EMAIL", "")
        self.password = password or os.getenv("SENDICO_PASSWORD", "")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept": "application/json",
            "Origin": BASE,
            "Referer": BASE + "/",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._logged_in = False

    # --- авторизация -----------------------------------------------------
    def _xsrf(self) -> Optional[str]:
        for c in self.session.cookies:
            if c.name == "XSRF-TOKEN":
                return urllib.parse.unquote(c.value)
        return None

    def login(self) -> None:
        if not self.email or not self.password:
            raise SendicoError("нет SENDICO_EMAIL / SENDICO_PASSWORD в окружении")
        self.session.get(f"{BASE}/api/auth/login", timeout=30)  # получить CSRF-cookie
        token = self._xsrf()
        headers = {"X-XSRF-TOKEN": token} if token else {}
        r = self.session.post(
            f"{BASE}/api/login",
            json={"email": self.email, "password": self.password},
            headers=headers, timeout=30,
        )
        if r.status_code >= 400:
            raise SendicoError(f"login {r.status_code}: {r.text[:200]}")
        self._logged_in = True

    # --- поиск -----------------------------------------------------------
    def search(self, keyword: str, *, page: int = 1, debug: bool = False,
               **filters: Any) -> "list[dict]":
        """Найти лоты Mercari по keyword. При 401/403 — один перелогин, затем повтор."""
        if not self._logged_in:
            self.login()
        params: dict[str, Any] = {"keyword": keyword, "page": page}
        params.update({k: v for k, v in filters.items() if v is not None})

        r = self.session.get(f"{BASE}/api/mercari/items", params=params, timeout=30)
        if r.status_code in (401, 403):
            self._logged_in = False
            self.login()
            r = self.session.get(f"{BASE}/api/mercari/items", params=params, timeout=30)
        if r.status_code >= 400:
            raise SendicoError(f"search {r.status_code}: {r.text[:200]}")

        raw = _extract_list(r.json())
        if debug and raw:
            print("[sendico] первый сырой элемент:",
                  json.dumps(raw[0], ensure_ascii=False)[:800])
        return [n for n in (_normalize(x) for x in raw) if n]


def _extract_list(data: Any) -> "list[dict]":
    """Достать список лотов из ответа, не зная точной обёртки (items/data/results/…)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "data", "results", "products", "list"):
            v = data.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):  # напр. {"data": {"items": [...]}}
                inner = _extract_list(v)
                if inner:
                    return inner
    return []


def _normalize(x: Any) -> Optional[dict]:
    if not isinstance(x, dict):
        return None
    item_id = _first(x, "id", "item_id", "code", "productId", "product_id")
    if item_id is None:
        return None
    price = _first(x, "price", "price_yen", "priceJpy", "price_jpy", "yen")
    try:
        price = int(price) if price is not None else None
    except (TypeError, ValueError):
        price = None
    title = _first(x, "name", "title", "subject", "productName") or ""
    url = _first(x, "url", "link", "itemUrl", "item_url") or \
        f"{BASE}/en/mercari/items/{item_id}"
    return {
        "id": str(item_id),
        "title": title,
        "price": price,
        "currency": "JPY",
        "url": url,
    }


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None
