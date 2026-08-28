"""Точка входа сканера (GitHub Actions cron).

Проходит подписки config.json, считает совпадения по правилу источника, шлёт
уведомления в Bot API соответствующего бота и коммитит state.json.
Kufar не требует секретов — потому dry-run проверяется в CI без Telegram.

Kufar-подписка знает два режима (поле "mode"):
  - "all"   — любое новое объявление по запросу, с фото и кнопкой (как старый
              бот): много лотов, без медианы;
  - "deals" — только дешевле обрезанной по IQR медианы на threshold_pct.
Первый прогон новой подписки в режиме "all" не спамит всё разом: он лишь
запоминает базовую линию (seen), а шлёт только то, что появится потом. На один
прогон одной подписки шлём не больше MAX_NOTIFY (потолок от вала).

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
MAX_NOTIFY = 10       # потолок уведомлений на подписку за один прогон


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


def _fmt_kufar(d: dict, label: str) -> str:
    """Карточка как у старого бота: заголовок · название · цена · поиск (+скидка)."""
    lines = [
        "🆕 <b>НОВОЕ · НАБЛЮДЕНИЕ</b>",
        f"🛍 {_esc(d.get('title') or '')}",
        f"💵 Цена: {_price_br(d.get('price'))}",
    ]
    if label:
        lines.append(f"🔍 Поиск: <i>{_esc(label)}</i>")
    if d.get("discount_pct") and d.get("median"):
        lines.append(f"📉 На {d['discount_pct']}% ниже медианы ({d['median']:.0f} Br)")
    return "\n".join(lines)


def _fmt_sendico(d: dict) -> str:
    price = (f"{d['price']:,} ¥".replace(",", " ")
             if d.get("price") is not None else "цена н/д")
    return f"🆕 <b>{_esc(d.get('title') or '')}</b>\n{price}\n{d.get('url','')}"


def _notify(src: str, token: str, chat: str, d: dict, sub: dict) -> dict:
    """Отправить одну карточку. Kufar — фото+кнопка, с откатом на текст."""
    if src == "kufar":
        label = sub.get("label") or sub.get("query") or ""
        caption = _fmt_kufar(d, label)
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
    keep = ("id", "title", "price", "currency", "url", "median", "discount_pct")
    return {k: d[k] for k in keep if k in d}


def _kufar_found(sub: dict) -> "tuple[list[dict], str]":
    """Вернуть (кандидаты, строка-лог) по режиму подписки."""
    mode = (sub.get("mode") or "all").lower()
    common = dict(cat=sub.get("cat"), rgn=sub.get("rgn"),
                  min_byn=sub.get("min_byn"), max_byn=sub.get("max_byn"))
    if mode == "deals":
        ads = kufar.search(sub["query"], size=sub.get("size", 200),
                           private_only=sub.get("private_only", True), **common)
        found, stats = deals.kufar_deals(ads, sub.get("threshold_pct", 30))
        return found, (f"режим=deals лотов={stats['count']} "
                       f"медиана={stats['median']} сделок={len(found)}")
    # "all" — любое новое, как старый бот: без медианы, без стоп-слов, частник+компания
    found = kufar.search(sub["query"], size=sub.get("size", 60),
                         private_only=sub.get("private_only", False),
                         drop_stopwords=sub.get("drop_stopwords", False), **common)
    return found, f"режим=all лотов={len(found)}"


def run(dry_run: bool, only_source: "str | None") -> int:
    config = store.load(CONFIG_PATH, {"subscriptions": [], "paused": False})
    state = store.load(STATE_PATH, {"runs": {}, "seen": {}, "deals": []})
    state.setdefault("seen", {})
    state.setdefault("deals", [])

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
                token, chat = _env("KUFAR_BOT_TOKEN"), _env("KUFAR_CHAT_ID")
            elif src == "sendico":
                if sendico_client is None:
                    sendico_client = sendico.Sendico()
                items = sendico_client.search(sub["query"], debug=dry_run)
                found = [i for i in items
                         if deals.sendico_price_ok(i, sub.get("max_price_yen"))]
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

            fresh = [d for d in found if d["id"] not in seen]
            notify = fresh[:MAX_NOTIFY]
            if len(fresh) > len(notify):
                print(f"[{key}] новых={len(fresh)} > потолок {MAX_NOTIFY}: "
                      f"шлём {len(notify)}, остальные помечаем виденными")
            for d in notify:
                state["deals"].append(
                    {"found_at": started, "source": src, "sub_id": sub.get("id"), **_slim(d)})
                if not dry_run and token and chat:
                    res = _notify(src, token, chat, d, sub)
                    if not res.get("ok"):
                        errors.append(f"{key}: telegram {res.get('description')}")
            state["seen"][key] = (list(seen) + [d["id"] for d in fresh])[-SEEN_CAP:]
            total_new += len(notify)
            note = " (dry-run, не отправлено)" if dry_run else ""
            print(f"[{key}] новых={len(fresh)} отправлено={len(notify)}{note}")
        except Exception as e:  # одна кривая подписка не должна ронять прогон
            errors.append(f"{key}: {type(e).__name__}: {e}")
            print("ERROR", key, repr(e))
            traceback.print_exc()

    state["deals"] = state["deals"][-DEALS_CAP:]
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
