"""Точка входа сканера (GitHub Actions cron).

Проходит подписки config.json, считает сделки по правилу источника, шлёт
уведомления в Bot API соответствующего бота и коммитит state.json.
Kufar не требует секретов — потому dry-run проверяется в CI без Telegram.

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
SEEN_CAP = 400   # сколько id помним на подписку (кольцо)
DEALS_CAP = 60   # сколько последних сделок держим для /deals


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


def _fmt_kufar(d: dict) -> str:
    disc = d.get("discount_pct")
    med = d.get("median")
    tail = (f" — на {disc}% ниже медианы ({med:.0f} BYN)"
            if disc is not None and med is not None else "")
    return f"🟢 <b>{_esc(d['title'])}</b>\n{d['price']:.0f} BYN{tail}\n{d['url']}"


def _fmt_sendico(d: dict) -> str:
    price = (f"{d['price']:,} ¥".replace(",", " ")
             if d.get("price") is not None else "цена н/д")
    return f"🆕 <b>{_esc(d['title'])}</b>\n{price}\n{d['url']}"


def _slim(d: dict) -> dict:
    keep = ("id", "title", "price", "currency", "url", "median", "discount_pct")
    return {k: d[k] for k in keep if k in d}


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
                ads = kufar.search(
                    sub["query"], cat=sub.get("cat"), rgn=sub.get("rgn"),
                    min_byn=sub.get("min_byn"), max_byn=sub.get("max_byn"),
                    private_only=sub.get("private_only", True),
                )
                found, stats = deals.kufar_deals(ads, sub.get("threshold_pct", 30))
                print(f"[{key}] лотов={stats['count']} медиана={stats['median']} "
                      f"сделок={len(found)}")
                token, chat, fmt = _env("KUFAR_BOT_TOKEN"), _env("KUFAR_CHAT_ID"), _fmt_kufar
            elif src == "sendico":
                if sendico_client is None:
                    sendico_client = sendico.Sendico()
                items = sendico_client.search(sub["query"], debug=dry_run)
                found = [i for i in items
                         if deals.sendico_price_ok(i, sub.get("max_price_yen"))]
                print(f"[{key}] найдено={len(found)}")
                token, chat, fmt = _env("SENDICO_BOT_TOKEN"), _env("SENDICO_CHAT_ID"), _fmt_sendico
            else:
                print(f"[{key}] неизвестный source={src} — пропуск")
                continue

            fresh = [d for d in found if d["id"] not in seen]
            for d in fresh:
                state["deals"].append(
                    {"found_at": started, "source": src, "sub_id": sub.get("id"), **_slim(d)})
                if not dry_run and token and chat:
                    res = telegram.send_message(token, chat, fmt(d))
                    if not res.get("ok"):
                        errors.append(f"{key}: telegram {res.get('description')}")
            state["seen"][key] = (list(seen) + [d["id"] for d in fresh])[-SEEN_CAP:]
            total_new += len(fresh)
            note = " (dry-run, не отправлено)" if dry_run else ""
            print(f"[{key}] новых={len(fresh)}{note}")
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
    # (state.runs.note). Ненулевой код бывает только на катастрофе —
    # необработанное исключение выше само завалит job.
    sys.exit(0)


if __name__ == "__main__":
    main()
