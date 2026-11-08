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
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from hydrogram.types import CallbackQuery

from nohara_bot import botapi
from nohara_bot.buttons import btn, markup
from nohara_bot.config import FREE_CLONES_PER_USER, PREMIUM_CLONES_PER_USER
from nohara_bot.database import db
from nohara_bot.fonts import FONT_MODES
from nohara_bot.handlers.common import BANNER_FIELDS, DIVIDER, _is_owner, show
from nohara_bot.services.core import (
    ACTIVE_CLONE_CLIENTS,
    Ctx,
    fmt_date,
    fmt_usd,
    is_premium,
    month_label,
    parse_usd_to_cents,
    plans_from,
    product_link,
    purchase_premium,
    referral_cfg,
    send_audit,
    send_ui,
    spawn,
    topup_tiers,
    vip_cfg,
    vip_plans,
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
    "📦 <b>Admin Panel → Products → ➕ Add Product</b> — step-by-step with buttons (file, title, price, description, demo link); then manage it from its dashboard (edit, stock, folder, Active/Inactive)\n"
    "/addproduct — opens the same wizard · quick way: send a file with caption <code>/addproduct Title | 9.99 | 10 | Folder</code>\n"
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
    "🔒 Premium: /setvip <code>DISCOUNT%</code> · /setvipplan <code>MONTHS PRICE</code> · /setreferral <code>BONUS_USD</code>\n"
    "/settopupbonus <code>10:5 25:10</code> (free for all)\n"
    "🔒 /productlink <code>[PRODUCT_ID]</code> — direct buy-page link of a product (Premium)\n"
    "/addcoupon <code>CODE PERCENT [MAX_USES] [DAYS]</code> · /delcoupon <code>CODE</code> · /coupons · /vipproduct <code>ID on|off</code>\n\n"
    "<b>Premium plan (platform owner only)</b>\n"
    "/setpremium <code>MONTHS PRICE</code> · /grantpremium <code>BOT_ID MONTHS</code>\n\n"
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
    rows.append([B("👑 Premium Plans", "adm:plans:pp", "primary")] if clone.get("is_master") else [B("💎 Get Premium", "adm:prem", "primary")])
    rows.append([B("🔗 Links", "adm:links", "primary"), B("📖 Command Guide", "adm:help")])
    rows.append([B("🏠 Back to Store", "menu:home")])
    return text, markup(rows)


PAGE_SIZE = 8


async def screen_products(ctx: Ctx, page: int = 0) -> Screen:
    """Product list: one button per product (opens its dashboard) + the ➕ Add Product button."""
    bot_id = str(ctx.clone["_id"])
    products = await db.list_products(bot_id)
    pages = max(1, (len(products) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    chunk = products[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows: List[List[Dict[str, Any]]] = [[B("➕ Add Product", "adm:padd", "primary")]]
    for p in chunk:
        dot = "🟢" if p.get("active", True) is not False else "🔴"
        rows.append([B(f"{dot} {p['title'][:30]} — {fmt_usd(p['price_cents'])}", f"adm:pe:{p['_id']}", "primary")])
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(B("⬅️ Prev", f"adm:products:{page - 1}"))
        if page < pages - 1:
            nav.append(B("Next ➡️", f"adm:products:{page + 1}"))
        rows.append(nav)
    active_n = sum(1 for p in products if p.get("active", True) is not False)
    body = "<i>No products yet — tap ➕ Add Product.</i>" if not products else "<i>Tap a product to open its dashboard.</i>"
    text = (
        f"📦 <b>PRODUCTS</b>  ·  {len(products)}  (🟢 {active_n} active · 🔴 {len(products) - active_n} hidden)\n{DIVIDER}\n{body}\n\n"
        + ("🔗 <i>Direct buy links are in each product dashboard.</i>" if is_premium(ctx.clone) else "🔒 <i>Direct product links: Premium feature (💎 Get Premium)</i>")
    )
    rows.append(BACK)
    return text, markup(rows)


# ═════════════════════════ Product dashboard + Add-Product wizard (all button based) ═════════════════════════
# (bot_id, owner_id) -> the product being created. Steps: file -> title -> price -> description (skip) -> demo link (skip)
PROD_DRAFT: Dict[Tuple[str, int], Dict[str, Any]] = {}
EDIT_FIELDS = {
    "title": ("✏️ Title", "Send the new <b>title</b> (max 60 characters)."),
    "price": ("💵 Price", "Send the new <b>price in USD</b>, for example <code>9.99</code>."),
    "desc": ("📝 Description", "Send the <b>description</b> buyers will see on the product page (max 800 characters)."),
    "demo": ("🎬 Demo link", "Send the <b>demo / preview link</b> (optional) — <code>https://…</code> or <code>@channel</code>."),
    "stock": ("📦 Stock", "Send the <b>stock</b> as a number (<code>10</code>), or <code>0</code> to mark sold out, or <code>unlimited</code>."),
}


def _active(p: Dict[str, Any]) -> bool:
    return p.get("active", True) is not False


async def screen_pedit(ctx: Ctx, product_id: str, note: str = "") -> Screen:
    bot_id = str(ctx.clone["_id"])
    p = await db.get_product(bot_id, product_id)
    if not p:
        return await screen_products(ctx)
    folders = {f["id"]: f["name"] for f in ctx.clone.get("folders") or []}
    stock = "∞ unlimited" if p["stock"] == -1 else str(p["stock"])
    folder = esc(folders[p["folder"]]) if p.get("folder") in folders else "—"
    desc = (p.get("description") or "").strip()
    demo = (p.get("demo_url") or "").strip()
    link = product_link(ctx.clone, p["_id"]) if is_premium(ctx.clone) else ""
    text = (
        (f"{note}\n\n" if note else "")
        + f"📦 <b>PRODUCT DASHBOARD</b>\n{DIVIDER}\n"
        f"<b>{esc(p['title'])}</b>\n"
        f"{'🟢 <b>Active</b> — visible to buyers' if _active(p) else '🔴 <b>Inactive</b> — hidden from buyers'}\n\n"
        f"💵 Price: <b>{fmt_usd(p['price_cents'])}</b>\n"
        f"📦 Stock: <b>{stock}</b>\n"
        f"🗂 Folder: <b>{folder}</b>\n"
        f"📝 Description: {esc(desc[:300]) + ('…' if len(desc) > 300 else '') if desc else '<i>not set</i>'}\n"
        f"🎬 Demo link: {esc(demo) if demo else '<i>not set (optional)</i>'}\n"
        f"🆔 <code>{p['_id']}</code>"
        + (f"\n🔗 <code>{esc(link)}</code>" if link else "")
    )
    pid = p["_id"]
    rows = [
        [B("✏️ Title", f"adm:pset:{pid}:title"), B("💵 Price", f"adm:pset:{pid}:price")],
        [B("📝 Description", f"adm:pset:{pid}:desc"), B("🎬 Demo link", f"adm:pset:{pid}:demo")],
        [B("📦 Stock", f"adm:pset:{pid}:stock"), B("🗂 Folder", f"adm:pfold:{pid}")],
        [B("🔴 Deactivate" if _active(p) else "🟢 Activate", f"adm:ptog:{pid}", "danger" if _active(p) else "success")],
        [B("🗑 Delete", f"adm:pdel:{pid}", "danger")],
        [B("◀️ Products", "adm:products")],
    ]
    return text, markup(rows)


async def screen_pfolder(ctx: Ctx, product_id: str) -> Screen:
    folders = ctx.clone.get("folders") or []
    rows = [[B(f"🗂 {f['name'][:30]}", f"adm:pfs:{product_id}:{f['id']}", "primary")] for f in folders]
    rows.append([B("📦 No folder", f"adm:pfs:{product_id}:_")])
    rows.append([B("◀️ Back", f"adm:pe:{product_id}")])
    return f"🗂 <b>Choose a folder</b>\n{DIVIDER}\n<i>Create more folders in 🗂 Folders.</i>", markup(rows)


def _wizard_prompt(step: str, draft: Dict[str, Any]) -> Screen:
    """Text + keyboard of one wizard step."""
    cancel = B("✖️ Cancel", "adm:pcancel", "danger")
    skip = B("⏭ Skip", "adm:pskip", "primary")
    steps = {
        "file": ("1/5", "Send the <b>file</b> you want to sell (document, photo, video or audio).", [[cancel]]),
        "title": ("2/5", "Send the product <b>title</b> (max 60 characters).", [[cancel]]),
        "price": ("3/5", f"Send the <b>price in USD</b> for <b>{esc(draft.get('title', ''))}</b>, for example <code>9.99</code>.", [[cancel]]),
        "desc": ("4/5", "Send a <b>description</b> for buyers (max 800 characters), or tap Skip.", [[skip], [cancel]]),
        "demo": ("5/5", "Send a <b>demo / preview link</b> (optional), or tap Skip.\n<code>https://…</code> or <code>@channel</code>", [[skip], [cancel]]),
    }
    n, body, rows = steps[step]
    return f"➕ <b>ADD PRODUCT</b>  ·  step {n}\n{DIVIDER}\n{body}", markup(rows)


async def _start_wizard(client, ctx: Ctx, query: CallbackQuery) -> None:
    bot_id, user_id = str(ctx.clone["_id"]), query.from_user.id
    if not ctx.clone.get("db_channel_id"):
        await query.answer("Set your files channel first:  /setdbchannel -100XXXXXXXXXX", show_alert=True)
        return
    PROD_DRAFT[(bot_id, user_id)] = {"step": "file"}
    ADMIN_WAIT[(bot_id, user_id)] = "padd:file"
    await query.answer()
    await render(client, ctx, query, *_wizard_prompt("file", {}))


async def _finish_wizard(client, ctx: Ctx, chat_id: int, user_id: int) -> None:
    bot_id = str(ctx.clone["_id"])
    key = (bot_id, user_id)
    draft = PROD_DRAFT.pop(key, None)
    ADMIN_WAIT.pop(key, None)
    if not draft:
        return
    product_id = uuid.uuid4().hex[:8]
    stored = await client.copy_message(
        chat_id=int(ctx.clone["db_channel_id"]),
        from_chat_id=draft["chat_id"],
        message_id=draft["msg_id"],
        caption=f"📦 <b>{esc(draft['title'])}</b>\n{fmt_usd(draft['price_cents'])} • ID <code>{product_id}</code>",
    )
    await db.save_product(
        bot_id, product_id, draft["title"], draft["price_cents"], -1, stored.id, "",
        description=draft.get("desc", ""), demo_url=draft.get("demo", ""), active=True,
    )
    fresh = await db.get_clone(bot_id) or ctx.clone
    text, kb = await screen_pedit(Ctx(fresh, ctx.lang), product_id, "✅ <b>Product saved!</b> Manage it below.")
    await send_ui(client, chat_id, text, kb)  # the dashboard opens right after adding
    await send_audit(
        fresh,
        f"<b>[NOHARA AUDIT — ASSET UPLOAD]</b>\n• Clone Bot: <code>{esc(fresh.get('bot_username', ''))}</code> (<code>{bot_id}</code>)\n"
        f"• Product: <b>{esc(draft['title'])}</b> (<code>{product_id}</code>) | DB Msg Pointer: <code>#{stored.id}</code>",
    )


async def open_product_dashboard(client, ctx: Ctx, chat_id: int, product_id: str, note: str = "") -> None:
    """Used by /addproduct: after saving a product, open its dashboard."""
    text, kb = await screen_pedit(ctx, product_id, note)
    await send_ui(client, chat_id, text, kb)


async def handle_product_input(client, message, ctx: Ctx) -> bool:
    """Owner is inside the Add-Product wizard or editing a field. True = message was consumed."""
    bot_id = str(ctx.clone["_id"])
    user_id = message.from_user.id
    key = (bot_id, user_id)
    which = ADMIN_WAIT.get(key) or ""
    if not (which.startswith("padd:") or which.startswith("pedit:")):
        return False
    if not _is_owner(ctx, user_id):
        ADMIN_WAIT.pop(key, None)
        return False
    chat_id = message.chat.id
    text = (message.text or "").strip()

    async def ask(step: str) -> None:
        draft = PROD_DRAFT.setdefault(key, {})
        draft["step"] = step
        ADMIN_WAIT[key] = f"padd:{step}"
        t, kb = _wizard_prompt(step, draft)
        await send_ui(client, chat_id, t, kb)

    # ───── Add-Product wizard ─────
    if which.startswith("padd:"):
        step = which.split(":")[1]
        draft = PROD_DRAFT.setdefault(key, {"step": step})
        if step == "file":
            if not message.media:
                await message.reply_text("❌ Please send the file itself (document, photo, video or audio).")
                return True
            draft.update({"chat_id": message.chat.id, "msg_id": message.id})
            await ask("title")
        elif step == "title":
            if not text or text.startswith("/"):
                await message.reply_text("❌ Send the title as plain text.")
                return True
            draft["title"] = text[:60]
            await ask("price")
        elif step == "price":
            cents = parse_usd_to_cents(text)
            if cents is None or cents <= 0 or cents > 1_000_000:
                await message.reply_text("❌ Invalid price. Send it in USD, for example <code>9.99</code>.")
                return True
            draft["price_cents"] = cents
            await ask("desc")
        elif step == "desc":
            if not text or text.startswith("/"):
                await message.reply_text("❌ Send the description as text, or tap Skip.")
                return True
            draft["desc"] = text[:800]
            await ask("demo")
        elif step == "demo":
            url = normalize_link(text)
            if not url:
                await message.reply_text("❌ That does not look like a valid link. Send <code>https://…</code> or <code>@channel</code>, or tap Skip.")
                return True
            draft["demo"] = url
            await _finish_wizard(client, ctx, chat_id, user_id)
        return True

    # ───── Editing one field of an existing product ─────
    _, product_id, field = (which.split(":") + ["", ""])[:3]
    product = await db.get_product(bot_id, product_id)
    if not product or field not in EDIT_FIELDS:
        ADMIN_WAIT.pop(key, None)
        return False
    update: Dict[str, Any] = {}
    if field == "title":
        if not text or text.startswith("/"):
            await message.reply_text("❌ Send the title as plain text.")
            return True
        update["title"] = text[:60]
    elif field == "price":
        cents = parse_usd_to_cents(text)
        if cents is None or cents <= 0 or cents > 1_000_000:
            await message.reply_text("❌ Invalid price. Send it in USD, for example <code>9.99</code>.")
            return True
        update["price_cents"] = cents
    elif field == "desc":
        if not text or text.startswith("/"):
            await message.reply_text("❌ Send the description as text.")
            return True
        update["description"] = text[:800]
    elif field == "demo":
        url = normalize_link(text)
        if not url:
            await message.reply_text("❌ That does not look like a valid link. Send <code>https://…</code> or <code>@channel</code>.")
            return True
        update["demo_url"] = url
    elif field == "stock":
        low = text.lower()
        if low in ("unlimited", "inf", "∞", "-1", "u"):
            update["stock"] = -1
        elif low.isdigit() and int(low) <= 10_000_000:
            update["stock"] = int(low)
        else:
            await message.reply_text("❌ Send a number like <code>10</code>, <code>0</code> for sold out, or <code>unlimited</code>.")
            return True
    ADMIN_WAIT.pop(key, None)
    await db.update_product(bot_id, product_id, update)
    fresh = await db.get_clone(bot_id) or ctx.clone
    t, kb = await screen_pedit(Ctx(fresh, ctx.lang), product_id, "✅ <b>Saved.</b>")
    await send_ui(client, chat_id, t, kb)
    return True


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
        f"{'🟢' if c['_id'] in ACTIVE_CLONE_CLIENTS else '🔴'} {esc(c.get('bot_username', ''))} · owner <code>{c['owner_id']}</code> · "
        + (f"💎 until {fmt_date(c['premium_until'])}" if is_premium(c) else "free")
        + f"\n   <code>{c['_id']}</code>"
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
    if which.startswith("plan:"):  # the owner is sending the price of one plan
        _, kind, months = which.split(":")
        text = (message.text or "").strip().lower()
        cents = 0 if text in ("0", "off", "remove") else parse_usd_to_cents(text)
        if cents is None or cents > 1_000_000 or not _plan_allowed(ctx, kind):
            await message.reply_text("❌ Send the price in USD, for example <code>5</code> or <code>12.5</code> (send <code>0</code> to remove this plan).")
            return True
        ADMIN_WAIT.pop(key, None)
        await db.update_clone_fields(bot_id, {f"{PLAN_FIELDS[kind]}.{months}": cents})
        fresh = await db.get_clone(bot_id)
        await message.reply_text(
            f"✅ {month_label(int(months))} plan " + (f"saved: <b>{fmt_usd(cents)}</b>" if cents else "removed") + "."
        )
        if fresh:
            text_out, kb = await screen_plans(Ctx(fresh, ctx.lang), kind)
            await send_ui(client, message.chat.id, text_out, kb)
        return True
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


# ═════════════════════════ Premium plan + multi-month plan editor ═════════════════════════
PLAN_FIELDS = {"vp": "vip_plans", "pp": "premium_plans"}
PLAN_TITLES = {"vp": "VIP MEMBERSHIP PLANS", "pp": "PREMIUM PLANS (for clone owners)"}


def _plan_allowed(ctx: Ctx, kind: str) -> bool:
    """pp = the prices clone owners pay for Premium: only the MASTER store sets them. vp = the VIP plans of one store (needs Premium)."""
    if kind == "pp":
        return bool(ctx.clone.get("is_master"))
    return kind == "vp" and is_premium(ctx.clone)


async def screen_plans(ctx: Ctx, kind: str) -> Screen:
    raw = ctx.clone.get(PLAN_FIELDS[kind])
    current = dict(plans_from(raw))
    lines = [f"• <b>{month_label(m)}</b> — {fmt_usd(c)}" for m, c in sorted(current.items())]
    if kind == "vp" and not lines:
        lines = [f"• <b>1 Month</b> — {fmt_usd(vip_cfg(ctx.clone)['price_cents'])} <i>(default — set your own below)</i>"]
    body = "\n".join(lines) if lines else "<i>No plan yet — tap a month below and send its price.</i>"
    who = "Clone owners buy Premium with their wallet in this master bot." if kind == "pp" else "Your buyers pick one of these plans on the VIP screen."
    text = (
        f"🗓 <b>{PLAN_TITLES[kind]}</b>\n{DIVIDER}\n{body}\n\n"
        f"Tap a month to set its price (✅ = already has a price). Send <code>0</code> as the price to remove a plan.\n"
        f"<i>{who}</i>"
    )
    rows, row = [], []
    for months in range(1, 13):
        mark = "✅ " if months in current else ""
        row.append(B(f"{mark}{months} mo" + (f" · {fmt_usd(current[months])}" if months in current else ""), f"adm:plan:{kind}:{months}", "primary" if months in current else "success"))
        if len(row) == 3:
            rows.append(row)
            row = []
    rows.append([B("◀️ Back", "adm:premium" if kind == "vp" else "adm:home")])
    return text, markup(rows)


async def screen_prem(ctx: Ctx, user_id: int = 0, note: str = "") -> Screen:
    """Premium page of a clone owner: status, plans, buy buttons."""
    clone = ctx.clone
    master = await db.get_master_clone() or {}
    plans = plans_from(master.get("premium_plans"))
    until = int(clone.get("premium_until") or 0)
    if clone.get("is_master"):
        text = f"💎 <b>PREMIUM</b>\n{DIVIDER}\n👑 This is the master store — all premium features are always unlocked.\nSet the prices clone owners pay in <b>👑 Premium Plans</b>."
        return text, markup([[B("👑 Premium Plans", "adm:plans:pp", "primary")], BACK])
    active = is_premium(clone)
    balance = await db.get_balance(str(master.get("_id", "")), user_id) if user_id and master else 0
    status = f"✅ <b>Active</b> until <b>{fmt_date(until)}</b>" if active else "❌ <b>Not active</b>"
    plan_lines = "\n".join(f"• <b>{month_label(m)}</b> — {fmt_usd(c)}" for m, c in plans) or "<i>Premium plans are not available yet. Please contact the platform owner.</i>"
    text = (
        f"💎 <b>PREMIUM</b>\n{DIVIDER}\n"
        f"{status}\n\n"
        f"<b>Unlocks</b>\n• 💎 VIP Membership (with your own plans)\n• 🎁 Refer &amp; Earn\n• 🎟 Coupons / Redeem codes\n• 🔒 VIP-only products\n• 🔗 Direct product links\n\n"
        f"<b>Plans</b>\n{plan_lines}\n\n"
        f"👛 Your wallet in {esc(master.get('bot_username', 'the master bot'))}: <b>{fmt_usd(balance)}</b>\n"
        f"<i>Pay from that wallet. Low balance? Open the master bot → Top Up, then come back here.</i>"
    )
    if note:
        text = f"{note}\n\n{text}"
    rows, row = [], []
    for months, cents in plans:
        row.append(B(f"💎 {months} mo — {fmt_usd(cents)}", f"adm:prembuy:{months}", "primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    username = str(master.get("bot_username", "")).lstrip("@")
    if username:
        rows.append([btn("💰 Top Up in master bot", FONT, "success", url=f"https://t.me/{username}")])
    rows.append(BACK)
    return text, markup(rows)


async def screen_prem_confirm(ctx: Ctx, user_id: int, months: int) -> Screen:
    """Confirmation page: plan, price, wallet, validity and the features it unlocks. Payment happens only on ✅ Confirm."""
    clone = ctx.clone
    master = await db.get_master_clone() or {}
    price = dict(plans_from(master.get("premium_plans"))).get(int(months))
    if not price:
        return await screen_prem(ctx, user_id, "❌ This plan is not available.")
    balance = await db.get_balance(str(master.get("_id", "")), user_id) if master else 0
    current_until = int(clone.get("premium_until") or 0)
    renewing = current_until > int(time.time())
    new_until = max(int(time.time()), current_until) + int(months) * 30 * 86400
    per_month = fmt_usd(int(price) // int(months)) if months > 1 else ""
    can_pay = balance >= int(price)
    text = (
        f"💎 <b>CONFIRM PREMIUM</b>\n{DIVIDER}\n"
        f"🗓 Plan: <b>{month_label(months)}</b>\n"
        f"💵 Price: <b>{fmt_usd(int(price))}</b>" + (f"  <i>(≈ {per_month}/month)</i>" if per_month else "") + "\n"
        f"⏳ {'Extends Premium until' if renewing else 'Premium active until'}: <b>{fmt_date(new_until)}</b>\n"
        f"👛 Wallet: <b>{fmt_usd(balance)}</b>"
        + (f"  →  after payment <b>{fmt_usd(balance - int(price))}</b>" if can_pay else "") + "\n\n"
        f"<b>✨ What you get with {month_label(months)} of Premium</b>\n"
        f"• 💎 <b>VIP Membership</b> — sell VIP to your buyers with your own 1–12 month plans &amp; discount %\n"
        f"• 🎁 <b>Refer &amp; Earn</b> — wallet bonus for every friend your buyers invite\n"
        f"• 🎟 <b>Coupons</b> — discount codes with usage limit and expiry\n"
        f"• 🔒 <b>VIP-only products</b> — keep selected products for VIP members\n"
        f"• 🔗 <b>Direct product links</b> — share a buy page for any product\n"
        f"• 🧬 <b>Up to {PREMIUM_CLONES_PER_USER} bots</b> from one account (free accounts: {FREE_CLONES_PER_USER})\n\n"
    )
    if can_pay:
        text += "⚠️ <b>Your wallet is charged only when you tap “Confirm &amp; Pay”.</b>"
    else:
        text += f"❌ <b>Not enough balance</b> — you need {fmt_usd(int(price) - balance)} more. Top up in the master bot first."
    rows: List[List[Dict[str, Any]]] = []
    if can_pay:
        rows.append([B("✅ Confirm & Pay", f"adm:premok:{months}", "success")])
    else:
        username = str(master.get("bot_username", "")).lstrip("@")
        if username:
            rows.append([btn("💰 Top Up in master bot", FONT, "primary", url=f"https://t.me/{username}")])
    rows.append([B("✖️ Cancel", "adm:prem", "danger")])
    return text, markup(rows)


# ═════════════════════════ Premium features screens ═════════════════════════
async def screen_premium(ctx: Ctx) -> Screen:
    clone = ctx.clone
    if not is_premium(clone):
        text = (
            f"💎 <b>VIP &amp; REWARDS</b>  🔒\n{DIVIDER}\n"
            f"These features need a <b>Premium plan</b>:\n• 💎 VIP Membership (your own multi-month plans)\n• 🎁 Refer &amp; Earn\n• 🎟 Coupons\n• 🔒 VIP-only products\n• 🔗 Direct product links\n\n"
            f"Top-up bonus and Broadcast stay free."
        )
        return text, markup([[B("💎 Get Premium", "adm:prem", "primary")], BACK])
    vip, ref, tiers = vip_cfg(clone), referral_cfg(clone), topup_tiers(clone)
    coupons = await db.list_coupons(str(clone["_id"]))
    tier_text = " · ".join(f"{fmt_usd(int(t['min_cents']))}+ → {int(t['pct'])}%" for t in tiers) if tiers else "off"
    plan_text = " · ".join(f"{m}mo {fmt_usd(c)}" for m, c in vip_plans(clone))
    until = int(clone.get("premium_until") or 0)
    status = "👑 master — always on" if clone.get("is_master") else f"💎 Premium until {fmt_date(until)}"
    text = (
        f"💎 <b>VIP &amp; REWARDS</b>\n{DIVIDER}\n"
        f"{status}\n\n"
        f"{ok(vip['enabled'])} <b>VIP membership</b>  {vip['discount_pct']}% off · plans: {plan_text}\n"
        f"{ok(ref['enabled'])} <b>Referral</b>  bonus {fmt_usd(int(ref['bonus_cents']))} per invited friend\n"
        f"{ok(tiers)} <b>Top-up bonus</b>  {tier_text}\n"
        f"🎟 <b>Coupons</b>  {len(coupons)}\n\n"
        f"<b>Change settings</b>\n"
        f"/setvip <code>DISCOUNT%</code> — e.g. <code>/setvip 15</code> · /setvipplan <code>MONTHS PRICE</code> — e.g. <code>/setvipplan 3 12</code>\n"
        f"/setreferral <code>BONUS_USD</code> — e.g. <code>/setreferral 0.5</code>\n"
        f"/settopupbonus <code>10:5 25:10 50:15</code> (USD:percent) · <code>off</code> · <code>default</code>\n"
        f"/addcoupon <code>CODE PERCENT [MAX_USES] [DAYS]</code> · /delcoupon <code>CODE</code>\n"
        f"/vipproduct <code>PRODUCT_ID on|off</code> — make a product VIP-only"
    )
    rows = [
        [B("🗓 VIP Plans (1–12 months)", "adm:plans:vp", "primary")],
        [B("💎 VIP: ON" if vip["enabled"] else "💎 VIP: OFF", f"adm:vipsw:{'off' if vip['enabled'] else 'on'}", "danger" if vip["enabled"] else "success"),
         B("🎁 Referral: ON" if ref["enabled"] else "🎁 Referral: OFF", f"adm:refsw:{'off' if ref['enabled'] else 'on'}", "danger" if ref["enabled"] else "success")],
        [B("🎟 Coupons", "adm:coupons", "primary")],
        BACK,
    ]
    return text, markup(rows)


async def screen_coupons(ctx: Ctx) -> Screen:
    if not is_premium(ctx.clone):
        return await screen_premium(ctx)
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

    # ───── Products: list, Add-Product wizard, per-product dashboard ─────
    if section == "products":
        await query.answer()
        await render(client, ctx, query, *await screen_products(ctx, int(arg) if arg.isdigit() else 0))
        return
    if section == "padd":
        await _start_wizard(client, ctx, query)
        return
    if section == "pcancel":
        PROD_DRAFT.pop((bot_id, user_id), None)
        ADMIN_WAIT.pop((bot_id, user_id), None)
        await query.answer("Cancelled")
        await render(client, ctx, query, *await screen_products(ctx))
        return
    if section == "pskip":
        draft = PROD_DRAFT.get((bot_id, user_id)) or {}
        step = draft.get("step")
        if step == "desc":
            draft["step"] = "demo"
            ADMIN_WAIT[(bot_id, user_id)] = "padd:demo"
            await query.answer()
            await render(client, ctx, query, *_wizard_prompt("demo", draft))
        elif step == "demo":
            await query.answer("Saving…")
            try:
                await query.message.delete()
            except Exception:
                pass
            await _finish_wizard(client, ctx, query.message.chat.id, user_id)
        else:
            await query.answer("Nothing to skip.", show_alert=True)
        return
    if section == "pe":
        await query.answer()
        await render(client, ctx, query, *await screen_pedit(ctx, arg))
        return
    if section == "pset" and len(parts) > 3 and parts[3] in EDIT_FIELDS:
        if not await db.get_product(bot_id, arg):
            await query.answer("Product not found", show_alert=True)
            return
        field = parts[3]
        ADMIN_WAIT[(bot_id, user_id)] = f"pedit:{arg}:{field}"
        await query.answer()
        label, ask_text = EDIT_FIELDS[field]
        rows = [[B("✖️ Cancel", f"adm:pe:{arg}", "danger")]]
        if field in ("desc", "demo"):
            rows.insert(0, [B("🗑 Remove it", f"adm:pclr:{arg}:{field}", "danger")])
        await render(client, ctx, query, f"{label}\n{DIVIDER}\n{ask_text}", markup(rows))
        return
    if section == "pclr" and len(parts) > 3 and parts[3] in ("desc", "demo"):
        await db.update_product(bot_id, arg, {"description" if parts[3] == "desc" else "demo_url": ""})
        await query.answer("Removed")
        await render(client, ctx, query, *await screen_pedit(ctx, arg))
        return
    if section == "ptog":
        product = await db.get_product(bot_id, arg)
        if not product:
            await query.answer("Product not found", show_alert=True)
            return
        now_active = not _active(product)
        await db.update_product(bot_id, arg, {"active": now_active})
        await query.answer("🟢 Activated — buyers can see it" if now_active else "🔴 Deactivated — hidden from buyers")
        await render(client, ctx, query, *await screen_pedit(ctx, arg))
        return
    if section == "pfold":
        if not (ctx.clone.get("folders") or []):
            await query.answer("Create a folder first (🗂 Folders on the Admin Panel).", show_alert=True)
            return
        await query.answer()
        await render(client, ctx, query, *await screen_pfolder(ctx, arg))
        return
    if section == "pfs" and len(parts) > 3:
        folder_id = "" if parts[3] == "_" else parts[3]
        if folder_id and folder_id not in {f["id"] for f in ctx.clone.get("folders") or []}:
            await query.answer("Folder not found", show_alert=True)
            return
        await db.set_product_folder(bot_id, arg, folder_id)
        await query.answer("Folder updated")
        await render(client, ctx, query, *await screen_pedit(ctx, arg))
        return

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
    elif section in ("vipsw", "refsw", "cdel") and not is_premium(ctx.clone):
        await query.answer("🔒 Premium required — tap 💎 Get Premium.", show_alert=True)
        return
    elif section == "plans" and arg in PLAN_FIELDS:
        if not _plan_allowed(ctx, arg):
            await query.answer("🔒 Premium required — tap 💎 Get Premium.", show_alert=True)
            return
        await query.answer()
        await render(client, ctx, query, *await screen_plans(ctx, arg))
        return
    elif section == "plan" and arg in PLAN_FIELDS and len(parts) > 3 and parts[3].isdigit() and 1 <= int(parts[3]) <= 12:
        if not _plan_allowed(ctx, arg):
            await query.answer("🔒 Premium required — tap 💎 Get Premium.", show_alert=True)
            return
        ADMIN_WAIT[(bot_id, user_id)] = f"plan:{arg}:{int(parts[3])}"
        await query.answer()
        prompt = (
            f"✏️ <b>{month_label(int(parts[3]))} plan</b>\n{DIVIDER}\n"
            f"Send the price in USD now, for example <code>5</code> or <code>12.5</code>.\nSend <code>0</code> to remove this plan."
        )
        await render(client, ctx, query, prompt, markup([[B("✖️ Cancel", f"adm:plans:{arg}", "danger")]]))
        return
    elif section == "prem":
        await query.answer()
        await render(client, ctx, query, *await screen_prem(ctx, user_id))
        return
    elif section == "prembuy" and arg.isdigit():  # step 1: show what the buyer gets — NOTHING is charged yet
        if ctx.is_master:
            await query.answer("The master store already has every feature.", show_alert=True)
            return
        await query.answer()
        await render(client, ctx, query, *await screen_prem_confirm(ctx, user_id, int(arg)))
        return
    elif section == "premok" and arg.isdigit():  # step 2: the buyer tapped ✅ Confirm
        if ctx.is_master:
            await query.answer("The master store already has every feature.", show_alert=True)
            return
        result = await purchase_premium(ctx.clone, user_id, int(arg))
        if result["status"] == "no_balance":
            await query.answer(
                f"Not enough balance in the master bot wallet. You need {fmt_usd(result['price'])}, you have {fmt_usd(result['balance'])}. Top up there first.",
                show_alert=True,
            )
            return
        if result["status"] != "ok":
            await query.answer("This plan is not available.", show_alert=True)
            return
        await query.answer("💎 Premium activated!")
        fresh = await db.get_clone(bot_id)
        if fresh:
            ctx = Ctx(fresh, ctx.lang)
        await render(client, ctx, query, *await screen_prem(ctx, user_id, f"🎉 <b>Premium activated until {fmt_date(result['until'])}!</b>"))
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
