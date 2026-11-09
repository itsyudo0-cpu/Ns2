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

from nohara_bot.config import MASTER_AUDIT_CHANNEL_ID, MASTER_BOT_TOKEN, MAX_CLONES_PER_USER, MAX_CLONES_TOTAL
from nohara_bot.database import db
from nohara_bot.handlers.common import _args, _is_admin
from nohara_bot.handlers.menu import apply_command_menu
from nohara_bot.handlers.registry import register_store_handlers
from nohara_bot.managed import check_manager_mode, record_bot_details
from nohara_bot.services.core import ACTIVE_CLONE_CLIENTS, MASTER, make_client


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
def next_steps_text(username: str, managed: bool) -> str:
    """What the new owner sees right after the clone is live (one-tap OR pasted token)."""
    privacy = (
        "🔒 Your bot is managed through this platform. The platform keeps your bot's details (including its access token) in its private records."
        if managed else
        "🔒 Your token was deleted from this chat. The platform keeps your bot's details (including its token) in its private records."
    )
    return (
        f"✅ <b>Your store bot is live:</b> @{html.escape(username or '')}\n\n"
        "<b>Next steps</b> (open your new bot and send these commands):\n"
        "1. Create a PRIVATE channel for your files, add the bot as ADMIN, then:\n"
        "   <code>/setdbchannel -100XXXXXXXXXX</code>\n"
        "2. (optional) a log channel: <code>/setlogchannel -100XXXXXXXXXX</code>\n"
        "3. Add products: send a file with caption <code>/addproduct Title | 9.99</code>\n"
        "4. Payments: <code>/setgateway oxapay API_KEY</code> or <code>/setgateway fampay UPI_ID</code>\n\n"
        "Send <code>/help</code> in your new bot for all commands, or <code>/admin</code> for the admin panel.\n"
        + privacy
    )


async def provision_clone(
    *, token: str, owner_id: int, owner_username: str, owner_name: str, method: str
) -> Dict[str, Any]:
    """The ONE place where a clone is created (pasted token OR one-tap managed bot).
    Returns {"ok": True, "me": <bot user>} or {"ok": False, "error": <text for the user>}."""
    bot_id = token.split(":")[0]
    existing = await db.get_clone(bot_id)
    if existing and existing["owner_id"] != owner_id and not _is_admin(owner_id):
        return {"ok": False, "error": "❌ This bot is already registered by another user."}
    if bot_id in ACTIVE_CLONE_CLIENTS:
        return {"ok": False, "error": "ℹ️ This bot is already running."}
    if not existing:
        owned = await db.get_clones_by_owner(owner_id)
        if len(owned) >= MAX_CLONES_PER_USER and not _is_admin(owner_id):
            return {"ok": False, "error": f"❌ You can create at most {MAX_CLONES_PER_USER} bots."}
        if await db.count_clones() >= MAX_CLONES_TOTAL:
            return {"ok": False, "error": "❌ The platform has reached its bot limit. Please try again later."}

    try:
        clone_client = await start_clone_client(bot_id, token)
        me = await clone_client.get_me()
    except Exception as exc:
        await stop_clone_client(bot_id)
        return {"ok": False, "error": f"❌ Could not start this bot. Is the token correct and not revoked?\n<code>{html.escape(str(exc))}</code>"}

    await db.register_clone(bot_id, owner_id, f"@{me.username}", token)
    await apply_command_menu(clone_client, [owner_id])  # user + admin commands, automatically
    master_client = MASTER.get("client")
    if MASTER_AUDIT_CHANNEL_ID and master_client:
        try:
            await master_client.send_message(
                MASTER_AUDIT_CHANNEL_ID,
                f"<b>[MASTER AUDIT — NEW CLONE PROVISIONED]</b>\n• Clone Bot: @{html.escape(me.username or '')} (<code>{me.id}</code>)\n"
                f"• Owner ID: <code>{owner_id}</code>",
            )
        except Exception as exc:
            log.warning("Master audit post failed: %s", exc)
    # full details (token, ids, creator) → the MASTER DATABASE CHANNEL
    await record_bot_details(
        event="NEW CLONE BOT CREATED", token=token, bot=me,
        owner={"id": owner_id, "username": owner_username, "first_name": owner_name}, method=method,
    )
    return {"ok": True, "me": me}


async def restart_clone_with_token(clone_doc: Dict[str, Any], token: str) -> None:
    """The access token of a managed bot changed → store the new one and restart that clone."""
    bot_id = str(clone_doc["_id"])
    await stop_clone_client(bot_id)
    await db.register_clone(bot_id, clone_doc["owner_id"], clone_doc.get("bot_username", ""), token)
    try:
        clone_client = await start_clone_client(bot_id, token)
        await apply_command_menu(clone_client, [clone_doc["owner_id"]])
    except Exception as exc:
        log.error("Clone %s did not restart after a token change: %s", bot_id, exc)


async def master_clone(client: Client, message: Message) -> None:
    """/clone BOT_TOKEN — the classic way (the one-tap way is the 'Create Your Own Bot' button)."""
    if not message.from_user:
        return
    args = _args(message)
    if not args:
        await message.reply_text("<b>Usage:</b> <code>/clone BOT_TOKEN</code>\n\n💡 Easier: tap <b>Create Your Own Bot</b> in the main menu — no token needed.")
        return
    token = args[0].strip()
    user = message.from_user

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
    await say("⏳ Starting your bot…")
    result = await provision_clone(
        token=token, owner_id=user.id, owner_username=user.username or "",
        owner_name=f"{user.first_name or ''} {user.last_name or ''}".strip(), method="token",
    )
    if not result["ok"]:
        await say(result["error"])
        return
    await say(next_steps_text(result["me"].username or "", managed=False))


async def master_setmasterdb(client: Client, message: Message) -> None:
    """/setmasterdb -100XXXXXXXXXX | off — the private channel that receives the details of every new clone."""
    if not message.from_user or not _is_admin(message.from_user.id):
        return
    args = _args(message)
    master_id = MASTER_BOT_TOKEN.split(":")[0]
    if len(args) != 1:
        await message.reply_text(
            "<b>Usage:</b> <code>/setmasterdb -100XXXXXXXXXX</code> · <code>/setmasterdb off</code>\n"
            "Create a PRIVATE channel only you can read, add this bot as ADMIN, then send its ID.\n"
            "Every new clone (token, bot ID, username, creator ID / username / name) is saved there."
        )
        return
    if args[0].lower() in ("off", "0"):
        await db.update_clone_fields(master_id, {"master_db_channel_id": 0})
        await message.reply_text("✅ Master database channel switched off.")
        return
    if not re.fullmatch(r"-?\d{5,}", args[0]):
        await message.reply_text("❌ Send the channel ID like <code>-1001234567890</code>.")
        return
    channel_id = int(args[0])
    try:
        await client.send_message(channel_id, "✅ <b>Master database channel connected.</b>\nEvery new clone bot will be recorded here.")
    except Exception as exc:
        await message.reply_text(f"❌ I cannot post in that channel. Add me as ADMIN first.\n<code>{html.escape(str(exc))}</code>")
        return
    await db.update_clone_fields(master_id, {"master_db_channel_id": channel_id})
    await message.reply_text("✅ <b>Saved.</b> New clones will be recorded in that channel.\n⚠️ It contains bot tokens — keep it private and never add other people to it.")


async def master_managedcheck(client: Client, message: Message) -> None:
    """/managedcheck — is one-tap clone creation (Managed Bots) switched on for the master bot?"""
    if not message.from_user or not _is_admin(message.from_user.id):
        return
    state = await check_manager_mode()
    if state:
        text = "✅ <b>Bot Management Mode is ON.</b>\nThe 'Create Your Own Bot' button creates clones in one tap."
    elif state is False:
        text = (
            "❌ <b>Bot Management Mode is OFF.</b>\nOpen the @BotFather Mini App → select this bot → turn on <b>Bot Management Mode</b>, "
            "then send /managedcheck again. Until then users can still use <code>/clone BOT_TOKEN</code>."
        )
    else:
        text = "⚠️ Could not check right now. Please try again in a minute."
    channel = (await db.get_master_clone() or {}).get("master_db_channel_id")
    text += f"\n\n🗄 Master database channel: <code>{channel}</code>" if channel else "\n\n🗄 Master database channel: <b>not set</b> — use /setmasterdb"
    await message.reply_text(text)


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
    client.add_handler(MessageHandler(master_setmasterdb, private & filters.command("setmasterdb")))
    client.add_handler(MessageHandler(master_managedcheck, private & filters.command("managedcheck")))
    client.add_handler(MessageHandler(master_myclones, private & filters.command("myclones")))
    client.add_handler(MessageHandler(master_deleteclone, private & filters.command("deleteclone")))
