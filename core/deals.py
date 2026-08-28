"""Правила отбора сделок.

Kufar — «дешевле обрезанной медианы на N%»: считаем медиану, но сперва режем
выбросы по IQR, чтобы приманки-запчасти и случайные дорогие лоты её не тянули.
Sendico — «любое новое совпадение» (дедуп по id делает сканер против state).
"""
from __future__ import annotations

from typing import Optional


def _quantile(sorted_vals: "list[float]", q: float) -> float:
    """Линейная интерполяция квантиля (как numpy 'linear'). sorted_vals не пуст."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= n:
        return sorted_vals[lo]
    return sorted_vals[lo] * (1 - frac) + sorted_vals[lo + 1] * frac


def trimmed_median(prices: "list[float]") -> "tuple[Optional[float], Optional[float], Optional[float]]":
    """Медиана после отсечения выбросов по IQR. Вернуть (median, lo, hi).

    lo/hi — границы [Q1−1.5·IQR, Q3+1.5·IQR]. Данных мало (<4) — медиана без обрезки.
    """
    vals = sorted(p for p in prices if p is not None and p > 0)
    if not vals:
        return None, None, None
    if len(vals) < 4:
        return _quantile(vals, 0.5), vals[0], vals[-1]
    q1 = _quantile(vals, 0.25)
    q3 = _quantile(vals, 0.75)
    iqr = q3 - q1
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    trimmed = [v for v in vals if lo <= v <= hi] or vals
    return _quantile(trimmed, 0.5), lo, hi


def kufar_deals(ads: "list[dict]", threshold_pct: float) -> "tuple[list[dict], dict]":
    """Отобрать лоты дешевле медианы на threshold_pct и не ниже нижней границы IQR.

    ads — уже нормализованные (price в BYN). Нижняя граница IQR отсекает
    «слишком хорошо, чтобы быть правдой» приманки. Вернуть (deals, stats).
    """
    prices = [a["price"] for a in ads if a.get("price")]
    median, lo, hi = trimmed_median(prices)
    stats = {"count": len(prices), "median": median, "iqr_lo": lo, "iqr_hi": hi}
    if median is None:
        return [], stats
    cutoff = median * (1 - threshold_pct / 100.0)
    floor = lo if lo is not None else 0
    deals = []
    for a in ads:
        p = a.get("price")
        if p is None:
            continue
        if p <= cutoff and p >= floor:
            d = dict(a)
            d["median"] = round(median, 2)
            d["discount_pct"] = round((1 - p / median) * 100, 1) if median else None
            deals.append(d)
    deals.sort(key=lambda d: d["price"])
    return deals, stats


# --- Уровни выгодности (общие для сканера и вебхука) -------------------------
SUPER_PCT = 35   # ниже медианы на столько и глубже → 🔥 супервыгодно
GOOD_PCT = 15    # ниже на столько → 🟢 выгодно (в пределах ± этого — рынок)

# tier → (эмодзи, слово, базовый хэштег|None). Хэштег есть только у super/good,
# чтобы «#супервыгодно» в поиске чата собирал именно топ.
TIERS = {
    "super":  ("🔥", "СУПЕРВЫГОДНО", "супервыгодно"),
    "good":   ("🟢", "Выгодно", "выгодно"),
    "market": ("⚪️", "Рынок", None),
    "pricey": ("🔺", "Дороговато", None),
    "low":    ("⚠️", "Подозрительно дёшево", None),
    "na":     ("▫️", "Цена н/д", None),
}
# очередь показа при переполнении потолка уведомлений: сперва лучшие сделки
TIER_RANK = {"super": 0, "good": 1, "market": 2, "low": 3, "pricey": 4, "na": 5}


def classify(price: Optional[float], median: Optional[float], iqr_lo: Optional[float],
             *, super_pct: float = SUPER_PCT,
             good_pct: float = GOOD_PCT) -> "tuple[str, Optional[float]]":
    """Уровень выгодности лота относительно медианы. Вернуть (tier, discount_pct).

    discount_pct > 0 — дешевле медианы, < 0 — дороже. По возрастанию цены:
    low (ниже границы IQR — «слишком дёшево», приманка/запчасти) < super < good
    < market < pricey.
    """
    if price is None or not median:
        return "na", None
    disc = round((1 - price / median) * 100, 1)
    floor = iqr_lo if iqr_lo is not None else 0
    if price < floor:
        return "low", disc
    if disc >= super_pct:
        return "super", disc
    if disc >= good_pct:
        return "good", disc
    if disc <= -good_pct:
        return "pricey", disc
    return "market", disc


def score_ads(ads: "list[dict]") -> "tuple[list[dict], dict]":
    """Проставить каждому лоту tier/discount_pct/median по обрезанной медиане.

    Вернуть (ads_scored, stats). Исходный порядок (по свежести) сохраняем —
    очередь показа выбирает сканер. Медиана обрезана по IQR (trimmed_median).
    """
    prices = [a["price"] for a in ads if a.get("price")]
    median, lo, hi = trimmed_median(prices)
    stats = {"count": len(prices), "median": median, "iqr_lo": lo, "iqr_hi": hi}
    out = []
    for a in ads:
        tier, disc = classify(a.get("price"), median, lo)
        d = dict(a)
        d["tier"] = tier
        d["discount_pct"] = disc
        d["median"] = round(median, 2) if median else None
        out.append(d)
    return out, stats


def sendico_price_ok(item: dict, max_price_yen: Optional[int]) -> bool:
    """Предохранитель от вала при широком запросе: отбросить дороже потолка (если задан)."""
    if max_price_yen is None:
        return True
    p = item.get("price")
    return p is None or p <= max_price_yen
