"""
NOHARA STORE BOT — Master bot (/start /clone /myclones /deleteclone) and the clone start/stop lifecycle.
(nohara_bot/handlers/master.py)
"""

import html
import logging
import re
from hydrogram import Client, filters
from hydrogram.handlers import MessageHandler
from hydrogram.types import Message
from typing import Any, Dict

from nohara_bot.config import MASTER_AUDIT_CHANNEL_ID, MAX_CLONES_PER_USER, MAX_CLONES_TOTAL
from nohara_bot.database import db
from nohara_bot.handlers.common import _args, _is_admin
from nohara_bot.handlers.menu import apply_command_menu
from nohara_bot.handlers.registry import register_store_handlers
from nohara_bot.services.core import ACTIVE_CLONE_CLIENTS, make_client


log = logging.getLogger("nohara.handlers.master")


TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

# ═════════════════════════════════════════════════════════════════════
#  Clone lifecycle
# ═════════════════════════════════════════════════════════════════════
async def start_clone_client(bot_id: str, token: str) -> Client:
    client = make_client(f"clone_{bot_id}", token)
    register_store_handlers(client, bot_id)
    await client.start()
    ACTIVE_CLONE_CLIENTS[str(bot_id)] = client
    return client


async def stop_clone_client(bot_id: str) -> None:
    client = ACTIVE_CLONE_CLIENTS.pop(str(bot_id), None)
    if client:
        try:
            await client.stop()
        except Exception:
            log.exception("Error while stopping clone %s", bot_id)


async def boot_clone(clone_doc: Dict[str, Any]) -> bool:
    """Starts one saved clone at boot. A broken clone must NEVER stop the whole platform."""
    bot_id = str(clone_doc["_id"])
    token = db.get_clone_token(clone_doc)
    if not token:
        log.error("Clone %s has no readable token (wrong ENCRYPTION_KEY?). Skipped.", bot_id)
        return False
    try:
        clone_client = await start_clone_client(bot_id, token)
        await apply_command_menu(clone_client, [clone_doc["owner_id"]])
        log.info("Clone started: %s", clone_doc.get("bot_username", bot_id))
        return True
    except Exception as exc:
        log.error("Clone %s failed to start (%s). Skipped.", bot_id, exc)
        return False


# ═════════════════════════════════════════════════════════════════════
#  MASTER BOT handlers
# ═════════════════════════════════════════════════════════════════════
async def master_clone(client: Client, message: Message) -> None:
    if not message.from_user:
        return
    args = _args(message)
    if not args:
        await message.reply_text("<b>Usage:</b> <code>/clone BOT_TOKEN</code>")
        return
    token = args[0].strip()
    owner_id = message.from_user.id

    # The message contains a secret token -> delete it from the chat immediately.
    try:
        await message.delete()
    except Exception:
        pass

    async def say(text: str) -> None:
        await client.send_message(message.chat.id, text)

    if not TOKEN_RE.match(token):
        await say("❌ That does not look like a valid bot token.")
        return
    bot_id = token.split(":")[0]

    existing = await db.get_clone(bot_id)
    if existing and existing["owner_id"] != owner_id and not _is_admin(owner_id):
        await say("❌ This bot is already registered by another user.")
        return
    if bot_id in ACTIVE_CLONE_CLIENTS:
        await say("ℹ️ This bot is already running.")
        return
    if not existing:
        owned = await db.get_clones_by_owner(owner_id)
        if len(owned) >= MAX_CLONES_PER_USER and not _is_admin(owner_id):
            await say(f"❌ You can create at most {MAX_CLONES_PER_USER} bots.")
            return
        if await db.count_clones() >= MAX_CLONES_TOTAL:
            await say("❌ The platform has reached its bot limit. Please try again later.")
            return

    await say("⏳ Starting your bot…")
    try:
        clone_client = await start_clone_client(bot_id, token)
        me = await clone_client.get_me()
    except Exception as exc:
        await stop_clone_client(bot_id)
        await say(f"❌ Could not start this bot. Is the token correct and not revoked?\n<code>{html.escape(str(exc))}</code>")
        return

    await db.register_clone(bot_id, owner_id, f"@{me.username}", token)
    await apply_command_menu(clone_client, [owner_id])  # user + admin commands, automatically
    if MASTER_AUDIT_CHANNEL_ID:
        try:
            await client.send_message(
                MASTER_AUDIT_CHANNEL_ID,
                f"<b>[MASTER AUDIT — NEW CLONE PROVISIONED]</b>\n• Clone Bot: @{html.escape(me.username or '')} (<code>{me.id}</code>)\n"
                f"• Owner ID: <code>{owner_id}</code>",
            )
        except Exception as exc:
            log.warning("Master audit post failed: %s", exc)

    await say(
        f"✅ <b>Clone active:</b> @{html.escape(me.username or '')}\n\n"
        "<b>Next steps</b> (open your new bot and send these commands):\n"
        "1. Create a PRIVATE channel for your files, add the bot as ADMIN, then:\n"
        "   <code>/setdbchannel -100XXXXXXXXXX</code>\n"
        "2. (optional) a log channel: <code>/setlogchannel -100XXXXXXXXXX</code>\n"
        "3. Add products: send a file with caption <code>/addproduct Title | 9.99</code>\n"
        "4. Payments: <code>/setgateway oxapay API_KEY</code> or <code>/setgateway fampay UPI_ID</code>\n\n"
        "Send <code>/help</code> in your new bot for all commands.\n"
        "🔒 Your token was deleted from this chat and is stored encrypted."
    )


async def master_myclones(client: Client, message: Message) -> None:
    if not message.from_user:
        return
    clones = await db.get_clones_by_owner(message.from_user.id)
    if not clones:
        await message.reply_text("You have no bots yet. Use <code>/clone BOT_TOKEN</code>.")
        return
    lines = []
    for c in clones:
        running = "🟢 running" if c["_id"] in ACTIVE_CLONE_CLIENTS else "🔴 stopped"
        lines.append(f"• {html.escape(str(c.get('bot_username', '')))} — <code>{c['_id']}</code> — {running}")
    await message.reply_text("<b>Your bots</b>\n" + "\n".join(lines))


async def master_deleteclone(client: Client, message: Message) -> None:
    if not message.from_user:
        return
    args = _args(message)
    if not args:
        await message.reply_text("<b>Usage:</b> <code>/deleteclone BOT_ID</code>")
        return
    bot_id = args[0].strip()
    clone = await db.get_clone(bot_id)
    user_id = message.from_user.id
    if clone and clone.get("is_master"):
        await message.reply_text("❌ The master store cannot be deleted.")
        return
    if not clone or (clone["owner_id"] != user_id and not _is_admin(user_id)):
        await message.reply_text("❌ Bot not found.")
        return
    await stop_clone_client(bot_id)
    await db.delete_clone(bot_id)
    if MASTER_AUDIT_CHANNEL_ID:
        try:
            await client.send_message(
                MASTER_AUDIT_CHANNEL_ID,
                f"<b>[MASTER AUDIT — CLONE DELETED]</b>\n• Clone Bot: <code>{html.escape(str(clone.get('bot_username', '')))}</code> (<code>{bot_id}</code>)\n• By: <code>{user_id}</code>",
            )
        except Exception:
            pass
    await message.reply_text(
        f"✅ {html.escape(str(clone.get('bot_username', '')))} was stopped and removed.\n"
        "Tip: revoke its token in @BotFather if you will not use it anymore."
    )


def register_master_handlers(client: Client) -> None:
    """Adds the platform commands to the master bot. (Its store features come from register_store_handlers.)"""
    private = filters.private
    client.add_handler(MessageHandler(master_clone, private & filters.command("clone")))
    client.add_handler(MessageHandler(master_myclones, private & filters.command("myclones")))
    client.add_handler(MessageHandler(master_deleteclone, private & filters.command("deleteclone")))
