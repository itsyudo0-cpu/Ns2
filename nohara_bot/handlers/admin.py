"""
NOHARA STORE BOT — Admin Panel (nohara_bot/handlers/admin.py)

Opens with /admin (or /dashboard, or the ⚙️ button in the menu) in the MASTER store AND in every
clone bot. Only the owner sees it. Everything is button based: tap a section, see its status,
and manage it with buttons. (Things that need typing — a name, an API key — show the exact
command to send.)
"""

import asyncio
import html
import re
from typing import Any, Dict, List, Optional, Tuple

from hydrogram.types import CallbackQuery

from nohara_bot import botapi
from nohara_bot.buttons import btn, markup
from nohara_bot.database import db
from nohara_bot.fonts import FONT_MODES
from nohara_bot.handlers.common import BANNER_FIELDS, DIVIDER, _is_owner, show
from nohara_bot.services.core import (
    ACTIVE_CLONE_CLIENTS,
    Ctx,
    fmt_date,
    fmt_usd,
    referral_cfg,
    send_ui,
    spawn,
    topup_tiers,
    vip_cfg,
)

FONT = "default_html"
Screen = Tuple[str, Dict[str, Any]]


def B(text: str, cb: str, style: str = "success") -> Dict[str, Any]:
    return btn(text, FONT, style, cb)


BACK = [B("◀️ Admin Panel", "adm:home")]

# (bot_id, user_id) -> "updates" | "support": the owner pressed "Set link" and the next text message is the link
ADMIN_WAIT: Dict[Tuple[str, int], str] = {}
LINK_LABELS = {"updates": "Updates channel", "support": "Support"}


def normalize_link(raw: str) -> Optional[str]:
    """@name / t.me/name / https://… -> a valid https URL, or None when it does not look like a link."""
    text = (raw or "").strip()
    if not text or len(text) > 200 or any(ch.isspace() for ch in text):
        return None
    if text.startswith("@"):
        name = text[1:]
        return f"https://t.me/{name}" if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", name) else None
    if re.match(r"^(t\.me|telegram\.me)/", text, re.I):
        text = "https://" + text
    if re.match(r"^https?://[^\s/]+\.[^\s/]+", text, re.I):
        return text
    return None


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
    "<b>VIP &amp; rewards</b>\n"
    "/setvip <code>PRICE DAYS DISCOUNT%</code> · /setreferral <code>BONUS_USD</code> · /settopupbonus <code>10:5 25:10</code>\n"
    "/addcoupon <code>CODE PERCENT [MAX_USES] [DAYS]</code> · /delcoupon <code>CODE</code> · /coupons · /vipproduct <code>ID on|off</code>\n\n"
    "<b>Broadcast (free)</b>\n"
    "/broadcast <code>text</code> — or reply to any message with /broadcast\n\n"
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
    rows.append([B("📣 Broadcast", "adm:bc", "primary"), B("💎 VIP & Rewards", "adm:premium", "primary")])
    rows.append([B("🔗 Links", "adm:links", "primary"), B("📖 Command Guide", "adm:help")])
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
        f"💰 Customers can top up their wallet with the live methods above.\n"
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


async def screen_links(ctx: Ctx) -> Screen:
    links = ctx.clone.get("links") or {}
    up, sp = links.get("updates_url") or "", links.get("support_url") or ""
    text = (
        f"🔗 <b>LINKS</b>\n{DIVIDER}\n"
        f"{ok(up)} <b>Updates channel</b>  {esc(up) if up else 'not set'}\n"
        f"{ok(sp)} <b>Support</b>  {esc(sp) if sp else 'not set'}\n\n"
        f"These two buttons appear on the main menu, under <b>Top Up</b>. A button is shown only when its link is set.\n"
        f"<i>Accepted: https://t.me/yourchannel · t.me/yourchannel · @username</i>"
    )
    rows = [[B("✏️ Set Updates link", "adm:linkset:updates", "primary"), B("✏️ Set Support link", "adm:linkset:support", "primary")]]
    removers = []
    if up:
        removers.append(B("🗑 Remove Updates", "adm:linkdel:updates", "danger"))
    if sp:
        removers.append(B("🗑 Remove Support", "adm:linkdel:support", "danger"))
    if removers:
        rows.append(removers)
    rows.append(BACK)
    return text, markup(rows)


async def handle_link_input(client, message, ctx: Ctx) -> bool:
    """The owner pressed 'Set link' and now sends the link as a normal message. True = message was consumed."""
    bot_id = str(ctx.clone["_id"])
    key = (bot_id, message.from_user.id)
    which = ADMIN_WAIT.get(key)
    if not which:
        return False
    if not _is_owner(ctx, message.from_user.id):
        ADMIN_WAIT.pop(key, None)
        return False
    url = normalize_link(message.text or "")
    if not url:
        await message.reply_text("❌ That does not look like a valid link.\nSend something like <code>https://t.me/yourchannel</code> or <code>@yourchannel</code>.")
        return True
    ADMIN_WAIT.pop(key, None)
    await db.set_link(bot_id, which, url)
    fresh = await db.get_clone(bot_id)
    await message.reply_text(f"✅ {LINK_LABELS[which]} link saved. It now shows on the main menu.")
    if fresh:
        text, kb = await screen_links(Ctx(fresh, ctx.lang))
        await send_ui(client, message.chat.id, text, kb)
    return True


# ═════════════════════════ Broadcast (free for every store) ═════════════════════════
# (bot_id, owner_id) -> what will be sent after the owner confirms. Set by the /broadcast command.
BC_PENDING: Dict[Tuple[str, int], Dict[str, Any]] = {}
BC_RUNNING: set = set()
_BLOCKED_ERRORS = {"UserIsBlocked", "InputUserDeactivated", "PeerIdInvalid", "UserDeactivated", "UserDeactivatedBan", "ChatWriteForbidden"}


async def _send_one(client, user_id: int, source: Dict[str, Any]) -> str:
    """'sent' | 'blocked' | 'failed'. Waits and retries once when Telegram asks us to slow down."""
    for attempt in range(2):
        try:
            if source["kind"] == "copy":
                await client.copy_message(user_id, source["chat_id"], source["message_id"])
            else:
                await client.send_message(user_id, source["text"])
            return "sent"
        except Exception as exc:  # noqa: BLE001
            name = exc.__class__.__name__
            if name == "FloodWait" and attempt == 0:
                await asyncio.sleep(int(getattr(exc, "value", 5) or 5) + 1)
                continue
            return "blocked" if name in _BLOCKED_ERRORS else "failed"
    return "failed"


async def run_broadcast(client, bot_id: str, report_chat_id: int, source: Dict[str, Any]) -> None:
    if bot_id in BC_RUNNING:
        return
    BC_RUNNING.add(bot_id)
    counts = {"sent": 0, "blocked": 0, "failed": 0}
    try:
        user_ids = await db.list_user_ids(bot_id)
        for uid in user_ids:
            counts[await _send_one(client, uid, source)] += 1
            await asyncio.sleep(0.05)  # ~20 messages / second, safely under Telegram's limits
        await send_ui(
            client, report_chat_id,
            f"📣 <b>Broadcast finished</b>\n{DIVIDER}\n"
            f"👥 Users: <b>{len(user_ids)}</b>\n✅ Delivered: <b>{counts['sent']}</b>\n"
            f"🚫 Blocked the bot: <b>{counts['blocked']}</b>\n⚠️ Failed: <b>{counts['failed']}</b>",
            markup([BACK]),
        )
    finally:
        BC_RUNNING.discard(bot_id)


# ═════════════════════════ Premium features screens ═════════════════════════
async def screen_premium(ctx: Ctx) -> Screen:
    clone = ctx.clone
    vip, ref, tiers = vip_cfg(clone), referral_cfg(clone), topup_tiers(clone)
    coupons = await db.list_coupons(str(clone["_id"]))
    tier_text = " · ".join(f"{fmt_usd(int(t['min_cents']))}+ → {int(t['pct'])}%" for t in tiers) if tiers else "off"
    text = (
        f"💎 <b>VIP &amp; REWARDS</b>\n{DIVIDER}\n"
        f"{ok(vip['enabled'])} <b>VIP membership</b>  {fmt_usd(vip['price_cents'])} / {vip['days']} days · {vip['discount_pct']}% off\n"
        f"{ok(ref['enabled'])} <b>Referral</b>  bonus {fmt_usd(int(ref['bonus_cents']))} per invited friend\n"
        f"{ok(tiers)} <b>Top-up bonus</b>  {tier_text}\n"
        f"🎟 <b>Coupons</b>  {len(coupons)}\n\n"
        f"<b>Change settings</b>\n"
        f"/setvip <code>PRICE DAYS DISCOUNT%</code> — e.g. <code>/setvip 5 30 15</code>\n"
        f"/setreferral <code>BONUS_USD</code> — e.g. <code>/setreferral 0.5</code>\n"
        f"/settopupbonus <code>10:5 25:10 50:15</code> (USD:percent) · <code>off</code> · <code>default</code>\n"
        f"/addcoupon <code>CODE PERCENT [MAX_USES] [DAYS]</code> · /delcoupon <code>CODE</code>\n"
        f"/vipproduct <code>PRODUCT_ID on|off</code> — make a product VIP-only"
    )
    rows = [
        [B("💎 VIP: ON" if vip["enabled"] else "💎 VIP: OFF", f"adm:vipsw:{'off' if vip['enabled'] else 'on'}", "danger" if vip["enabled"] else "success"),
         B("🎁 Referral: ON" if ref["enabled"] else "🎁 Referral: OFF", f"adm:refsw:{'off' if ref['enabled'] else 'on'}", "danger" if ref["enabled"] else "success")],
        [B("🎟 Coupons", "adm:coupons", "primary")],
        BACK,
    ]
    return text, markup(rows)


async def screen_coupons(ctx: Ctx) -> Screen:
    coupons = await db.list_coupons(str(ctx.clone["_id"]))
    lines = []
    rows = []
    for c in coupons[:20]:
        exp = fmt_date(c["expires_at"]) if c.get("expires_at") else "never"
        limit = c["max_uses"] if c.get("max_uses") else "∞"
        lines.append(f"🎟 <code>{esc(c['code'])}</code> — <b>{c['pct']}%</b> · used {c.get('used', 0)}/{limit} · expires {exp}")
        rows.append([B(f"🗑 {c['code']}", f"adm:cdel:{c['code']}", "danger")])
    body = "\n".join(lines) if lines else "<i>No coupons yet.</i>"
    text = (
        f"🎟 <b>COUPONS</b>\n{DIVIDER}\n{body}\n\n"
        f"Create: /addcoupon <code>CODE PERCENT [MAX_USES] [DAYS]</code>\n"
        f"<i>Users redeem a code from the main menu; it is applied to their next purchase (one use per user).</i>"
    )
    rows.append([B("◀️ VIP & Rewards", "adm:premium")])
    return text, markup(rows)


async def screen_bc(ctx: Ctx) -> Screen:
    total = await db.count_users(str(ctx.clone["_id"]))
    running = str(ctx.clone["_id"]) in BC_RUNNING
    text = (
        f"📣 <b>BROADCAST</b>  <i>(free)</i>\n{DIVIDER}\n"
        f"👥 Users who can receive it: <b>{total}</b>\n"
        f"{'⏳ A broadcast is running right now.' if running else '✅ Ready.'}\n\n"
        f"<b>How to send</b>\n"
        f"1. Text: <code>/broadcast Your message here</code>\n"
        f"2. Photo / video / any formatted post: send it to this chat first, then <b>reply to it</b> with <code>/broadcast</code>\n\n"
        f"You will get a preview and must tap <b>Send to all</b> to confirm."
    )
    return text, markup([BACK])


async def screen_help(ctx: Ctx) -> Screen:
    return ADMIN_GUIDE + (PLATFORM_GUIDE if ctx.is_master else ""), markup([BACK])


SECTIONS = {
    "home": screen_home, "products": screen_products, "folders": screen_folders, "pay": screen_pay,
    "images": screen_images, "chan": screen_chan, "fsub": screen_fsub, "look": screen_look,
    "wallet": screen_wallet, "clones": screen_clones, "help": screen_help, "links": screen_links,
    "premium": screen_premium, "coupons": screen_coupons, "bc": screen_bc,
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
    elif section == "linkset" and arg in LINK_LABELS:
        ADMIN_WAIT[(bot_id, user_id)] = arg
        await query.answer()
        prompt = (
            f"✏️ <b>Set {LINK_LABELS[arg]} link</b>\n{DIVIDER}\n"
            f"Send the link now as a message:\n<code>https://t.me/yourchannel</code>  or  <code>@yourchannel</code>"
        )
        await render(client, ctx, query, prompt, markup([[B("✖️ Cancel", "adm:links", "danger")]]))
        return
    elif section == "vipsw" and arg in ("on", "off"):
        await db.update_clone_fields(bot_id, {"vip.enabled": arg == "on"})
        note = f"VIP {arg.upper()}"
        section = "premium"
    elif section == "refsw" and arg in ("on", "off"):
        await db.update_clone_fields(bot_id, {"referral.enabled": arg == "on"})
        note = f"Referral {arg.upper()}"
        section = "premium"
    elif section == "cdel":
        note = "Coupon deleted" if await db.delete_coupon(bot_id, arg) else "Not found"
        section = "coupons"
    elif section == "bccancel":
        BC_PENDING.pop((bot_id, user_id), None)
        note = "Broadcast cancelled"
        section = "bc"
    elif section == "bcgo":
        source = BC_PENDING.pop((bot_id, user_id), None)
        if not source:
            await query.answer("Nothing to send — use /broadcast again.", show_alert=True)
            return
        if bot_id in BC_RUNNING:
            await query.answer("A broadcast is already running.", show_alert=True)
            return
        await query.answer("📣 Broadcast started…")
        spawn(run_broadcast(client, bot_id, query.message.chat.id, source))
        await render(client, ctx, query, "📣 <b>Broadcast started.</b>\nYou will get a report when it is finished.", markup([BACK]))
        return
    elif section == "linkdel" and arg in LINK_LABELS:
        await db.set_link(bot_id, arg, "")
        note = f"{LINK_LABELS[arg]} link removed"
        section = "links"
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
