"""
NOHARA STORE BOT — Store bot — public handlers: /start, language, menus, product pages, payments, UPI proof.
(nohara_bot/handlers/store.py)
"""

import html
import logging
from decimal import Decimal
from hydrogram import Client
from hydrogram.types import CallbackQuery, Message
from typing import Dict, Optional, Set, Tuple
from urllib.parse import quote

from nohara_bot.buttons import (
    after_coupon_keyboard,
    folders_keyboard,
    back_keyboard,
    cancel_keyboard,
    language_keyboard,
    payment_link_keyboard,
    product_keyboard,
    products_keyboard,
    topup_amount_keyboard,
    topup_cancel_keyboard,
    topup_methods_keyboard,
    referral_keyboard,
    upi_review_keyboard,
    vip_keyboard,
    vip_locked_keyboard,
    wallet_keyboard,
)
from nohara_bot.config import APP_URL
from nohara_bot.database import db
from nohara_bot.handlers.menu import ensure_owner_menu
from nohara_bot.handlers.common import (
    DIVIDER,
    _is_owner,
    ensure_fsub,
    missing_fsub,
    show,
    show_home,
)
from nohara_bot.handlers.admin import ADMIN_WAIT, handle_admin_callback, handle_link_input, screen_home
from nohara_bot.i18n import SUPPORTED_LANG_CODES
from nohara_bot.payments import create_oxapay_invoice
from nohara_bot.services.core import (
    Ctx,
    cents_to_inr,
    coupon_is_valid,
    fmt_date,
    fmt_usd,
    fulfill_paid_order,
    get_ctx,
    get_user_perks,
    parse_usd_to_cents,
    price_for,
    referral_cfg,
    send_audit,
    send_ui,
    spawn,
    topup_bonus_cents,
    topup_tiers,
    vip_cfg,
)


log = logging.getLogger("nohara.handlers.store")

MIN_TOPUP_CENTS = 100       # $1
MAX_TOPUP_CENTS = 100_000   # $1,000
# (bot_id, user_id) -> gateway: the user chose "Custom amount" and the next text message is the amount
TOPUP_WAIT: Dict[Tuple[str, int], str] = {}
# (bot_id, user_id): the user pressed "Redeem Coupon" and the next text message is the code
COUPON_WAIT: Set[Tuple[str, int]] = set()


# ═════════════════════════════════════════════════════════════════════
#  STORE BOT — public handlers
# ═════════════════════════════════════════════════════════════════════
async def store_start(client: Client, message: Message) -> None:
    if not message.from_user:
        return
    bot_id = client.nohara_bot_id
    user_id = message.from_user.id
    ctx = await get_ctx(bot_id, user_id)
    if not ctx:
        return
    is_new = await db.touch_user(bot_id, user_id)
    if is_new and referral_cfg(ctx.clone)["enabled"]:
        args = message.command[1:] if getattr(message, "command", None) else []
        if args and args[0].startswith("ref_") and args[0][4:].isdigit():
            referrer_id = int(args[0][4:])
            if referrer_id != user_id and await db.get_user(bot_id, referrer_id):
                await db.set_referrer(bot_id, user_id, referrer_id)
    if _is_owner(ctx, user_id):
        spawn(ensure_owner_menu(client, bot_id, user_id, ctx.is_master))
    if not await ensure_fsub(client, ctx, message.chat.id, user_id):
        return
    await show_home(client, ctx, message.chat.id, user_id, name=message.from_user.first_name or "")


async def store_changelang(client: Client, message: Message) -> None:
    if not message.from_user:
        return
    ctx = await get_ctx(client.nohara_bot_id, message.from_user.id)
    if ctx:
        await send_ui(client, message.chat.id, ctx.T("select_language"), language_keyboard(ctx.T, ctx.font))


async def store_help(client: Client, message: Message) -> None:
    if not message.from_user:
        return
    ctx = await get_ctx(client.nohara_bot_id, message.from_user.id)
    if not ctx:
        return
    if _is_owner(ctx, message.from_user.id):
        text, kb = await screen_home(ctx)
        await send_ui(client, message.chat.id, text, kb)
    else:
        await show_home(client, ctx, message.chat.id, message.from_user.id, name=message.from_user.first_name or "")


async def _append_to_message(msg: Optional[Message], suffix: str) -> None:
    if not msg:
        return
    try:
        base = msg.text.html if msg.text else ""
        await msg.edit_text(f"{base}\n\n{suffix}")
    except Exception:
        pass


async def store_callback(client: Client, query: CallbackQuery) -> None:
    bot_id = client.nohara_bot_id
    user_id = query.from_user.id
    ctx = await get_ctx(bot_id, user_id)
    if not ctx:
        await query.answer()
        return

    msg = query.message
    chat_id = msg.chat.id if msg else user_id
    parts = (query.data or "").split(":")
    action = parts[0]

    # any button press cancels a pending "send me the amount / link" step (handlers below can set it again)
    TOPUP_WAIT.pop((bot_id, user_id), None)
    ADMIN_WAIT.pop((bot_id, user_id), None)
    COUPON_WAIT.discard((bot_id, user_id))

    try:
        # ── actions that do not need the force-subscribe check ──
        if action == "noop":
            await query.answer(ctx.T("out_of_stock_msg"), show_alert=True)
            return

        if action == "lang" and len(parts) == 2 and parts[1] in SUPPORTED_LANG_CODES:
            await db.set_user_lang(bot_id, user_id, parts[1])
            ctx = await get_ctx(bot_id, user_id) or ctx
            await query.answer(ctx.T("language_updated"))
            await show_home(client, ctx, chat_id, user_id, replace=msg, name=query.from_user.first_name or "")
            return

        if action == "fsub":
            if await missing_fsub(client, ctx, user_id):
                await query.answer(ctx.T("fsub_required_msg"), show_alert=True)
                return
            await query.answer()
            await show_home(client, ctx, chat_id, user_id, replace=msg, name=query.from_user.first_name or "")
            return

        if action == "cancel" and len(parts) == 2:
            order = await db.get_order(parts[1])
            if order and order["user_id"] == user_id and order["bot_id"] == bot_id:
                await db.cancel_order(order["_id"])
            await query.answer(ctx.T("order_cancelled"))
            await show_home(client, ctx, chat_id, user_id, replace=msg, name=query.from_user.first_name or "")
            return

        if action == "adm":
            await handle_admin_callback(client, query, ctx, parts)
            return

        if action == "upi" and len(parts) == 3:
            await _handle_upi_review(client, query, ctx, parts[1], parts[2])
            return

        # ── everything below requires the force-subscribe check ──
        if not await ensure_fsub(client, ctx, chat_id, user_id, replace=msg):
            await query.answer()
            return

        if action == "menu" and len(parts) == 2:
            await _handle_menu(client, query, ctx, parts[1])
            return

        if action == "fold" and len(parts) == 2:
            await query.answer()
            await _show_products(client, ctx, chat_id, msg, parts[1])
            return

        if action == "vip" and parts[1:] == ["buy"]:
            await _handle_vip_buy(client, query, ctx)
            return

        if action == "top" and len(parts) >= 3:
            await _handle_topup(client, query, ctx, parts)
            return

        if action == "prod" and len(parts) == 2:
            await _handle_product(client, query, ctx, parts[1])
            return

        if action == "pay" and len(parts) == 3:
            await _handle_pay(client, query, ctx, parts[1], parts[2])
            return

        await query.answer()
    except Exception:
        log.exception("Callback failed: %s", query.data)
        try:
            await query.answer(ctx.T("something_wrong"), show_alert=True)
        except Exception:
            pass


async def _handle_menu(client: Client, query: CallbackQuery, ctx: Ctx, target: str) -> None:
    bot_id = client.nohara_bot_id
    user_id = query.from_user.id
    msg = query.message
    chat_id = msg.chat.id if msg else user_id

    if target == "home":
        await query.answer()
        await show_home(client, ctx, chat_id, user_id, replace=msg, name=query.from_user.first_name or "")

    elif target == "products":
        await query.answer()
        await _show_products(client, ctx, chat_id, msg, None)

    elif target == "wallet":
        await query.answer()
        balance = await db.get_balance(bot_id, user_id)
        text = (
            f"👛 {ctx.H(ctx.T('my_wallet'))}\n{DIVIDER}\n"
            f"💰 {html.escape(ctx.T('balance_label'))}:  <b>{fmt_usd(balance)}</b>"
        )
        if ctx.clone["gateways"]["fampay"]["enabled"]:
            text += f"\n🇮🇳 ≈ <b>₹{cents_to_inr(balance, ctx.clone['usd_to_inr'])}</b>"
        await show(client, ctx, chat_id, text, wallet_keyboard(ctx.T, ctx.font), "wallet", msg)

    elif target == "topup":
        await query.answer()
        await _show_topup_methods(client, ctx, chat_id, query.from_user.id, msg)

    elif target == "vip":
        await query.answer()
        await _show_vip(client, ctx, chat_id, user_id, msg)

    elif target == "ref":
        await query.answer()
        await _show_referral(client, ctx, chat_id, user_id, msg)

    elif target == "coupon":
        await query.answer()
        await _prompt_coupon(client, ctx, chat_id, user_id, msg)

    elif target == "lang":
        await query.answer()
        await show(client, ctx, chat_id, ctx.T("select_language"), language_keyboard(ctx.T, ctx.font), replace=msg)

    elif target == "dash":
        if not _is_owner(ctx, user_id):
            await query.answer(ctx.T("owner_only"), show_alert=True)
            return
        await query.answer()
        text, kb = await screen_home(ctx)
        await show(client, ctx, chat_id, text, kb, replace=msg)

    elif target == "clone" and ctx.is_master:
        await query.answer()
        await show(client, ctx, chat_id, ctx.T("clone_info"), back_keyboard(ctx.T, ctx.font), "start", msg)

    else:
        await query.answer()


# ═════════════════════════════════════════════════════════════════════
#  Premium features: VIP · referral · coupon
# ═════════════════════════════════════════════════════════════════════
async def _show_vip(client: Client, ctx: Ctx, chat_id: int, user_id: int, msg, note: str = "") -> None:
    cfg = vip_cfg(ctx.clone)
    if not cfg["enabled"]:
        await show(client, ctx, chat_id, f"💎 {ctx.H('VIP')}\n{DIVIDER}\n{html.escape(ctx.TX('feature_off', 'This feature is not available right now.'))}",
                   back_keyboard(ctx.T, ctx.font), "wallet", msg)
        return
    perks = await get_user_perks(ctx.clone, user_id)
    balance = await db.get_balance(str(ctx.clone["_id"]), user_id)
    if perks["vip_active"]:
        status = f"✅ <b>{html.escape(ctx.TX('vip_active', 'Active'))}</b> · {html.escape(ctx.TX('vip_until', 'until'))} <b>{fmt_date(perks['vip_until'])}</b>"
        buy_label = ctx.TX("vip_extend", "Extend VIP — {price} / {days} days", price=fmt_usd(cfg["price_cents"]), days=cfg["days"])
    else:
        status = f"❌ {html.escape(ctx.TX('vip_inactive', 'Not a member yet'))}"
        buy_label = ctx.TX("vip_join", "Join VIP — {price} / {days} days", price=fmt_usd(cfg["price_cents"]), days=cfg["days"])
    text = (
        f"💎 {ctx.H(ctx.TX('vip_title', 'VIP Membership'))}\n{DIVIDER}\n"
        f"{html.escape(ctx.TX('vip_status', 'Status'))}: {status}\n\n"
        f"<b>{html.escape(ctx.TX('vip_benefits', 'Benefits'))}</b>\n"
        f"• 🏷 {html.escape(ctx.TX('vip_b1', '{pct}% off every product', pct=cfg['discount_pct']))}\n"
        f"• 🔓 {html.escape(ctx.TX('vip_b2', 'Access to VIP-only products'))}\n"
        f"• ⚡ {html.escape(ctx.TX('vip_b3', 'Instant delivery, always'))}\n\n"
        f"💵 {html.escape(ctx.TX('vip_price', 'Price'))}: <b>{fmt_usd(cfg['price_cents'])}</b> / {cfg['days']} {html.escape(ctx.TX('days', 'days'))}\n"
        f"👛 {html.escape(ctx.T('balance_label'))}: <b>{fmt_usd(balance)}</b>"
    )
    if note:
        text = f"{note}\n\n{text}"
    await show(client, ctx, chat_id, text, vip_keyboard(ctx.T, ctx.font, buy_label), "wallet", msg)


async def _handle_vip_buy(client: Client, query: CallbackQuery, ctx: Ctx) -> None:
    bot_id = client.nohara_bot_id
    user_id = query.from_user.id
    msg = query.message
    chat_id = msg.chat.id if msg else user_id
    cfg = vip_cfg(ctx.clone)
    if not cfg["enabled"]:
        await query.answer(ctx.TX("feature_off", "This feature is not available right now."), show_alert=True)
        return
    if not await db.debit(bot_id, user_id, int(cfg["price_cents"])):
        await query.answer(ctx.T("insufficient_balance"), show_alert=True)
        return
    until = await db.extend_vip(bot_id, user_id, int(cfg["days"]))
    await query.answer("💎 VIP activated!")
    await send_audit(
        ctx.clone,
        f"<b>[NOHARA AUDIT — VIP PURCHASE]</b>\n• User: <code>{user_id}</code> | Paid: <b>{fmt_usd(cfg['price_cents'])}</b> (wallet) | Until: {fmt_date(until)}",
    )
    await _show_vip(client, ctx, chat_id, user_id, msg, note="🎉 <b>VIP activated — enjoy your benefits!</b>")


async def _show_referral(client: Client, ctx: Ctx, chat_id: int, user_id: int, msg) -> None:
    cfg = referral_cfg(ctx.clone)
    if not cfg["enabled"]:
        await show(client, ctx, chat_id, f"🎁 {ctx.H('Refer & Earn')}\n{DIVIDER}\n{html.escape(ctx.TX('feature_off', 'This feature is not available right now.'))}",
                   back_keyboard(ctx.T, ctx.font), "wallet", msg)
        return
    bot_id = str(ctx.clone["_id"])
    username = str(ctx.clone.get("bot_username", "")).lstrip("@")
    link = f"https://t.me/{username}?start=ref_{user_id}"
    invited = await db.count_referrals(bot_id, user_id)
    user = await db.get_user(bot_id, user_id) or {}
    earned = int(user.get("ref_earned_cents") or 0)
    text = (
        f"🎁 {ctx.H(ctx.TX('ref_title', 'Refer & Earn'))}\n{DIVIDER}\n"
        f"{html.escape(ctx.TX('ref_desc', 'Share your personal link. When a friend makes their first payment, you earn {amount} in your wallet — unlimited!', amount=fmt_usd(int(cfg['bonus_cents']))))}\n\n"
        f"🔗 <b>{html.escape(ctx.TX('ref_your_link', 'Your link'))}</b>\n<code>{html.escape(link)}</code>\n\n"
        f"👥 {html.escape(ctx.TX('ref_joined', 'Friends joined'))}: <b>{invited}</b>\n"
        f"💰 {html.escape(ctx.TX('ref_earned', 'Total earned'))}: <b>{fmt_usd(earned)}</b>"
    )
    share_text = ctx.TX("ref_share_text", "Join this store and get premium digital products instantly!")
    share_url = f"https://t.me/share/url?url={quote(link, safe='')}&text={quote(share_text, safe='')}"
    await show(client, ctx, chat_id, text, referral_keyboard(ctx.T, ctx.font, share_url), "wallet", msg)


async def _prompt_coupon(client: Client, ctx: Ctx, chat_id: int, user_id: int, msg) -> None:
    perks = await get_user_perks(ctx.clone, user_id)
    active = (perks["user"] or {}).get("coupon")
    text = (
        f"🎟 {ctx.H(ctx.TX('coupon_title', 'Redeem Coupon'))}\n{DIVIDER}\n"
        f"{html.escape(ctx.TX('coupon_send', 'Send your coupon code as a message.'))}"
    )
    if active:
        text += f"\n\n✅ {html.escape(ctx.TX('coupon_active', 'Active coupon'))}: <code>{html.escape(active['code'])}</code> (−{active['pct']}%)"
    COUPON_WAIT.add((str(ctx.clone["_id"]), user_id))
    await show(client, ctx, chat_id, text, back_keyboard(ctx.T, ctx.font), "wallet", msg)


async def _handle_coupon_input(client: Client, message: Message, ctx: Ctx) -> None:
    bot_id = str(ctx.clone["_id"])
    user_id = message.from_user.id
    code = (message.text or "").strip().upper()
    coupon = await db.get_coupon(bot_id, code) if code and len(code) <= 32 else None
    if not coupon_is_valid(coupon, user_id):
        await message.reply_text("❌ " + html.escape(ctx.TX("coupon_invalid", "Invalid, expired or already used coupon. Send another code, or press Home.")))
        return
    COUPON_WAIT.discard((bot_id, user_id))
    await db.set_user_coupon(bot_id, user_id, coupon["code"], int(coupon["pct"]))
    await send_ui(
        client, message.chat.id,
        f"✅ <b>{html.escape(ctx.TX('coupon_applied', 'Coupon applied!'))}</b>\n"
        f"🎟 <code>{html.escape(coupon['code'])}</code> — <b>{coupon['pct']}% OFF</b>\n"
        f"{html.escape(ctx.TX('coupon_note', 'It is applied automatically to your next purchase (one use).'))}",
        after_coupon_keyboard(ctx.T, ctx.font),
    )


async def _vip_gate(client: Client, ctx: Ctx, chat_id: int, user_id: int, product: dict, msg, back_cb: str) -> bool:
    """True when the product is VIP-only and this user may not open it (the lock screen is shown)."""
    if not product.get("vip_only") or _is_owner(ctx, user_id):
        return False
    perks = await get_user_perks(ctx.clone, user_id)
    if perks["vip_active"]:
        return False
    text = (
        f"🔒 {ctx.H(product['title'])}\n{DIVIDER}\n"
        f"💎 {html.escape(ctx.TX('vip_only_msg', 'This is a VIP-only product. Join VIP to unlock it and save on everything else too.'))}"
    )
    await show(client, ctx, chat_id, text, vip_locked_keyboard(ctx.T, ctx.font, back_cb), "product", msg)
    return True


# ═════════════════════════════════════════════════════════════════════
#  Wallet top-up
# ═════════════════════════════════════════════════════════════════════
def _gateway_flags(ctx: Ctx) -> Tuple[bool, bool]:
    """(crypto live, UPI live) — the same switches the owner controls in Admin Panel -> Payments."""
    gw = ctx.clone["gateways"]
    oxapay_on = bool(gw["oxapay"]["enabled"] and APP_URL and db.get_oxapay_key(ctx.clone))
    fampay_on = bool(gw["fampay"]["enabled"] and gw["fampay"]["upi_id"])
    return oxapay_on, fampay_on


async def _show_topup_methods(client: Client, ctx: Ctx, chat_id: int, user_id: int, msg) -> None:
    oxapay_on, fampay_on = _gateway_flags(ctx)
    title = f"💰 {ctx.H(ctx.TX('topup_title', 'Top Up Wallet'))}"
    if not (oxapay_on or fampay_on):
        text = f"{title}\n{DIVIDER}\n" + html.escape(
            ctx.TX("topup_unavailable", "Top-up is not available right now. Please contact the store owner.")
        )
        await show(client, ctx, chat_id, text, back_keyboard(ctx.T, ctx.font), "wallet", msg)
        return
    balance = await db.get_balance(str(ctx.clone["_id"]), user_id)
    text = (
        f"{title}\n{DIVIDER}\n"
        f"💰 {html.escape(ctx.T('balance_label'))}:  <b>{fmt_usd(balance)}</b>\n\n"
        f"{html.escape(ctx.TX('topup_choose_method', 'Choose a payment method:'))}"
    )
    await show(client, ctx, chat_id, text, topup_methods_keyboard(ctx.T, ctx.font, oxapay_on, fampay_on), "wallet", msg)


async def _show_topup_amounts(client: Client, ctx: Ctx, chat_id: int, method: str, msg) -> None:
    name = "🪙 Crypto (OxaPay)" if method == "oxapay" else "🇮🇳 UPI"
    text = (
        f"💰 {ctx.H(ctx.TX('topup_title', 'Top Up Wallet'))}\n{DIVIDER}\n"
        f"{name}\n\n"
        f"{html.escape(ctx.TX('topup_choose_amount', 'Choose an amount (USD):'))}\n"
        f"<i>Min {fmt_usd(MIN_TOPUP_CENTS)} · Max {fmt_usd(MAX_TOPUP_CENTS)}</i>"
    )
    if method == "fampay":
        text += f"\n<i>1 USD = ₹{ctx.clone['usd_to_inr']}</i>"
    tiers = topup_tiers(ctx.clone)
    if tiers:
        text += "\n\n🎁 <b>" + html.escape(ctx.TX("bonus_title", "Top-up bonus")) + "</b>\n" + "\n".join(
            f"• {fmt_usd(int(t['min_cents']))}+ → <b>+{int(t['pct'])}%</b>" for t in tiers
        )
    await show(client, ctx, chat_id, text, topup_amount_keyboard(method, ctx.T, ctx.font), "wallet", msg)


async def _handle_topup(client: Client, query: CallbackQuery, ctx: Ctx, parts: list) -> None:
    """Callbacks: top:m:<gw> (pick method) · top:a:<gw>:<cents> (pick amount) · top:c:<gw> (custom amount)."""
    bot_id = client.nohara_bot_id
    user_id = query.from_user.id
    msg = query.message
    chat_id = msg.chat.id if msg else user_id
    sub, method = parts[1], parts[2]

    oxapay_on, fampay_on = _gateway_flags(ctx)
    live = {"oxapay": oxapay_on, "fampay": fampay_on}
    if not live.get(method):
       
