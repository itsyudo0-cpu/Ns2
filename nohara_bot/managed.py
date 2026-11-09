"""
NOHARA STORE BOT — Managed Bots (Telegram Bot API 9.6, April 3 2026)   (nohara_bot/managed.py)

ONE-TAP CLONE CREATION  (no BotFather steps, no token copy/paste)
  1. The MASTER bot is a "manager bot": Bot Management Mode must be ON for it in the @BotFather Mini App.
  2. The user taps  https://t.me/newbot/<master_username>/<suggested_username>?name=<bot name>
  3. Telegram opens a ready "create bot" screen; the user confirms.
  4. Telegram sends the master bot a `managed_bot` update (the creator + the new bot).
  5. We call getManagedBotToken, start the clone and tell the creator it is live.
  The new bot is OWNED by the user on Telegram, and MANAGED by the master bot (bot_manager_id = master).

Hydrogram (MTProto) does not know this update yet, so the `managed_bot` update is read through the
Bot API (getUpdates, allowed_updates=["managed_bot"]) — the same HTTPS helper that nohara_bot/botapi.py
already uses. Both work together on the same token.

MASTER DATABASE CHANNEL
  Every new clone (one-tap OR /clone TOKEN) is recorded in a private channel chosen by the platform
  owner with /setmasterdb: bot token, bot id, username, creator id / username / name, time, method.
"""

import asyncio
import html
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import quote

from nohara_bot.botapi import BotApiError, call
from nohara_bot.config import MASTER_BOT_TOKEN
from nohara_bot.database import db
from nohara_bot.services.core import MASTER

log = logging.getLogger("nohara.managed")

DEFAULT_BOT_NAME = "My Nohara Store"


# ───────────────────────── links ─────────────────────────
def creation_link(master_username: str, name: str = DEFAULT_BOT_NAME) -> str:
    """https://t.me/newbot/<manager>/<suggested_username>?name=<name> — a random suggestion is almost always free."""
    manager = str(master_username).lstrip("@")
    suggested = f"nohara_{secrets.token_hex(3)}_bot"
    return f"https://t.me/newbot/{manager}/{suggested}?name={quote(name)}"


async def check_manager_mode() -> Optional[bool]:
    """True when Bot Management Mode is ON for the master bot (getMe.can_manage_bots)."""
    try:
        me = await call(MASTER_BOT_TOKEN, "getMe", {})
        enabled = bool(me.get("can_manage_bots"))
    except Exception as exc:
        log.warning("Could not check Bot Management Mode: %s", exc)
        return None
    if enabled:
        log.info("Managed bots: Bot Management Mode is ON — one-tap clone creation is active")
    else:
        log.warning(
            "Managed bots: Bot Management Mode is OFF for the master bot. Open @BotFather's Mini App → your master bot → "
            "enable 'Bot Management Mode'. Until then clones can only be made with /clone TOKEN."
        )
    return enabled


# ───────────────────────── master database channel ─────────────────────────
async def master_db_channel_id() -> int:
    master = await db.get_master_clone() or {}
    value = master.get("master_db_channel_id")
    if value is None:
        value = os.getenv("MASTER_DB_CHANNEL_ID", "0")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _who(person: Any) -> Dict[str, Any]:
    """Accepts a Hydrogram User object or a Bot API dict."""
    if isinstance(person, dict):
        first, last = person.get("first_name") or "", person.get("last_name") or ""
        return {"id": person.get("id"), "username": person.get("username") or "", "name": f"{first} {last}".strip()}
    first, last = getattr(person, "first_name", "") or "", getattr(person, "last_name", "") or ""
    return {"id": getattr(person, "id", None), "username": getattr(person, "username", "") or "", "name": f"{first} {last}".strip()}


async def record_bot_details(*, event: str, token: str, bot: Any, owner: Any, method: str) -> bool:
    """Posts the full details of a clone to the MASTER DATABASE CHANNEL. Never raises."""
    channel_id = await master_db_channel_id()
    client = MASTER.get("client")
    if not channel_id or not client:
        return False
    b, o = _who(bot), _who(owner)
    method_label = "One-tap (Managed Bot)" if method == "managed" else "Pasted token (/clone)"
    text = (
        f"<b>🆕 {html.escape(event)}</b>\n━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>Bot:</b> @{html.escape(b['username'])} ({html.escape(b['name'])})\n"
        f"🆔 <b>Bot ID:</b> <code>{b['id']}</code>\n"
        f"🔑 <b>Bot Token:</b> <code>{html.escape(token)}</code>\n\n"
        f"👤 <b>Created by:</b> {html.escape(o['name'] or '—')} ({'@' + html.escape(o['username']) if o['username'] else 'no username'})\n"
        f"🆔 <b>User ID:</b> <code>{o['id']}</code>\n\n"
        f"🛠 <b>Method:</b> {method_label}\n"
        f"🕒 <b>Time (UTC):</b> {time.strftime('%d %b %Y, %H:%M:%S', time.gmtime())}"
    )
    try:
        await client.send_message(channel_id, text)
        return True
    except Exception as exc:
        log.warning("Could not write to the master database channel (%s). Is the bot an admin there?", exc)
        return False


# ───────────────────────── managed_bot updates ─────────────────────────
async def handle_managed_bot_update(update: Dict[str, Any]) -> None:
    """`managed_bot` = {"user": creator, "bot": the managed bot}. Fired on creation AND when its token changes."""
    from nohara_bot.handlers.master import next_steps_text, provision_clone, restart_clone_with_token  # lazy: avoids an import cycle

    creator, bot = update.get("user") or {}, update.get("bot") or {}
    if not creator.get("id") or not bot.get("id"):
        return
    bot_id = str(bot["id"])
    owner_id = int(creator["id"])
    try:
        token = await call(MASTER_BOT_TOKEN, "getManagedBotToken", {"user_id": int(bot["id"])})
    except BotApiError as exc:
        log.error("getManagedBotToken failed for %s: %s", bot_id, exc)
        await _tell(owner_id, f"❌ Your bot was created, but I could not read its access token.\n<code>{html.escape(exc.description)}</code>")
        return

    existing = await db.get_clone(bot_id)
    if existing:  # token was replaced (or this update is a replay) → refresh only when it really changed
        if db.get_clone_token(existing) != token:
            await restart_clone_with_token(existing, token)
            await record_bot_details(event="CLONE BOT TOKEN CHANGED", token=token, bot=bot, owner=creator, method="managed")
            await _tell(int(existing["owner_id"]), f"🔑 The access token of @{html.escape(bot.get('username', ''))} was changed — your store bot was restarted automatically.")
        return

    result = await provision_clone(
        token=token, owner_id=owner_id, owner_username=creator.get("username") or "",
        owner_name=f"{creator.get('first_name') or ''} {creator.get('last_name') or ''}".strip(), method="managed",
    )
    if not result["ok"]:
        await _tell(owner_id, result["error"])
        return
    await _tell(owner_id, next_steps_text(result["me"].username or bot.get("username", ""), managed=True))


async def _tell(user_id: int, text: str) -> None:
    client = MASTER.get("client")
    if not client:
        return
    try:
        await client.send_message(user_id, text)
    except Exception as exc:
        log.warning("Could not message user %s: %s", user_id, exc)


async def poll_managed_updates() -> None:
    """Background task (started in main.py): long-polls ONLY the `managed_bot` updates of the master bot."""
    offset = 0
    log.info("Managed-bots listener started")
    while True:
        try:
            updates = await call(MASTER_BOT_TOKEN, "getUpdates", {"offset": offset, "timeout": 15, "allowed_updates": ["managed_bot"]})
            for upd in updates or []:
                offset = int(upd["update_id"]) + 1
                if upd.get("managed_bot"):
                    asyncio.create_task(_safe_handle(upd["managed_bot"]))
        except asyncio.CancelledError:
            raise
        except BotApiError as exc:
            log.warning("Managed-bots listener: %s (retry in 15s)", exc)
            await asyncio.sleep(15)
        except Exception as exc:
            log.warning("Managed-bots listener error: %s (retry in 5s)", exc)
            await asyncio.sleep(5)


async def _safe_handle(update: Dict[str, Any]) -> None:
    try:
        await handle_managed_bot_update(update)
    except Exception:
        log.exception("Managed bot update failed")
