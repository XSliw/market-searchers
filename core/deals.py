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


def sendico_price_ok(item: dict, max_price_yen: Optional[int]) -> bool:
    """Предохранитель от вала при широком запросе: отбросить дороже потолка (если задан)."""
    if max_price_yen is None:
        return True
    p = item.get("price")
    return p is None or p <= max_price_yen
