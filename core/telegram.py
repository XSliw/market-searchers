"""Тонкая обёртка над Telegram Bot API на requests (синхронно).

Один стек для сканера (Actions) и вебхука (Render): вебхук зовёт эти же
функции из FastAPI BackgroundTasks, которые выполняются в threadpool и не
блокируют event loop.
"""
from __future__ import annotations

import time
from typing import Any, Optional

import requests

API = "https://api.telegram.org/bot{token}/{method}"
CHUNK = 3500  # запас под лимит Telegram 4096 с учётом HTML-разметки


def _call(token: str, method: str, payload: dict, *, retries: int = 3) -> dict:
    """Один вызов Bot API с ретраями на 429/5xx. Возвращает распарсенный JSON."""
    url = API.format(token=token, method=method)
    last: dict = {"ok": False, "description": "no attempt"}
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=30)
        except requests.RequestException as e:
            last = {"ok": False, "description": f"network: {e}"}
            time.sleep(1.5 * (attempt + 1))
            continue
        try:
            data = r.json()
        except ValueError:
            data = {"ok": False, "description": f"http {r.status_code}: {r.text[:200]}"}
        if data.get("ok"):
            return data
        if r.status_code == 429:  # уважить retry_after
            wait = data.get("parameters", {}).get("retry_after", 2)
            time.sleep(min(wait, 30))
            last = data
            continue
        if 500 <= r.status_code < 600:  # временная — backoff и ретрай
            time.sleep(1.5 * (attempt + 1))
            last = data
            continue
        return data  # прочие 4xx не ретраим, отдаём как есть
    return last


def send_message(token: str, chat_id: "str | int", text: str,
                 reply_markup: Optional[dict] = None,
                 parse_mode: Optional[str] = "HTML",
                 disable_preview: bool = True) -> dict:
    """Отправить текст; при длине >CHUNK — несколькими сообщениями, клавиатура на последнем."""
    parts = _split(text, CHUNK)
    result: dict = {"ok": True}
    for i, part in enumerate(parts):
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup and i == len(parts) - 1:
            payload["reply_markup"] = reply_markup
        result = _call(token, "sendMessage", payload)
        if not result.get("ok"):
            return result
    return result


def send_photo(token: str, chat_id: "str | int", photo: str, caption: str = "",
               reply_markup: Optional[dict] = None,
               parse_mode: Optional[str] = "HTML") -> dict:
    """Отправить фото по URL с подписью (лимит подписи Telegram — 1024 символа).

    photo — прямой URL картинки (Telegram сам её скачает и пройдёт редиректы).
    Кнопку под фото передавать в reply_markup. При битом URL Bot API вернёт
    ok:false — вызывающий тогда откатывается на текстовую карточку.
    """
    payload: dict[str, Any] = {"chat_id": chat_id, "photo": photo, "caption": caption[:1024]}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _call(token, "sendPhoto", payload)


def edit_message_text(token: str, chat_id: "str | int", message_id: int, text: str,
                      reply_markup: Optional[dict] = None,
                      parse_mode: Optional[str] = "HTML") -> dict:
    payload: dict[str, Any] = {
        "chat_id": chat_id, "message_id": message_id,
        "text": text[:CHUNK], "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return _call(token, "editMessageText", payload)


def answer_callback(token: str, callback_query_id: str, text: Optional[str] = None) -> dict:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return _call(token, "answerCallbackQuery", payload)


def set_webhook(token: str, url: str, secret: str,
                allowed_updates: Optional[list] = None) -> dict:
    payload = {
        "url": url,
        "secret_token": secret,
        "allowed_updates": allowed_updates or ["message", "callback_query"],
    }
    return _call(token, "setWebhook", payload)


def get_me(token: str) -> dict:
    return _call(token, "getMe", {})


def _split(text: str, limit: int) -> list[str]:
    """Резать по границам строк, не рвать слова; жёстко режем только очень длинную строку."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            if cur:
                parts.append(cur)
            cur = line
        else:
            cur = cur + "\n" + line if cur else line
    if cur:
        parts.append(cur)
    return parts
