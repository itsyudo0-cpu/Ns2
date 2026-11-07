"""
NOHARA STORE BOT — Telegram Bot API helper (nohara_bot/botapi.py)

WHY THIS FILE EXISTS
  Telegram's NATIVE coloured buttons (green / red / blue) are the "style" field of
  InlineKeyboardButton in the official Bot API (Feb 2026). Hydrogram 0.2.0 does not know
  that field, so every message that carries a keyboard is sent through the Bot API over
  HTTPS from here. Everything else (receiving updates, button presses, files, channels)
  still goes through Hydrogram. Both use the SAME bot token, so they work together.

  style values: "primary" (blue) | "success" (green) | "danger" (red)
  Older Telegram apps simply show the button without colour.

SAFETY NET
  If Telegram ever rejects a styled keyboard (400 error), the call is retried ONCE with
  the colours removed, so a button problem can never stop the bot from answering.
"""

import copy
import logging
from typing import Any, Dict, Optional

import aiohttp

log = logging.getLogger("nohara.botapi")

_API_URL = "https://api.telegram.org/bot{token}/{method}"
_session: Optional[aiohttp.ClientSession] = None


class BotApiError(Exception):
    def __init__(self, description: str, code: Optional[int] = None):
        super().__init__(description)
        self.description = description
        self.code = code


def _get_session() -> aiohttp.ClientSession:
    """One shared HTTP session (created lazily inside the running event loop)."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None


def has_style(markup: Optional[Dict[str, Any]]) -> bool:
    if not markup:
        return False
    return any("style" in button for row in markup.get("inline_keyboard", []) for button in row)


def strip_styles(markup: Dict[str, Any]) -> Dict[str, Any]:
    """Same keyboard, without the colour field."""
    clean = copy.deepcopy(markup)
    for row in clean.get("inline_keyboard", []):
        for button in row:
            button.pop("style", None)
    return clean


async def call(token: str, method: str, payload: Dict[str, Any]) -> Any:
    url = _API_URL.format(token=token, method=method)
    async with _get_session().post(url, json=payload) as resp:
        data = await resp.json(content_type=None)
    if isinstance(data, dict) and data.get("ok"):
        return data.get("result")
    description = (data or {}).get("description", "unknown Bot API error") if isinstance(data, dict) else "bad response"
    code = (data or {}).get("error_code") if isinstance(data, dict) else None
    raise BotApiError(str(description), code)


async def _with_markup(token: str, method: str, payload: Dict[str, Any], markup: Optional[Dict[str, Any]]) -> Any:
    body = dict(payload)
    if markup:
        body["reply_markup"] = markup
    try:
        return await call(token, method, body)
    except BotApiError as exc:
        desc = exc.description.lower()
        harmless = ("not modified", "not found", "chat", "forbidden", "message to")
        if markup and has_style(markup) and exc.code == 400 and not any(h in desc for h in harmless):
            log.warning("Styled keyboard rejected (%s) — retrying without colours", exc.description)
            body["reply_markup"] = strip_styles(markup)
            return await call(token, method, body)
        raise


async def send_message(token: str, chat_id: int, text: str, markup: Optional[Dict[str, Any]] = None) -> Any:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    return await _with_markup(token, "sendMessage", payload, markup)


async def copy_message(
    token: str,
    chat_id: int,
    from_chat_id: int,
    message_id: int,
    caption: Optional[str] = None,
    markup: Optional[Dict[str, Any]] = None,
) -> Any:
    payload: Dict[str, Any] = {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
    if caption is not None:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    return await _with_markup(token, "copyMessage", payload, markup)


async def edit_message_text(token: str, chat_id: int, message_id: int, text: str, markup: Optional[Dict[str, Any]] = None) -> Any:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    return await _with_markup(token, "editMessageText", payload, markup)
