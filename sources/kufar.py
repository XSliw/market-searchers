"""Адаптер kufar.by — открытый JSON-поиск, без авторизации.

Ловушки (все подтверждены запросами к живому API):
- цены в копейках-строкой: price_byn="55500" == 555.00 BYN;
- prc=r:min,max требует ОБЕ границы, иначе HTTP 422;
- неизвестный query-параметр → HTTP 422 (роняем одну подписку, не весь прогон);
- нечёткий поиск подмешивает чехлы/запчасти — сужаем категорией cat и стоп-словами.

Поля ответа (проверены): ad_id, list_id, subject, price_byn (строка, копейки),
ad_link (полный URL), company_ad (bool), category (строка, напр. "17010"),
images[] ({path, media_storage:"rms"}) → фото-URL rms.kufar.by/v1/gallery/<path>.

ФИЛЬТРЫ КАТЕГОРИЙ (разведано живьём, tools/probe_*.mjs). В каждом лоте
ad_parameters[] несёт поле `pu` — это и есть ИМЯ query-параметра. То есть любой
фильтр, видимый на сайте, доступен через API: cct (тип комплектующих),
ccvc (модель GPU), cte (приставки/игры), mss (размер обуви), shlgt (длина
стельки), bca (класс велосипеда), brnd (бренд), cnd (состояние) и т.д.
Такой фильтр точнее слова в названии: cat=16010&cct=11 отдаёт ЧИСТЫЕ видеокарты
(1118 лотов, медиана 399 Br), тогда как запрос «rtx 3080» тянет системные блоки
и завышает медиану. Проверено: без cct медиана 150 Br, с cct=11 — 399 Br, состав
выборки 100/100 «Видеокарты».

МУЛЬТИЗНАЧЕНИЕ — только через префикс `v.or:`, проверено арифметикой:
bca=1 → 1056 лотов, bca=5 → 107, bca=v.or:1,5 → 1163 = ровно сумма.
Формы `1,5` / повтор параметра / `1|5` дают 0 лотов (молча!), `bca[]=` → 422,
`v.in:` и `or:` → 500. Поэтому список значений тут всегда клеится в `v.or:`.
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

# Служебные параметры собираем сами — подписка не должна их перебить через filters
# (иначе размер выборки/сортировка/цена уедут молча и медиана станет мусором).
RESERVED_PARAMS = frozenset({"query", "size", "sort", "prc", "cat", "rgn", "cursor"})


def _filter_value(v: Any) -> str:
    """Значение фильтра в форму, которую Kufar реально понимает.

    Список/кортеж или строка через запятую = «любое из» → префикс `v.or:`
    (единственная рабочая форма мультизначения, см. docstring модуля).
    Готовые префиксы (`v.or:`, `r:`) и одиночные значения не трогаем.
    """
    if isinstance(v, (list, tuple, set)):
        vals = [str(x).strip() for x in v if str(x).strip()]
        return f"v.or:{','.join(vals)}" if len(vals) > 1 else (vals[0] if vals else "")
    s = str(v).strip()
    if "," in s and ":" not in s:  # "9,10,25" → v.or:9,10,25
        return "v.or:" + ",".join(p.strip() for p in s.split(",") if p.strip())
    return s


def search(query: str = "", *, cat: Optional[str] = None, rgn: Optional[str] = None,
           filters: Optional[dict] = None,
           min_byn: Optional[float] = None, max_byn: Optional[float] = None,
           size: int = 200, private_only: bool = True,
           drop_stopwords: bool = True) -> "list[dict]":
    """Вернуть нормализованные лоты: {id,title,price(BYN),currency,url,is_company,category,image}.

    query необязателен: поиск по одной категории с фильтрами (cat=16010&cct=11)
    точнее, чем по слову в названии, — берём весь раздел, а не совпадения текста.
    filters — любые параметры Kufar как есть: {"cct": "11", "mss": [9, 10, 25]}.
    """
    params: dict[str, Any] = {"size": max(1, min(size, 200)), "sort": "lst.d"}
    if query:
        params["query"] = query
    if cat:
        params["cat"] = cat
    if rgn:
        params["rgn"] = rgn
    for k, v in (filters or {}).items():
        if k in RESERVED_PARAMS or v is None or v == "":
            continue  # служебное или пустое — молча мимо
        val = _filter_value(v)
        if val:
            params[k] = val
    # prc в копейках и требует ОБЕ границы — подставляем широкий диапазон, если одна пуста.
    if min_byn is not None or max_byn is not None:
        lo = int((min_byn if min_byn is not None else 0) * 100)
        hi = int((max_byn if max_byn is not None else 10_000_000) * 100)
        params["prc"] = f"r:{lo},{hi}"

    if not query and not cat:
        raise ValueError("kufar.search: нужен либо query, либо cat")

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
