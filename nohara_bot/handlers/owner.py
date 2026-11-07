"""
NOHARA STORE BOT — Store bot — owner commands (/setdbchannel, /addproduct, /setgateway, /setbanner, /addmoney ...).
(nohara_bot/handlers/owner.py)
"""

import html
import re
import uuid
from hydrogram import Client
from hydrogram.types import Message
from typing import Optional

from nohara_bot.config import APP_URL
from nohara_bot.database import db
from nohara_bot.fonts import FONT_MODES
from nohara_bot.handlers.admin import screen_home
from nohara_bot.handlers.common import (
    DIVIDER,
    BANNER_FIELDS,
    _args,
    _banner_key,
    _rest_of_text,
    owner_only,
)
from nohara_bot.i18n import SUPPORTED_LANG_CODES
from nohara_bot.services.core import (
    send_ui,
    Ctx,
    fmt_usd,
    parse_usd_to_cents,
    send_audit,
)


@owner_only
async def cmd_dashboard(client: Client, message: Message, ctx: Ctx) -> None:
    """/admin and /dashboard — open the button based Admin Panel."""
    text, kb = await screen_home(ctx)
    await send_ui(client, message.chat.id, text, kb)


async def _set_channel(client: Client, message: Message, ctx: Ctx, kind: str) -> None:
    args = _args(message)
    if not args or not re.fullmatch(r"-?\d+", args[0]):
        await message.reply_text(f"<b>Usage:</b> <code>/set{kind}channel -100XXXXXXXXXX</code>")
        return
    channel_id = int(args[0])
    try:
        await client.send_message(channel_id, f"✅ <b>{kind.upper()} channel connected</b> — {html.escape(str(ctx.clone.get('bot_username', '')))}")
    except Exception as exc:
        await message.reply_text(
            "❌ I could not post in that channel. Add me as <b>ADMIN</b> with permission to post messages, then try again.\n"
            f"<code>{html.escape(str(exc))}</code>"
        )
        return
    if kind == "db":
        await db.set_channels(client.nohara_bot_id, db_channel_id=channel_id)
    else:
        await db.set_channels(client.nohara_bot_id, log_channel_id=channel_id)
    await message.reply_text(f"✅ <b>{kind.upper()} channel saved:</b> <code>{channel_id}</code>")


@owner_only
async def cmd_setdbchannel(client: Client, message: Message, ctx: Ctx) -> None:
    await _set_channel(client, message, ctx, "db")


@owner_only
async def cmd_setlogchannel(client: Client, message: Message, ctx: Ctx) -> None:
    await _set_channel(client, message, ctx, "log")


@owner_only
async def cmd_setgateway(client: Client, message: Message, ctx: Ctx) -> None:
    bot_id = client.nohara_bot_id
    args = _args(message)
    usage = (
        "<b>Usage:</b>\n<code>/setgateway oxapay API_KEY</code> | <code>/setgateway oxapay off</code>\n"
        "<code>/setgateway fampay UPI_ID</code> | <code>/setgateway fampay off</code>\n"
        "<code>/setgateway fampay_secret SECRET</code>"
    )
    if len(args) < 2:
        await message.reply_text(usage)
        return
    gateway, value = args[0].lower(), args[1]
    off = value.lower() in ("off", "disable", "disabled")

    if gateway == "oxapay":
        if off:
            await db.set_oxapay(bot_id, enabled=False)
            reply = "✅ OxaPay disabled."
        else:
            await db.set_oxapay(bot_id, enabled=True, api_key=value)
            reply = "✅ OxaPay enabled. Your API key is stored encrypted."
            if not APP_URL:
                reply += "\n⚠️ APP_URL is not set on the server, so crypto checkout will not work yet."
    elif gateway == "fampay":
        if off:
            await db.set_fampay(bot_id, enabled=False)
            reply = "✅ UPI / FamPay disabled."
        else:
            await db.set_fampay(bot_id, enabled=True, upi_id=value)
            reply = (
                f"✅ UPI enabled with <code>{html.escape(value)}</code>.\n"
                "Buyers send their UTR / screenshot to the bot and you approve it with a button."
            )
    elif gateway == "fampay_secret":
        await db.set_fampay(bot_id, webhook_secret=value)
        reply = (
            "✅ Webhook secret saved (encrypted).\n"
            f"Signed UPI webhook URL: <code>{APP_URL or 'APP_URL-not-set'}/webhook/fampay/{bot_id}</code>"
        )
    else:
        await message.reply_text(usage)
        return

    # The message contained a secret — remove it from the chat.
    try:
        await message.delete()
    except Exception:
        pass
    await client.send_message(message.chat.id, reply)


@owner_only
async def cmd_setrate(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    try:
        rate = float(args[0])
        if rate <= 0 or rate > 100000:
            raise ValueError
    except (IndexError, ValueError):
        await message.reply_text("<b>Usage:</b> <code>/setrate 88</code>")
        return
    await db.update_clone_fields(client.nohara_bot_id, {"usd_to_inr": rate})
    await message.reply_text(f"✅ 1 USD = ₹{rate}")


@owner_only
async def cmd_setfont(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    if not args or args[0] not in FONT_MODES:
        await message.reply_text("<b>Usage:</b> <code>/setfont MODE</code>\n" + "\n".join(f"• <code>{m}</code>" for m in FONT_MODES))
        return
    await db.update_clone_fields(client.nohara_bot_id, {"font_style": args[0]})
    await message.reply_text(f"✅ Font style: <code>{args[0]}</code>")


@owner_only
async def cmd_setlang(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    if not args or args[0] not in SUPPORTED_LANG_CODES:
        await message.reply_text("<b>Usage:</b> <code>/setlang hi</code>\n" + ", ".join(SUPPORTED_LANG_CODES))
        return
    await db.update_clone_fields(client.nohara_bot_id, {"default_language": args[0]})
    await message.reply_text(f"✅ Default language: <code>{args[0]}</code>")


@owner_only
async def cmd_setwelcome(client: Client, message: Message, ctx: Ctx) -> None:
    text = _rest_of_text(message)
    if not text:
        await message.reply_text("<b>Usage:</b> <code>/setwelcome your text</code> or <code>/setwelcome reset</code>")
        return
    if text.lower() == "reset":
        text = ""
    await db.update_clone_fields(client.nohara_bot_id, {"welcome_msg": text[:500]})
    await message.reply_text("✅ Welcome message updated.")


def _has_banner_media(msg: Optional[Message]) -> bool:
    return bool(msg and (msg.photo or msg.animation or msg.video))


IMAGE_CMD_KEYS = {
    "setstartimage": "start", "setcatalogimage": "catalog", "setwalletimage": "wallet", "setstockimage": "stock",
    "setproductimage": "product", "setoxapayimage": "oxapay", "setfampayimage": "fampay",
    "delproductimage": "product", "deloxapayimage": "oxapay", "delfampayimage": "fampay",
    "delstartimage": "start", "delcatalogimage": "catalog", "delwalletimage": "wallet", "delstockimage": "stock",
}

BANNER_USAGE = (
    "<b>🖼 Banner / start images</b>\n"
    "Send a photo (or GIF/video) with the caption — or reply to one with — any of these:\n"
    "<code>/setstartimage</code> — welcome / start screen\n"
    "<code>/setcatalogimage</code> — product list &amp; product pages\n"
    "<code>/setwalletimage</code> — wallet screen\n"
    "<code>/setstockimage</code> — shown when a product is out of stock\n"
    "<code>/setproductimage</code> — single product page (falls back to the catalog image)\n"
    "<code>/setoxapayimage</code> — crypto (OxaPay) payment screen\n"
    "<code>/setfampayimage</code> — UPI / FamPay payment screen\n"
    "Same thing: <code>/setbanner start</code> · <code>/setimage catalog</code> (names: start, catalog, wallet, stock)\n"
    "Remove: <code>/delstartimage</code> · <code>/delcatalogimage</code> · <code>/delwalletimage</code> · "
    "<code>/delstockimage</code> or <code>/delbanner NAME</code>"
)


@owner_only
async def cmd_setbanner(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    cmd_name = (message.command[0] if message.command else "").lower()
    key = IMAGE_CMD_KEYS.get(cmd_name) or (_banner_key(args[0]) if args else None)
    replied = message.reply_to_message
    source = message if _has_banner_media(message) else (replied if _has_banner_media(replied) else None)
    if not key or not source:
        await message.reply_text(BANNER_USAGE)
        return
    if not ctx.clone.get("db_channel_id"):
        await message.reply_text("Set your DB channel first: <code>/setdbchannel -100XXXXXXXXXX</code>")
        return
    copied = await client.copy_message(
        chat_id=int(ctx.clone["db_channel_id"]),
        from_chat_id=message.chat.id,
        message_id=source.id,
        caption=f"🖼 banner: {key}",
    )
    await db.update_clone_fields(client.nohara_bot_id, {f"ui_branding.{BANNER_FIELDS[key]}": copied.id})
    await message.reply_text(f"✅ Banner <b>{key}</b> saved. Send /start to see it.")


@owner_only
async def cmd_delbanner(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    cmd_name = (message.command[0] if message.command else "").lower()
    key = IMAGE_CMD_KEYS.get(cmd_name) or (_banner_key(args[0]) if args else None)
    if not key:
        await message.reply_text("<b>Usage:</b> <code>/delbanner start|catalog|wallet|stock</code>")
        return
    await db.update_clone_fields(client.nohara_bot_id, {f"ui_branding.{BANNER_FIELDS[key]}": 0})
    await message.reply_text(f"✅ Banner <b>{key}</b> removed.")


async def _resolve_folder(bot_id: str, ctx: Ctx, raw: str) -> str:
    """Folder by id or name; an unknown name creates a new folder on the fly."""
    raw = raw.strip()
    if not raw:
        return ""
    for f in ctx.clone.get("folders") or []:
        if raw == f["id"] or raw.lower() == f["name"].lower():
            return f["id"]
    return await db.add_folder(bot_id, raw[:40])


@owner_only
async def cmd_addfolder(client: Client, message: Message, ctx: Ctx) -> None:
    name = _rest_of_text(message)[:40].strip()
    if not name:
        await message.reply_text("<b>Usage:</b> <code>/addfolder Folder Name</code>")
        return
    if any(f["name"].lower() == name.lower() for f in ctx.clone.get("folders") or []):
        await message.reply_text("ℹ️ A folder with this name already exists. See /folders")
        return
    folder_id = await db.add_folder(client.nohara_bot_id, name)
    await message.reply_text(
        f"✅ Folder <b>{html.escape(name)}</b> created — ID <code>{folder_id}</code>\n"
        f"Add a product to it: <code>/addproduct Title | 9.99 | 10 | {html.escape(name)}</code>\n"
        f"Or move one: <code>/setfolder PRODUCT_ID {folder_id}</code>"
    )


@owner_only
async def cmd_folders(client: Client, message: Message, ctx: Ctx) -> None:
    folders = ctx.clone.get("folders") or []
    if not folders:
        await message.reply_text("No folders yet. Create one with <code>/addfolder Name</code>")
        return
    products = await db.list_products(client.nohara_bot_id)
    lines = [
        f"🗂 <b>{html.escape(f['name'])}</b> — {sum(1 for p in products if p.get('folder') == f['id'])} product(s) — <code>{f['id']}</code>"
        for f in folders
    ]
    await message.reply_text("<b>Your folders</b>\n" + "\n".join(lines))


@owner_only
async def cmd_delfolder(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    if not args:
        await message.reply_text("<b>Usage:</b> <code>/delfolder FOLDER_ID</code> (products are kept)")
        return
    deleted = await db.delete_folder(client.nohara_bot_id, args[0])
    await message.reply_text("✅ Folder deleted. Its products are now under “Other products”." if deleted else "❌ Folder not found.")


@owner_only
async def cmd_setfolder(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    if len(args) < 2:
        await message.reply_text("<b>Usage:</b> <code>/setfolder PRODUCT_ID FOLDER_ID</code> (use <code>none</code> to remove it from a folder)")
        return
    folder_id = "" if args[1].lower() == "none" else args[1]
    if folder_id and folder_id not in {f["id"] for f in ctx.clone.get("folders") or []}:
        await message.reply_text("❌ Folder not found. See /folders")
        return
    moved = await db.set_product_folder(client.nohara_bot_id, args[0], folder_id)
    await message.reply_text("✅ Product moved." if moved else "❌ Product not found. See /products")


@owner_only
async def cmd_addproduct(client: Client, message: Message, ctx: Ctx) -> None:
    bot_id = client.nohara_bot_id
    usage = (
        "<b>Usage:</b> send a file with the caption\n<code>/addproduct Title | 9.99 | 10 | Folder</code>\n"
        "(or reply to an already sent file with the same command). Price is in USD; stock and folder are optional (empty stock = unlimited; a new folder name is created automatically)."
    )
    parts = [p.strip() for p in _rest_of_text(message).split("|")]
    if len(parts) < 2 or not parts[0]:
        await message.reply_text(usage)
        return
    title = parts[0][:60]
    price_cents = parse_usd_to_cents(parts[1])
    if price_cents is None:
        await message.reply_text("❌ Invalid price.\n" + usage)
        return
    stock = -1
    if len(parts) >= 3 and parts[2]:
        try:
            stock = int(parts[2])
        except ValueError:
            stock = -2
        if stock < -1:
            await message.reply_text("❌ Stock must be a number (or empty for unlimited).\n" + usage)
            return

    source = message if message.media else (message.reply_to_message if (message.reply_to_message and message.reply_to_message.media) else None)
    if not source:
        await message.reply_text(usage)
        return
    if not ctx.clone.get("db_channel_id"):
        await message.reply_text("Set your DB channel first: <code>/setdbchannel -100XXXXXXXXXX</code>")
        return

    folder_id = await _resolve_folder(bot_id, ctx, parts[3]) if len(parts) >= 4 else ""
    product_id = uuid.uuid4().hex[:8]
    stored = await client.copy_message(
        chat_id=int(ctx.clone["db_channel_id"]),
        from_chat_id=message.chat.id,
        message_id=source.id,
        caption=f"📦 <b>{html.escape(title)}</b>\n{fmt_usd(price_cents)} • ID <code>{product_id}</code>",
    )
    await db.save_product(bot_id, product_id, title, price_cents, stock, stored.id, folder_id)
    await message.reply_text(
        f"✅ <b>Product saved</b>\n• {html.escape(title)}\n• Price: {fmt_usd(price_cents)}\n"
        f"• Stock: {'unlimited' if stock == -1 else stock}\n"
        + (f"• Folder: {html.escape(parts[3])}\n" if folder_id else "")
        + f"• ID: <code>{product_id}</code>"
    )
    await send_audit(
        ctx.clone,
        f"<b>[NOHARA AUDIT — ASSET UPLOAD]</b>\n• Clone Bot: <code>{html.escape(str(ctx.clone.get('bot_username', '')))}</code> (<code>{bot_id}</code>)\n"
        f"• Product: <b>{html.escape(title)}</b> (<code>{product_id}</code>) | DB Msg Pointer: <code>#{stored.id}</code>",
    )


@owner_only
async def cmd_products(client: Client, message: Message, ctx: Ctx) -> None:
    products = await db.list_products(client.nohara_bot_id)
    if not products:
        await message.reply_text("No products yet. Use <code>/addproduct</code>.")
        return
    lines = [
        f"• <b>{html.escape(p['title'])}</b> — {fmt_usd(p['price_cents'])} — stock {'∞' if p['stock'] == -1 else p['stock']} — <code>{p['_id']}</code>"
        for p in products
    ]
    await message.reply_text("<b>Your products</b>\n" + "\n".join(lines))


@owner_only
async def cmd_delproduct(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    if not args:
        await message.reply_text("<b>Usage:</b> <code>/delproduct PRODUCT_ID</code>")
        return
    deleted = await db.delete_product(client.nohara_bot_id, args[0])
    await message.reply_text("✅ Product removed." if deleted else "❌ Product not found.")


@owner_only
async def cmd_addfsub(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    if not args or not re.fullmatch(r"-?\d+", args[0]):
        await message.reply_text("<b>Usage:</b> <code>/addfsub -100XXXXXXXXXX [invite_link]</code>")
        return
    channel_id = int(args[0])
    try:
        chat = await client.get_chat(channel_id)
    except Exception as exc:
        await message.reply_text(f"❌ I cannot see that channel. Add me as ADMIN there first.\n<code>{html.escape(str(exc))}</code>")
        return
    link = args[1] if len(args) > 1 else ""
    if not link:
        try:
            link = await client.export_chat_invite_link(channel_id)
        except Exception:
            link = getattr(chat, "invite_link", "") or (f"https://t.me/{chat.username}" if getattr(chat, "username", None) else "")
    await db.add_fsub(client.nohara_bot_id, channel_id, chat.title or "Channel", link or "")
    await message.reply_text(f"✅ Force-subscribe added: <b>{html.escape(chat.title or 'Channel')}</b>")


@owner_only
async def cmd_removefsub(client: Client, message: Message, ctx: Ctx) -> None:
    args = _args(message)
    if not args or not re.fullmatch(r"-?\d+", args[0]):
        await message.reply_text("<b>Usage:</b> <code>/removefsub -100XXXXXXXXXX</code>")
        return
    removed = await db.remove_fsub(client.nohara_bot_id, int(args[0]))
    await message.reply_text("✅ Removed." if removed else "❌ That channel was not in the list.")


@owner_only
async def cmd_fsublist(client: Client, message: Message, ctx: Ctx) -> None:
    channels = ctx.clone.get("fsub_channels", [])
    if not channels:
        await message.reply_text("No force-subscribe channels.")
        return
    lines = [f"• {html.escape(c.get('title') or '')} — <code>{c['channel_id']}</code>" for c in channels]
    await message.reply_text("<b>Force-subscribe channels</b>\n" + "\n".join(lines))


async def _money_command(client: Client, message: Message, ctx: Ctx, add: bool) -> None:
    bot_id = client.nohara_bot_id
    args = _args(message)
    name = "addmoney" if add else "removemoney"
    if len(args) < 2 or not re.fullmatch(r"\d+", args[0]):
        await message.reply_text(f"<b>Usage:</b> <code>/{name} USER_ID AMOUNT_USD</code>")
        return
    target_id = int(args[0])
    cents = parse_usd_to_cents(args[1])
    if cents is None:
        await message.reply_text("❌ Invalid amount.")
        return
    if add:
        balance = await db.credit(bot_id, target_id, cents)
    else:
        if not await db.debit(bot_id, target_id, cents):
            await message.reply_text(f"❌ Insufficient balance. Current: {fmt_usd(await db.get_balance(bot_id, target_id))}")
            return
        balance = await db.get_balance(bot_id, target_id)
    await message.reply_text(f"✅ {'Added' if add else 'Removed'} {fmt_usd(cents)} — user <code>{target_id}</code> balance: <b>{fmt_usd(balance)}</b>")
    if add:
        try:
            await client.send_message(target_id, f"💰 {fmt_usd(cents)} added to your wallet. Balance: <b>{fmt_usd(balance)}</b>")
        except Exception:
            pass
    await send_audit(
        ctx.clone,
        f"<b>[NOHARA AUDIT — WALLET {'CREDIT' if add else 'DEBIT'}]</b>\n• Clone Bot: <code>{bot_id}</code>\n"
        f"• User: <code>{target_id}</code> | Amount: <b>{fmt_usd(cents)}</b> | By owner: <code>{message.from_user.id}</code>",
    )


@owner_only
async def cmd_addmoney(client: Client, message: Message, ctx: Ctx) -> None:
    await _money_command(client, message, ctx, add=True)


@owner_only
async def cmd_removemoney(client: Client, message: Message, ctx: Ctx) -> None:
    await _money_command(client, message, ctx, add=False)
