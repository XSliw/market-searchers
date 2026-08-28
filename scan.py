"""Точка входа сканера (GitHub Actions cron).

Проходит подписки config.json, считает совпадения по правилу источника, шлёт
уведомления в Bot API соответствующего бота и коммитит state.json.
Kufar не требует секретов — потому dry-run проверяется в CI без Telegram.

Kufar — единый режим «оценка выгодности». По каждому запросу берём выборку
лотов, считаем обрезанную по IQR медиану и клеим каждому лоту уровень:
  🔥 супервыгодно / 🟢 выгодно / ⚪️ рынок / 🔺 дороговато / ⚠️ подозрительно.
Что реально слать — решает поле подписки `notify`:
  "all" (любое новое) | "good" (только 🔥/🟢) | "super" (только 🔥).
Супервыгодные копятся в state["hot"] и несут хэштег #супервыгодно (поиск в
чате находит их разом). Первый прогон подписки лишь запоминает базовую линию
(не спамит всё разом); на один прогон одной подписки шлём не больше MAX_NOTIFY,
причём сперва лучшие сделки (порядок по TIER_RANK).

Запуск:  python scan.py [--dry-run] [--source kufar|sendico]
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone

from core import deals, store, telegram
from sources import kufar, sendico

CONFIG_PATH = "config.json"
STATE_PATH = "state.json"
SEEN_CAP = 400        # сколько id помним на подписку (кольцо)
DEALS_CAP = 60        # сколько последних сделок держим для /deals
HOT_CAP = 120         # сколько супервыгодных копим для /hot и поиска в чате
MAX_NOTIFY = 10       # потолок уведомлений на подписку за один прогон

# notify подписки → множество уровней, которые реально уходят в чат.
NOTIFY_TIERS = {
    "all":   {"super", "good", "market", "pricey", "low"},
    "good":  {"super", "good"},
    "deals": {"super", "good"},
    "super": {"super"},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _env(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return ""


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _price_br(p) -> str:
    if p is None:
        return "цена н/д"
    s = f"{p:.2f}".rstrip("0").rstrip(".")
    return f"{s} Br"


def _tier_line(d: dict) -> str:
    emoji, word, _ = deals.TIERS.get(d.get("tier", "na"), deals.TIERS["na"])
    disc = d.get("discount_pct")
    if disc is None:
        return f"{emoji} <b>{word}</b>"
    sign = "−" if disc >= 0 else "+"
    return f"{emoji} <b>{word}</b> · {sign}{abs(disc):g}% от медианы"


def _tags(d: dict, sub: dict) -> str:
    """Хэштеги для поиска в чате: #супервыгодно/#выгодно + категорийный тег."""
    _, _, base = deals.TIERS.get(d.get("tier", "na"), deals.TIERS["na"])
    if not base:
        return ""
    parts = [f"#{base}"]
    tag = sub.get("tag")
    if tag:
        parts.append(f"#{tag}")
    return " ".join(parts)


def _fmt_kufar(d: dict, sub: dict) -> str:
    """Карточка: уровень выгодности · название · цена/медиана · поиск (+хэштеги)."""
    label = sub.get("label") or sub.get("query") or ""
    lines = [_tier_line(d), f"🛍 {_esc(d.get('title') or '')}"]
    med = d.get("median")
    lines.append(f"💵 {_price_br(d.get('price'))}"
                 + (f"  ·  медиана ~{med:.0f} Br" if med else ""))
    if label:
        lines.append(f"🔍 Поиск: <i>{_esc(label)}</i>")
    tags = _tags(d, sub)
    if tags:
        lines.append(tags)
    return "\n".join(lines)


def _fmt_sendico(d: dict) -> str:
    price = (f"{d['price']:,} ¥".replace(",", " ")
             if d.get("price") is not None else "цена н/д")
    return f"🆕 <b>{_esc(d.get('title') or '')}</b>\n{price}\n{d.get('url','')}"


def _notify(src: str, token: str, chat: str, d: dict, sub: dict) -> dict:
    """Отправить одну карточку. Kufar — фото+кнопка, с откатом на текст."""
    if src == "kufar":
        caption = _fmt_kufar(d, sub)
        url = d.get("url") or ""
        btn = ({"inline_keyboard": [[{"text": "🔗 Открыть объявление", "url": url}]]}
               if url else None)
        img = d.get("image")
        if img:
            res = telegram.send_photo(token, chat, img, caption, reply_markup=btn)
            if res.get("ok"):
                return res
            # битый URL картинки — лот не теряем, шлём текстом
        text = caption + (f"\n{url}" if url else "")
        return telegram.send_message(token, chat, text, reply_markup=btn)
    return telegram.send_message(token, chat, _fmt_sendico(d))


def _slim(d: dict) -> dict:
    keep = ("id", "title", "price", "currency", "url", "median", "discount_pct", "tier")
    return {k: d[k] for k in keep if k in d}


def _kufar_found(sub: dict) -> "tuple[list[dict], str]":
    """Оценить выборку по запросу: каждый лот получает tier/discount/median."""
    ads = kufar.search(
        sub["query"], size=sub.get("size", 200),
        cat=sub.get("cat"), rgn=sub.get("rgn"),
        min_byn=sub.get("min_byn"), max_byn=sub.get("max_byn"),
        private_only=sub.get("private_only", False),
        drop_stopwords=sub.get("drop_stopwords", False))
    scored, stats = deals.score_ads(ads)
    n_super = sum(1 for d in scored if d["tier"] == "super")
    n_good = sum(1 for d in scored if d["tier"] == "good")
    med = f"{stats['median']:.0f}" if stats.get("median") else "—"
    return scored, f"лотов={stats['count']} медиана={med} 🔥{n_super} 🟢{n_good}"


def run(dry_run: bool, only_source: "str | None") -> int:
    config = store.load(CONFIG_PATH, {"subscriptions": [], "paused": False})
    state = store.load(STATE_PATH, {"runs": {}, "seen": {}, "deals": [], "hot": []})
    state.setdefault("seen", {})
    state.setdefault("deals", [])
    state.setdefault("hot", [])

    if config.get("paused") and not dry_run:
        print("paused — прогон пропущен (dry-run игнорирует паузу)")
        return 0

    started = now_iso()
    sendico_client: "sendico.Sendico | None" = None
    total_new = 0
    errors: list[str] = []

    for sub in config.get("subscriptions", []):
        if not sub.get("enabled", True):
            continue
        src = sub.get("source")
        if only_source and src != only_source:
            continue
        key = f"{src}:{sub.get('id')}"
        seen = set(state["seen"].get(key, []))
        try:
            if src == "kufar":
                found, logline = _kufar_found(sub)
                allowed = NOTIFY_TIERS.get(
                    (sub.get("notify") or "all").lower(), NOTIFY_TIERS["all"])
                token, chat = _env("KUFAR_BOT_TOKEN"), _env("KUFAR_CHAT_ID")
            elif src == "sendico":
                if sendico_client is None:
                    sendico_client = sendico.Sendico()
                items = sendico_client.search(sub["query"], debug=dry_run)
                found = [i for i in items
                         if deals.sendico_price_ok(i, sub.get("max_price_yen"))]
                for i in found:
                    i.setdefault("tier", "na")
                allowed = None  # sendico — любое новое совпадение
                logline = f"найдено={len(found)}"
                token, chat = _env("SENDICO_BOT_TOKEN"), _env("SENDICO_CHAT_ID")
            else:
                print(f"[{key}] неизвестный source={src} — пропуск")
                continue
            print(f"[{key}] {logline}")

            # Первый прогон подписки: запомнить базу, не слать всё разом.
            # В dry-run базу не пишем — показываем, что бы улетело.
            if key not in state["seen"] and not dry_run:
                state["seen"][key] = [d["id"] for d in found][:SEEN_CAP]
                print(f"[{key}] базлайн={len(found)} (первый прогон, не шлём)")
                continue

            new = [d for d in found if d["id"] not in seen]
            pool = [d for d in new if allowed is None or d.get("tier") in allowed]
            pool.sort(key=lambda d: (deals.TIER_RANK.get(d.get("tier", "na"), 9),
                                     -(d.get("discount_pct") or 0)))
            notify = pool[:MAX_NOTIFY]
            if len(pool) > len(notify):
                print(f"[{key}] к отправке={len(pool)} > потолок {MAX_NOTIFY}: "
                      f"шлём лучшие {len(notify)}")
            for d in notify:
                rec = {"found_at": started, "source": src, "sub_id": sub.get("id"),
                       "label": sub.get("label"), **_slim(d)}
                state["deals"].append(rec)
                if d.get("tier") == "super":
                    state["hot"].append(rec)
                if not dry_run and token and chat:
                    res = _notify(src, token, chat, d, sub)
                    if not res.get("ok"):
                        errors.append(f"{key}: telegram {res.get('description')}")
            # Все новые (любого уровня) помечаем виденными — чтобы не пересматривать.
            state["seen"][key] = (list(seen) + [d["id"] for d in new])[-SEEN_CAP:]
            total_new += len(notify)
            note = " (dry-run, не отправлено)" if dry_run else ""
            print(f"[{key}] новых={len(new)} к отправке={len(pool)} "
                  f"отправлено={len(notify)}{note}")
        except Exception as e:  # одна кривая подписка не должна ронять прогон
            errors.append(f"{key}: {type(e).__name__}: {e}")
            print("ERROR", key, repr(e))
            traceback.print_exc()

    state["deals"] = state["deals"][-DEALS_CAP:]
    state["hot"] = state["hot"][-HOT_CAP:]
    state["runs"] = {
        "last_started": started, "last_finished": now_iso(),
        "ok": not errors, "new": total_new, "note": "; ".join(errors)[:500],
    }
    if not dry_run:
        store.save(STATE_PATH, state)
        print(f"state.json обновлён: новых={total_new}, ошибок={len(errors)}")
    else:
        print(f"dry-run: новых было бы={total_new}, ошибок={len(errors)}")
    return len(errors)


def main() -> None:
    ap = argparse.ArgumentParser(description="market-searchers scanner")
    ap.add_argument("--dry-run", action="store_true",
                    help="считать и печатать, ничего не слать и не коммитить")
    ap.add_argument("--source", choices=["kufar", "sendico"], default=None,
                    help="ограничить одним источником")
    args = ap.parse_args()
    run(args.dry_run, args.source)
    # Cron держим зелёным: ошибки отдельных подписок видны в /status
    # (state.runs.note). Необработанное исключение выше само завалит job.
    sys.exit(0)


if __name__ == "__main__":
    main()
