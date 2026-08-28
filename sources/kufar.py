"""Адаптер kufar.by — открытый JSON-поиск, без авторизации.

Ловушки (все подтверждены запросами к живому API):
- цены в копейках-строкой: price_byn="55500" == 555.00 BYN;
- prc=r:min,max требует ОБЕ границы, иначе HTTP 422;
- неизвестный query-параметр → HTTP 422 (роняем одну подписку, не весь прогон);
- нечёткий поиск подмешивает чехлы/запчасти — сужаем категорией cat и стоп-словами.

Поля ответа (проверены): ad_id, list_id, subject, price_byn (строка, копейки),
ad_link (полный URL), company_ad (bool), category (строка, напр. "17010"),
images[] ({path, media_storage:"rms"}) → фото-URL rms.kufar.by/v1/gallery/<path>.
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests

BASE = "https://api.kufar.by/search-api/v2/search/rendered-paginated"

# Вежливая пауза перед каждым запросом: в прогоне ~90 подписок подряд, не хотим
# долбить Kufar очередью без передышки. Переопределяется env KUFAR_THROTTLE_S.
THROTTLE_S = float(os.getenv("KUFAR_THROTTLE_S", "0.3"))
# На троттлинг/5xx — короткий повтор, чтобы разовый 429 не терял подписку целиком.
RETRY_STATUS = {429, 500, 502, 503, 504}

# Битое/запчасти/реплики: ломают медиану ложными «супервыгодно» и не цель поиска.
# Список намеренно узкий — общие слова (обмен, ремонт, экран) режут и годные лоты.
STOPWORDS = (
    "чехол", "чехлы", "стекло", "стёкла", "стекла",
    "запчаст", "на запчасти", "копия", "реплика",
    "разбит", "битый", "не работает", "нерабоч", "неисправ", "треснут",
)


def search(query: str, *, cat: Optional[str] = None, rgn: Optional[str] = None,
           min_byn: Optional[float] = None, max_byn: Optional[float] = None,
           size: int = 200, private_only: bool = True,
           drop_stopwords: bool = True) -> "list[dict]":
    """Вернуть нормализованные лоты: {id,title,price(BYN),currency,url,is_company,category,image}."""
    params: dict[str, Any] = {"query": query, "size": max(1, min(size, 200)), "sort": "lst.d"}
    if cat:
        params["cat"] = cat
    if rgn:
        params["rgn"] = rgn
    # prc в копейках и требует ОБЕ границы — подставляем широкий диапазон, если одна пуста.
    if min_byn is not None or max_byn is not None:
        lo = int((min_byn if min_byn is not None else 0) * 100)
        hi = int((max_byn if max_byn is not None else 10_000_000) * 100)
        params["prc"] = f"r:{lo},{hi}"

    r = _get_with_retry(params)
    ads_raw = r.json().get("ads") or []

    out: list[dict] = []
    for ad in ads_raw:
        norm = _normalize(ad)
        if norm is None:
            continue
        # Цена не указана / 0 Br — это «договорная» или ads-услуги (ремонт,
        # скупка, вывоз): для оценщика по цене они пусты и лишь сорят выборку.
        if norm["price"] is None or norm["price"] <= 0:
            continue
        if cat and str(norm["category"]) != str(cat):
            continue
        if private_only and norm["is_company"]:
            continue
        if drop_stopwords and _has_stopword(norm["title"]):
            continue
        out.append(norm)
    return out


def _get_with_retry(params: dict) -> requests.Response:
    """GET с вежливой паузой и коротким повтором на троттлинг/5xx.

    422 (кривой параметр подписки) НЕ ретраим — пусть падает сразу, это баг
    в конфиге, а не временная неудача. Ретраим только RETRY_STATUS и сетевые сбои.
    """
    last: Optional[Exception] = None
    for attempt in range(3):
        if THROTTLE_S:
            time.sleep(THROTTLE_S)
        try:
            r = requests.get(BASE, params=params, timeout=30,
                             headers={"User-Agent": "market-searchers/1.0"})
        except requests.RequestException as e:  # сеть моргнула — повторим
            last = e
            time.sleep(1.0 * (attempt + 1))
            continue
        if r.status_code in RETRY_STATUS and attempt < 2:
            time.sleep(1.0 * (attempt + 1))  # backoff перед повтором
            continue
        r.raise_for_status()  # 422 и прочее 4xx — пусть падает только эта подписка
        return r
    if last is not None:
        raise last
    raise RuntimeError("kufar: исчерпаны повторы без ответа")


def _normalize(ad: dict) -> Optional[dict]:
    ad_id = ad.get("ad_id") or ad.get("list_id")
    if ad_id is None:
        return None
    price = None
    raw = ad.get("price_byn")
    if raw is not None:
        try:
            price = int(raw) / 100.0
        except (TypeError, ValueError):
            price = None
    return {
        "id": str(ad_id),
        "title": ad.get("subject") or "",
        "price": price,
        "currency": "BYN",
        "url": ad.get("ad_link") or f"https://www.kufar.by/item/{ad_id}",
        "is_company": bool(ad.get("company_ad")),
        "category": _category_of(ad),
        "image": _image_url(ad),
    }


def _image_url(ad: dict) -> Optional[str]:
    """Первое фото объявления как URL для Telegram sendPhoto.

    Kufar кладёт картинки в images[] c полем path; host rms.kufar.by отдаёт
    их через 302 на rmsN.kufar.by (Telegram сам ходит по редиректу). Нет фото
    → None, сканер тогда шлёт карточку текстом.
    """
    for im in ad.get("images") or []:
        p = im.get("path")
        if p:
            return f"https://rms.kufar.by/v1/gallery/{p}"
    return None


def _category_of(ad: dict) -> str:
    if ad.get("category"):
        return str(ad["category"])
    for p in ad.get("ad_parameters", []) or []:  # запасной путь, если поле уедет
        if p.get("pu") == "cat" or p.get("p") == "category":
            return str(p.get("v") or "")
    return ""


def _has_stopword(title: str) -> bool:
    t = (title or "").lower()
    return any(w in t for w in STOPWORDS)
