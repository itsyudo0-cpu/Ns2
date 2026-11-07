"""
NOHARA STORE BOT — Admin Panel (nohara_bot/handlers/admin.py)

Opens with /admin (or /dashboard, or the ⚙️ button in the menu) in the MASTER store AND in every
clone bot. Only the owner sees it. Everything is button based: tap a section, see its status,
and manage it with buttons. (Things that need typing — a name, an API key — show the exact
command to send.)
"""

import html
from typing import Any, Dict, List, Tuple

from hydrogram.types import CallbackQuery

from nohara_bot import botapi
from nohara_bot.buttons import btn, markup
from nohara_bot.database import db
from nohara_bot.fonts import FONT_MODES
from nohara_bot.handlers.common import BANNER_FIELDS, DIVIDER, _is_owner, show
from nohara_bot.services.core import ACTIVE_CLONE_CLIENTS, Ctx, fmt_usd

FONT = "default_html"
Screen = Tuple[str, Dict[str, Any]]


def B(text: str, cb: str, style: str = "success") -> Dict[str, Any]:
    return btn(text, FONT, style, cb)


BACK = [B("◀️ Admin Panel", "adm:home")]


def ok(flag: Any) -> str:
    return "✅" if flag else "❌"


def esc(value: Any) -> str:
    return html.escape(str(value))


# ═════════════════════════ Command guide ═════════════════════════
ADMIN_GUIDE = (
    "📖 <b>COMMAND GUIDE</b>\n" + DIVIDER + "\n"
    "<b>Setup</b>\n"
    "/setdbchannel <code>-100…</code> — private channel for your files\n"
    "/setlogchannel <code>-100…</code> — order log channel\n\n"
    "<b>Products &amp; folders</b>\n"
    "/addproduct <code>Title | 9.99 | 10 | Folder</code> — send with a file (stock &amp; folder optional)\n"
    "/products · /delproduct <code>ID</code>\n"
    "/addfolder <code>Name</code> · /folders · /delfolder <code>ID</code> · /setfolder <code>PRODUCT_ID FOLDER_ID</code>\n\n"
    "<b>Payments</b>\n"
    "/setgateway <code>oxapay API_KEY</code> · <code>oxapay off</code>\n"
    "/setgateway <code>fampay UPI_ID</code> · <code>fampay off</code>\n"
    "/setrate <code>88</code> — USD → INR\n\n"
    "<b>Look &amp; feel</b>\n"
    "/setstartimage · /setcatalogimage · /setproductimage · /setwalletimage · /setstockimage · "
    "/setoxapayimage · /setfampayimage — send a photo with the command as caption\n"
    "/delbanner <code>NAME</code> · /setfont <code>MODE</code> · /setlang <code>hi</code> · /setwelcome <code>text</code>\n\n"
    "<b>Users &amp; wallet</b>\n"
    "/addmoney <code>USER_ID 5</code> · /removemoney <code>USER_ID 5</code> (USD)\n"
    "/addfsub <code>-100…</code> · /removefsub <code>-100…</code> · /fsublist"
)
PLATFORM_GUIDE = (
    "\n\n<b>Platform (master only)</b>\n"
    "/clone <code>BOT_TOKEN</code> · /myclones · /deleteclone <code>BOT_ID</code>"
)


# ═════════════════════════ Screens ═════════════════════════
async def screen_home(ctx: Ctx) -> Screen:
    clone = ctx.clone
    bot_id = str(clone["_id"])
    stats = await db.get_stats(bot_id)
    gw = clone["gateways"]
    pay_on = bool((gw["oxapay"]["enabled"] and db.get_oxapay_key(clone)) or (gw["fampay"]["enabled"] and gw["fampay"]["upi_id"]))
    brand = clone.get("ui_branding") or {}
    tag = "👑 Master Store" if clone.get("is_master") else "🤖 Store Bot"
    text = (
        f"⚙️ <b>ADMIN PANEL</b>\n{DIVIDER}\n"
        f"{tag}  ·  {esc(clone.get('bot_username', ''))}\n\n"
        f"📊 <b>Overview</b>\n"
        f"📦 Products  <b>{stats['products']}</b>      👥 Users  <b>{stats['users']}</b>\n"
        f"🧾 Paid orders  <b>{stats['orders']}</b>      ✅ Delivered  <b>{stats['delivered']}</b>\n\n"
        f"🧭 <b>Setup checklist</b>\n"
        f"{ok(clone.get('db_channel_id'))} Files channel\n"
        f"{ok(stats['products'])} At least one product\n"
        f"{ok(pay_on)} A payment method\n"
        f"{ok(brand.get('start_photo_message_id'))} Start image\n"
        f"{ok(clone.get('log_channel_id'))} Log channel (optional)\n\n"
        f"<i>Tap a button to manage.</i>"
    )
    rows = [
        [B("📦 Products", "adm:products", "primary"), B("🗂 Folders", "adm:folders", "primary")],
        [B("💳 Payments", "adm:pay"), B("🖼 Images", "adm:images")],
        [B("📡 Channels", "adm:chan"), B("📢 Force-Sub", "adm:fsub")],
        [B("🎨 Appearance", "adm:look"), B("👛 Wallet & Users", "adm:wallet")],
    ]
    if clone.get("is_master"):
        rows.append([B("🧬 Clone Bots", "adm:clones", "primary")])
    rows.append([B("📖 Command Guide", "adm:help")])
    rows.append([B("🏠 Back to Store", "menu:home")])
    return text, markup(rows)


async def screen_products(ctx: Ctx) -> Screen:
    bot_id = str(ctx.clone["_id"])
    products = await db.list_products(bot_id)
    folders = {f["id"]: f["name"] for f in ctx.clone.get("folders") or []}
    lines = []
    rows: List[List[Dict[str, Any]]] = []
    for p in products[:10]:
        stock = "∞" if p["stock"] == -1 else p["stock"]
        where = f" · 🗂 {esc(folders[p['folder']])}" if p.get("folder") in folders else ""
        lines.append(f"• <b>{esc(p['title'])}</b> — {fmt_usd(p['price_cents'])} · stock {stock}{where}\n   <code>{p['_id']}</code>")
        rows.append([B(f"🗑 Delete: {p['title'][:28]}", f"adm:pdel:{p['_id']}", "danger")])
    body = "\n".join(lines) if lines else "<i>No products yet.</i>"
    more = f"\n<i>…and {len(products) - 10} more (see /products)</i>" if len(products) > 10 else ""
    text = (
        f"📦 <b>PRODUCTS</b>  ·  {len(products)}\n{DIVIDER}\n{body}{more}\n\n"
        f"➕ <b>Add a product</b>\nSend a file with the caption:\n<code>/addproduct Title | 9.99 | 10 | Folder</code>\n"
        f"<i>Price in USD · stock and folder are optional</i>"
    )
    rows.append(BACK)
    return text, markup(rows)


async def screen_folders(ctx: Ctx) -> Screen:
    bot_id = str(ctx.clone["_id"])
    folders = ctx.clone.get("folders") or []
    products = await db.list_products(bot_id)
    lines, rows = [], []
    for f in folders:
        n = sum(1 for p in products if p.get("folder") == f["id"])
        lines.append(f"🗂 <b>{esc(f['name'])}</b> — {n} product(s)\n   <code>{f['id']}</code>")
        rows.append([B(f"🗑 Delete: {f['name'][:28]}", f"adm:fdel:{f['id']}", "danger")])
    body = "\n".join(lines) if lines else "<i>No folders yet.</i>"
    text = (
        f"🗂 <b>FOLDERS</b>  ·  {len(folders)}\n{DIVIDER}\n{body}\n\n"
        f"➕ <b>Create</b>  <code>/addfolder Name</code>\n"
        f"➡️ <b>Move a product</b>  <code>/setfolder PRODUCT_ID FOLDER_ID</code>\n"
        f"<i>Customers see folders first; products without a folder appear under “Other products”. "
        f"Deleting a folder keeps its products.</i>"
    )
    rows.append(BACK)
    return text, markup(rows)


async def screen_pay(ctx: Ctx) -> Screen:
    clone = ctx.clone
    gw = clone["gateways"]
    ox_key = bool(db.get_oxapay_key(clone))
    upi = gw["fampay"]["upi_id"]
    text = (
        f"💳 <b>PAYMENTS</b>\n{DIVIDER}\n"
        f"🪙 <b>Crypto (OxaPay)</b>\n   Key saved: {ok(ox_key)}   Live: {ok(gw['oxapay']['enabled'] and ox_key)}\n"
        f"🇮🇳 <b>UPI / FamPay</b>\n   UPI ID: {('<code>' + esc(upi) + '</code>') if upi else '❌'}   Live: {ok(gw['fampay']['enabled'] and upi)}\n"
        f"👛 <b>Wallet</b>  always on\n"
        f"💱 Rate: 1 USD = ₹{clone.get('usd_to_inr')}\n\n"
        f"<b>Set up</b>\n<code>/setgateway oxapay API_KEY</code>\n<code>/setgateway fampay UPI_ID</code>\n<code>/setrate 88</code>"
    )
    ox_on = bool(gw["oxapay"]["enabled"])
    up_on = bool(gw["fampay"]["enabled"])
    rows = [
        [B("⏸ Disable OxaPay" if ox_on else "▶️ Enable OxaPay", f"adm:gw:oxapay:{'off' if ox_on else 'on'}", "danger" if ox_on else "success")],
        [B("⏸ Disable UPI" if up_on else "▶️ Enable UPI", f"adm:gw:fampay:{'off' if up_on else 'on'}", "danger" if up_on else "success")],
        BACK,
    ]
    return text, markup(rows)


async def screen_images(ctx: Ctx) -> Screen:
    brand = ctx.clone.get("ui_branding") or {}
    names = {"start": "Start / welcome", "catalog": "Product list", "product": "Product page", "wallet": "Wallet",
             "stock": "Out of stock", "oxapay": "Crypto payment", "fampay": "UPI payment"}
    lines, rows, row = [], [], []
    for key, field in BANNER_FIELDS.items():
        is_set = bool(brand.get(field))
        lines.append(f"{ok(is_set)} {names.get(key, key)}")
        if is_set:
            row.append(B(f"🗑 {key}", f"adm:bdel:{key}", "danger"))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    text = (
        f"🖼 <b>IMAGES</b>\n{DIVIDER}\n" + "\n".join(lines) + "\n\n"
        "<b>Set an image</b> — send a photo with the caption:\n"
        "<code>/setstartimage</code> · <code>/setcatalogimage</code> · <code>/setproductimage</code>\n"
        "<code>/setwalletimage</code> · <code>/setstockimage</code>\n"
        "<code>/setoxapayimage</code> · <code>/setfampayimage</code>\n"
        "<i>Needs the files channel to be set first. Missing images fall back to the catalog / wallet image.</i>"
    )
    rows.append(BACK)
    return text, markup(rows)


async def screen_chan(ctx: Ctx) -> Screen:
    c = ctx.clone
    text = (
        f"📡 <b>CHANNELS</b>\n{DIVIDER}\n"
        f"{ok(c.get('db_channel_id'))} <b>Files channel</b>  <code>{c.get('db_channel_id') or 'not set'}</code>\n"
        f"{ok(c.get('log_channel_id'))} <b>Log channel</b>  <code>{c.get('log_channel_id') or 'not set'}</code>\n\n"
        f"1️⃣ Create a <b>private</b> channel and add this bot as <b>Admin</b>\n"
        f"2️⃣ Send  <code>/setdbchannel -100XXXXXXXXXX</code>\n"
        f"3️⃣ (optional)  <code>/setlogchannel -100XXXXXXXXXX</code>"
    )
    return text, markup([BACK])


async def screen_fsub(ctx: Ctx) -> Screen:
    channels = ctx.clone.get("fsub_channels") or []
    lines = [f"📢 <b>{esc(c.get('title') or 'Channel')}</b>  <code>{c['channel_id']}</code>" for c in channels]
    rows = [[B(f"🗑 Remove: {(c.get('title') or 'Channel')[:26]}", f"adm:fsubdel:{c['channel_id']}", "danger")] for c in channels]
    text = (
        f"📢 <b>FORCE-SUBSCRIBE</b>  ·  {len(channels)}\n{DIVIDER}\n"
        + ("\n".join(lines) if lines else "<i>No channels. Users can use the store freely.</i>")
        + "\n\n➕ <code>/addfsub -100XXXXXXXXXX</code>\n<i>The bot must be Admin in that channel.</i>"
    )
    rows.append(BACK)
    return text, markup(rows)


async def screen_look(ctx: Ctx) -> Screen:
    c = ctx.clone
    current = c.get("font_style")
    text = (
        f"🎨 <b>APPEARANCE</b>\n{DIVIDER}\n"
        f"🔤 Font style: <code>{esc(current)}</code>\n"
        f"🌐 Default language: <code>{esc(c.get('default_language'))}</code>\n"
        f"💬 Welcome text: <i>{esc(c.get('welcome_msg') or 'default')}</i>\n\n"
        f"Pick a font style below. Other commands:\n"
        f"<code>/setlang hi</code> · <code>/setwelcome your text</code> (or <code>reset</code>)"
    )
    rows = []
    for i in range(0, len(FONT_MODES), 2):
        rows.append([B(("✔ " if m == current else "") + m.replace("_", " "), f"adm:font:{m}", "primary" if m == current else "success") for m in FONT_MODES[i:i + 2]])
    rows.append(BACK)
    return text, markup(rows)


async def screen_wallet(ctx: Ctx) -> Screen:
    stats = await db.get_stats(str(ctx.clone["_id"]))
    text = (
        f"👛 <b>WALLET &amp; USERS</b>\n{DIVIDER}\n"
        f"👥 Users: <b>{stats['users']}</b>\n🧾 Paid orders: <b>{stats['orders']}</b>\n\n"
        f"💰 Add balance:  <code>/addmoney USER_ID 5</code>\n"
        f"➖ Remove balance:  <code>/removemoney USER_ID 5</code>\n"
        f"<i>Amounts are in USD. The user gets a message when you add balance.</i>"
    )
    return text, markup([BACK])


async def screen_clones(ctx: Ctx) -> Screen:
    clones = await db.list_all_clones(15)
    lines = [
        f"{'🟢' if c['_id'] in ACTIVE_CLONE_CLIENTS else '🔴'} {esc(c.get('bot_username', ''))} · owner <code>{c['owner_id']}</code>\n   <code>{c['_id']}</code>"
        for c in clones
    ]
    text = (
        f"🧬 <b>CLONE BOTS</b>  ·  {len(clones)}\n{DIVIDER}\n"
        + ("\n".join(lines) if lines else "<i>No clones yet.</i>")
        + "\n\n/myclones · /deleteclone <code>BOT_ID</code>\n<i>Users create their own bot with /clone BOT_TOKEN.</i>"
    )
    return text, markup([BACK])


async def screen_help(ctx: Ctx) -> Screen:
    return ADMIN_GUIDE + (PLATFORM_GUIDE if ctx.is_master else ""), markup([BACK])


SECTIONS = {
    "home": screen_home, "products": screen_products, "folders": screen_folders, "pay": screen_pay,
    "images": screen_images, "chan": screen_chan, "fsub": screen_fsub, "look": screen_look,
    "wallet": screen_wallet, "clones": screen_clones, "help": screen_help,
}


# ═════════════════════════ Rendering & callbacks ═════════════════════════
async def render(client, ctx: Ctx, query: CallbackQuery, text: str, kb: Dict[str, Any]) -> None:
    """Edits the panel in place (smooth); falls back to a fresh message if that is not possible."""
    msg = query.message
    try:
        await botapi.edit_message_text(client.nohara_token, msg.chat.id, msg.id, text, kb)
    except botapi.BotApiError as exc:
        if "not modified" in exc.description.lower():
            return
        await show(client, ctx, msg.chat.id, text, kb, replace=msg)
    except Exception:
        await show(client, ctx, msg.chat.id, text, kb, replace=msg)


async def handle_admin_callback(client, query: CallbackQuery, ctx: Ctx, parts: List[str]) -> None:
    user_id = query.from_user.id
    if not _is_owner(ctx, user_id):
        await query.answer(ctx.T("owner_only"), show_alert=True)
        return
    bot_id = str(ctx.clone["_id"])
    section = parts[1] if len(parts) > 1 else "home"
    arg = parts[2] if len(parts) > 2 else ""
    note = ""

    if section == "pdel":  # confirmation screen
        product = await db.get_product(bot_id, arg)
        await query.answer()
        if not product:
            await render(client, ctx, query, *await screen_products(ctx))
            return
        text = f"🗑 <b>Delete this product?</b>\n{DIVIDER}\n<b>{esc(product['title'])}</b> — {fmt_usd(product['price_cents'])}\n<i>The file stays in your files channel.</i>"
        kb = markup([[B("✅ Yes, delete", f"adm:pdelok:{arg}", "danger"), B("✖️ Cancel", "adm:products", "success")]])
        await render(client, ctx, query, text, kb)
        return

    if section == "pdelok":
        note = "Product deleted" if await db.delete_product(bot_id, arg) else "Not found"
        section = "products"
    elif section == "fdel":
        note = "Folder deleted" if await db.delete_folder(bot_id, arg) else "Not found"
        section = "folders"
    elif section == "bdel" and arg in BANNER_FIELDS:
        await db.update_clone_fields(bot_id, {f"ui_branding.{BANNER_FIELDS[arg]}": 0})
        note = f"Image '{arg}' removed"
        section = "images"
    elif section == "fsubdel":
        try:
            note = "Channel removed" if await db.remove_fsub(bot_id, int(arg)) else "Not found"
        except ValueError:
            note = "Not found"
        section = "fsub"
    elif section == "font" and arg in FONT_MODES:
        await db.update_clone_fields(bot_id, {"font_style": arg})
        note = "Font updated"
        section = "look"
    elif section == "gw" and len(parts) == 4 and parts[2] in ("oxapay", "fampay") and parts[3] in ("on", "off"):
        gateway, turn_on = parts[2], parts[3] == "on"
        clone = await db.get_clone(bot_id) or ctx.clone
        if turn_on and gateway == "oxapay" and not db.get_oxapay_key(clone):
            await query.answer("Save the API key first:  /setgateway oxapay API_KEY", show_alert=True)
            return
        if turn_on and gateway == "fampay" and not clone["gateways"]["fampay"]["upi_id"]:
            await query.answer("Save your UPI ID first:  /setgateway fampay UPI_ID", show_alert=True)
            return
        if gateway == "oxapay":
            await db.set_oxapay(bot_id, enabled=turn_on)
        else:
            await db.set_fampay(bot_id, enabled=turn_on)
        note = f"{'OxaPay' if gateway == 'oxapay' else 'UPI'} {'enabled' if turn_on else 'disabled'}"
        section = "pay"

    if section not in SECTIONS:
        section = "home"
    await query.answer(note or None)
    fresh = await db.get_clone(bot_id)
    if fresh:
        ctx = Ctx(fresh, ctx.lang)
    text, kb = await SECTIONS[section](ctx)
    await render(client, ctx, query, text, kb)
