"""FastAPI-вебхук: два бота на одном сервисе (Render).

  POST /tg/kufar    → @kufarresearchbot
  POST /tg/sendico  → бот Sendico (@newsliwaibot)
  GET  /health

Уведомления о сделках шлёт сканер (Actions) — здесь только команды и меню:
/status и /deals читают state.json (raw GitHub), /menu правит config.json
(Contents API). Sendico-креды сюда НЕ попадают: поиск живёт в сканере.

Переиспользован каркас из XSliw/intervals-icu-mcp (telegram_bot.py):
Settings(BaseSettings), hmac.compare_digest на секрет-заголовке, гейт по
allowed_user_ids, дедуп update_id, самопрописывание вебхука при старте.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, Callable, Optional

import anyio
import requests
from fastapi import BackgroundTasks, FastAPI, Header, Request, Response
from pydantic_settings import BaseSettings, SettingsConfigDict

from core import gh, store, telegram

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("market-searchers")

CONFIG_PATH = "config.json"
STATE_PATH = "state.json"
KUFAR_STEP = 5        # шаг изменения threshold_pct
SENDICO_STEP = 5000   # шаг изменения max_price_yen (¥)


class Settings(BaseSettings):
    kufar_bot_token: str = ""
    sendico_bot_token: str = ""
    kufar_webhook_secret: str = ""
    sendico_webhook_secret: str = ""
    allowed_telegram_user_ids: str = ""
    public_base_url: str = ""
    auto_set_webhook: bool = True
    gh_token: str = ""
    github_repo: str = "XSliw/market-searchers"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_user_ids(self) -> "set[int]":
        out: set[int] = set()
        for part in self.allowed_telegram_user_ids.replace(",", " ").split():
            try:
                out.add(int(part))
            except ValueError:
                pass
        return out


settings = Settings()

# bot-ключ → его конфигурация. Держим лямбды, чтобы читать актуальный settings.
BOTS: "dict[str, Callable[[], dict]]" = {
    "kufar": lambda: {"token": settings.kufar_bot_token,
                      "secret": settings.kufar_webhook_secret, "source": "kufar"},
    "sendico": lambda: {"token": settings.sendico_bot_token,
                        "secret": settings.sendico_webhook_secret, "source": "sendico"},
}

_recent_update_ids: deque = deque(maxlen=512)


# --------------------------------------------------------------------------
# Чтение/запись конфигурации и состояния
# --------------------------------------------------------------------------
def _load_config_display() -> dict:
    """Свежий config.json для показа: сперва raw GitHub, потом локальный файл."""
    if settings.github_repo:
        raw = gh.read_raw(settings.github_repo, CONFIG_PATH)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
    return store.load(CONFIG_PATH, {"subscriptions": [], "paused": False})


def _load_state() -> dict:
    if settings.github_repo:
        raw = gh.read_raw(settings.github_repo, STATE_PATH)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
    return store.load(STATE_PATH, {"runs": {}, "seen": {}, "deals": []})


def _edit_config(mutate: "Callable[[dict], None]") -> Optional[str]:
    """Прочитать config.json (Contents API), применить mutate, записать.

    Возвращает None при успехе или строку-ошибку. Один повтор на 409 (гонка sha).
    """
    if not (settings.gh_token and settings.github_repo):
        return "GitHub не настроен (GH_TOKEN/GITHUB_REPO) — правки меню недоступны"
    for attempt in range(2):
        content, sha = gh.get_file(settings.github_repo, CONFIG_PATH, settings.gh_token)
        cfg = json.loads(content) if content else {"subscriptions": [], "paused": False}
        mutate(cfg)
        body = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
        try:
            gh.put_file(settings.github_repo, CONFIG_PATH, body, sha,
                        "config: правка из Telegram-меню", settings.gh_token)
            return None
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 409 and attempt == 0:
                continue  # sha устарел — перечитать и повторить
            return f"GitHub {getattr(e.response, 'status_code', '?')}"
    return "конфликт записи config.json"


# --------------------------------------------------------------------------
# Рендер меню и текстов
# --------------------------------------------------------------------------
def _menu(source: str) -> "tuple[str, dict]":
    cfg = _load_config_display()
    paused = cfg.get("paused", False)
    subs = [s for s in cfg.get("subscriptions", []) if s.get("source") == source]

    lines = [f"⚙️ <b>Меню — {source}</b>",
             f"Глобально: {'⏸ на паузе' if paused else '▶️ активно'}", ""]
    kb: list[list[dict]] = [[{"text": "▶️ Снять паузу" if paused else "⏸ Поставить паузу",
                              "callback_data": "pause"}]]

    if not subs:
        lines.append("Подписок этого источника нет.")
    for s in subs:
        sid = s.get("id")
        on = s.get("enabled", True)
        if source == "kufar":
            extra = f"−{s.get('threshold_pct', 30)}%"
        else:
            mp = s.get("max_price_yen")
            extra = f"≤{mp}¥" if mp else "без потолка"
        lines.append(f"{'✅' if on else '⬜'} <b>{sid}</b> — «{s.get('query','')}» · {extra}")
        row = [{"text": f"{'✅' if on else '⬜'} {sid}", "callback_data": f"t:{sid}"}]
        if source == "kufar":
            row.append({"text": "−5%", "callback_data": f"p:{sid}:-"})
            row.append({"text": "+5%", "callback_data": f"p:{sid}:+"})
        else:
            row.append({"text": "−5k¥", "callback_data": f"m:{sid}:-"})
            row.append({"text": "+5k¥", "callback_data": f"m:{sid}:+"})
        kb.append(row)

    kb.append([{"text": "🔄 Обновить", "callback_data": "refresh"}])
    return "\n".join(lines), {"inline_keyboard": kb}


def _status_text(source: str) -> str:
    st = _load_state()
    runs = st.get("runs", {})
    seen = st.get("seen", {})
    mine = {k: len(v) for k, v in seen.items() if k.startswith(source + ":")}
    lines = [f"📊 <b>Статус — {source}</b>",
             f"Последний прогон: {runs.get('last_finished', '—')}",
             f"Итог: {'ok' if runs.get('ok', False) else '⚠️ с ошибками'}"
             f", новых: {runs.get('new', 0)}"]
    if runs.get("note"):
        lines.append(f"Заметка: {runs['note']}")
    if mine:
        lines.append("Отслеживается id: " +
                     ", ".join(f"{k.split(':',1)[1]}={n}" for k, n in mine.items()))
    return "\n".join(lines)


def _deals_text(source: str, limit: int = 10) -> str:
    st = _load_state()
    mine = [d for d in st.get("deals", []) if d.get("source") == source][-limit:]
    if not mine:
        return f"Пока нет сохранённых сделок по {source}."
    out = [f"🧾 <b>Последние сделки — {source}</b>", ""]
    for d in reversed(mine):
        if source == "kufar":
            disc = f" (−{d['discount_pct']}%)" if d.get("discount_pct") is not None else ""
            price = f"{d.get('price', '?')} BYN{disc}"
        else:
            price = (f"{d['price']:,} ¥".replace(",", " ")
                     if d.get("price") is not None else "цена н/д")
        out.append(f"• {_esc(d.get('title', ''))} — {price}\n{d.get('url', '')}")
    return "\n".join(out)


def _help_text(source: str) -> str:
    return ("\n".join([
        f"🔎 <b>market-searchers — {source}</b>",
        "Ищу выгодные лоты и присылаю новые совпадения.",
        "",
        "/menu — подписки, пороги, пауза",
        "/status — время последнего прогона",
        "/deals — последние найденные сделки",
    ]))


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# Обработка апдейтов
# --------------------------------------------------------------------------
def _mutate_toggle_pause(cfg: dict) -> None:
    cfg["paused"] = not cfg.get("paused", False)


def _mutate_sub(source: str, sid: str, fn: "Callable[[dict], None]") -> "Callable[[dict], None]":
    def _apply(cfg: dict) -> None:
        for s in cfg.get("subscriptions", []):
            if s.get("source") == source and str(s.get("id")) == str(sid):
                fn(s)
    return _apply


def _handle_command(source: str, token: str, chat_id: int, text: str) -> None:
    cmd = text.split()[0].lstrip("/").split("@")[0].lower()
    if cmd in ("start", "help"):
        telegram.send_message(token, chat_id, _help_text(source))
    elif cmd == "menu":
        body, kb = _menu(source)
        telegram.send_message(token, chat_id, body, reply_markup=kb)
    elif cmd == "status":
        telegram.send_message(token, chat_id, _status_text(source))
    elif cmd == "deals":
        telegram.send_message(token, chat_id, _deals_text(source))
    else:
        telegram.send_message(token, chat_id, _help_text(source))


def _handle_callback(source: str, token: str, cq: dict) -> None:
    data = cq.get("data", "")
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    cq_id = cq.get("id")
    err: Optional[str] = None

    if data == "pause":
        err = _edit_config(_mutate_toggle_pause)
    elif data == "refresh":
        pass
    elif data.startswith("t:"):
        sid = data[2:]
        err = _edit_config(_mutate_sub(source, sid,
                           lambda s: s.__setitem__("enabled", not s.get("enabled", True))))
    elif data.startswith("p:") and source == "kufar":
        _, sid, sign = data.split(":")
        delta = KUFAR_STEP if sign == "+" else -KUFAR_STEP
        err = _edit_config(_mutate_sub(source, sid, lambda s: s.__setitem__(
            "threshold_pct", max(0, min(90, s.get("threshold_pct", 30) + delta)))))
    elif data.startswith("m:") and source == "sendico":
        _, sid, sign = data.split(":")
        delta = SENDICO_STEP if sign == "+" else -SENDICO_STEP

        def _bump(s: dict) -> None:
            cur = s.get("max_price_yen") or 0
            nxt = cur + delta
            s["max_price_yen"] = None if nxt <= 0 else nxt
        err = _edit_config(_mutate_sub(source, sid, _bump))

    telegram.answer_callback(token, cq_id, text=err or "готово")
    if chat_id and message_id:  # перерисовать меню свежими значениями
        body, kb = _menu(source)
        telegram.edit_message_text(token, chat_id, message_id, body, reply_markup=kb)


def _process_update(source: str, token: str, update: dict) -> None:
    try:
        if "message" in update:
            m = update["message"]
            user_id = m.get("from", {}).get("id")
            if user_id not in settings.allowed_user_ids:
                log.info("Rejected message from unauthorized user_id=%s", user_id)
                return
            text = m.get("text", "")
            if text.startswith("/"):
                _handle_command(source, token, m["chat"]["id"], text)
        elif "callback_query" in update:
            cq = update["callback_query"]
            user_id = cq.get("from", {}).get("id")
            if user_id not in settings.allowed_user_ids:
                log.info("Rejected callback from unauthorized user_id=%s", user_id)
                telegram.answer_callback(token, cq.get("id"), text="нет доступа")
                return
            _handle_callback(source, token, cq)
    except Exception:  # никогда не роняем вебхук из фоновой задачи
        log.exception("process_update failed for source=%s", source)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.auto_set_webhook and settings.public_base_url:
        for name, get in BOTS.items():
            cfg = get()
            if cfg["token"] and cfg["secret"]:
                url = f"{settings.public_base_url.rstrip('/')}/tg/{name}"
                res = await anyio.to_thread.run_sync(
                    telegram.set_webhook, cfg["token"], url, cfg["secret"])
                log.info("setWebhook %s -> %s: %s", name, url, res.get("ok"))
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    bots = [n for n, get in BOTS.items() if get()["token"]]
    return {"status": "ok", "bots": bots}


@app.post("/tg/{bot}")
async def webhook(bot: str, request: Request, background: BackgroundTasks,
                  x_telegram_bot_api_secret_token: str = Header(default="")) -> Response:
    get = BOTS.get(bot)
    if get is None:
        return Response(status_code=404)
    cfg = get()
    if not cfg["token"] or not cfg["secret"]:
        return Response(status_code=404)  # бот не сконфигурирован на этом сервисе
    if not hmac.compare_digest(x_telegram_bot_api_secret_token, cfg["secret"]):
        return Response(status_code=403)

    update = await request.json()
    update_id = update.get("update_id")
    if update_id is not None:
        if update_id in _recent_update_ids:
            return Response(status_code=200)  # дубль доставки
        _recent_update_ids.append(update_id)

    background.add_task(_process_update, cfg["source"], cfg["token"], update)
    return Response(status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
