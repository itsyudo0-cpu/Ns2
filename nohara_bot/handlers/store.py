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
    is_premium,
    parse_usd_to_cents,
    price_for,
    referral_cfg,
    send_audit,
    send_ui,
    spawn,
    topup_bonus_cents,
    topup_tiers,
    vip_cfg,
    vip_plans,
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
    if is_new and is_premium(ctx.clone) and referral_cfg(ctx.clone)["enabled"]:
        args = message.command[1:] if getattr(message, "command", None) else []
        if args and args[0].startswith("ref_") and args[0][4:].isdigit():
            referrer_id = int(args[0][4:])
            if referrer_id != user_id and await db.get_user(bot_id, referrer_id):
                await db.set_referrer(bot_id, user_id, referrer_id)
    if _is_owner(ctx, user_id):
        spawn(ensure_owner_menu(client, bot_id, user_id, ctx.is_master))
    if not await ensure_fsub(client, ctx, message.chat.id, user_id):
        return
    # Direct product link  t.me/<bot>?start=p_<product id>  → the buy page itself (Premium feature of a clone)
    args = message.command[1:] if getattr(message, "command", None) else []
    if args and args[0].startswith("p_") and is_premium(ctx.clone):
        product = await db.get_product(bot_id, args[0][2:])
        if product:
            await _render_product(client, ctx, message.chat.id, user_id, product, None, back_cb="menu:home")
            return
        await message.reply_text("⚠️ " + html.escape(ctx.TX("product_gone", "This product is no longer available.")))
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

        if action == "vip" and len(parts) == 3 and parts[1] == "buy" and parts[2].isdigit():
            await _handle_vip_buy(client, query, ctx, int(parts[2]))
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

    elif target in ("vip", "ref", "coupon"):
        if not is_premium(ctx.clone):  # stale button from before the feature was locked
            await query.answer(ctx.TX("feature_off", "This feature is not available right now."), show_alert=True)
            return
        await query.answer()
        if target == "vip":
            await _show_vip(client, ctx, chat_id, user_id, msg)
        elif target == "ref":
            await _show_referral(client, ctx, chat_id, user_id, msg)
        else:
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
    if not cfg["enabled"] or not is_premium(ctx.clone):
        await show(client, ctx, chat_id, f"💎 {ctx.H('VIP')}\n{DIVIDER}\n{html.escape(ctx.TX('feature_off', 'This feature is not available right now.'))}",
                   back_keyboard(ctx.T, ctx.font), "wallet", msg)
        return
    perks = await get_user_perks(ctx.clone, user_id)
    balance = await db.get_balance(str(ctx.clone["_id"]), user_id)
    plans = vip_plans(ctx.clone)
    if perks["vip_active"]:
        status = f"✅ <b>{html.escape(ctx.TX('vip_active', 'Active'))}</b> · {html.escape(ctx.TX('vip_until', 'until'))} <b>{fmt_date(perks['vip_until'])}</b>"
    else:
        status = f"❌ {html.escape(ctx.TX('vip_inactive', 'Not a member yet'))}"
    plan_lines = "\n".join(f"• <b>{m} Month{'' if m == 1 else 's'}</b> — {fmt_usd(c)}" for m, c in plans)
    text = (
        f"💎 {ctx.H(ctx.TX('vip_title', 'VIP Membership'))}\n{DIVIDER}\n"
        f"{html.escape(ctx.TX('vip_status', 'Status'))}: {status}\n\n"
        f"<b>{html.escape(ctx.TX('vip_benefits', 'Benefits'))}</b>\n"
        f"• 🏷 {html.escape(ctx.TX('vip_b1', '{pct}% off every product', pct=cfg['discount_pct']))}\n"
        f"• 🔓 {html.escape(ctx.TX('vip_b2', 'Access to VIP-only products'))}\n"
        f"• ⚡ {html.escape(ctx.TX('vip_b3', 'Instant delivery, always'))}\n\n"
        f"💵 <b>{html.escape(ctx.TX('vip_plans', 'Choose a plan'))}</b>\n{plan_lines}\n\n"
        f"👛 {html.escape(ctx.T('balance_label'))}: <b>{fmt_usd(balance)}</b>"
    )
    if note:
        text = f"{note}\n\n{text}"
    await show(client, ctx, chat_id, text, vip_keyboard(ctx.T, ctx.font, plans, fmt_usd), "wallet", msg)


async def _handle_vip_buy(client: Client, query: CallbackQuery, ctx: Ctx, months: int) -> None:
    bot_id = client.nohara_bot_id
    user_id = query.from_user.id
    msg = query.message
    chat_id = msg.chat.id if msg else user_id
    cfg = vip_cfg(ctx.clone)
    price = dict(vip_plans(ctx.clone)).get(months)
    if not cfg["enabled"] or not is_premium(ctx.clone) or not price:
        await query.answer(ctx.TX("feature_off", "This feature is not available right now."), show_alert=True)
        return
    if not await db.debit(bot_id, user_id, int(price)):
        await query.answer(ctx.T("insufficient_balance"), show_alert=True)
        return
    until = await db.extend_vip(bot_id, user_id, months * 30)
    await query.answer("💎 VIP activated!")
    await send_audit(
        ctx.clone,
        f"<b>[NOHARA AUDIT — VIP PURCHASE]</b>\n• User: <code>{user_id}</code> | Plan: {months} month(s) | Paid: <b>{fmt_usd(price)}</b> (wallet) | Until: {fmt_date(until)}",
    )
    await _show_vip(client, ctx, chat_id, user_id, msg, note="🎉 <b>VIP activated — enjoy your benefits!</b>")


async def _show_referral(client: Client, ctx: Ctx, chat_id: int, user_id: int, msg) -> None:
    cfg = referral_cfg(ctx.clone)
    if not cfg["enabled"] or not is_premium(ctx.clone):
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
    if not product.get("vip_only") or _is_owner(ctx, user_id) or not is_premium(ctx.clone):
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
        await query.answer(ctx.T("gateway_error"), show_alert=True)
        return

    if sub == "m":
        await query.answer()
        await _show_topup_amounts(client, ctx, chat_id, method, msg)
    elif sub == "c":
        await query.answer()
        TOPUP_WAIT[(bot_id, user_id)] = method
        text = (
            f"✍️ {ctx.H(ctx.TX('btn_custom_amount', 'Custom amount'))}\n{DIVIDER}\n"
            f"{html.escape(ctx.TX('topup_send_amount', 'Send the amount in USD as a message, for example:'))} <code>12.5</code>\n"
            f"<i>Min {fmt_usd(MIN_TOPUP_CENTS)} · Max {fmt_usd(MAX_TOPUP_CENTS)}</i>"
        )
        await show(client, ctx, chat_id, text, topup_cancel_keyboard(ctx.T, ctx.font), "wallet", msg)
    elif sub == "a" and len(parts) == 4 and parts[3].isdigit():
        cents = int(parts[3])
        if not MIN_TOPUP_CENTS <= cents <= MAX_TOPUP_CENTS:
            await query.answer(ctx.T("something_wrong"), show_alert=True)
            return
        await query.answer()
        await _create_topup(client, ctx, chat_id, user_id, method, cents, msg)
    else:
        await query.answer()


async def _create_topup(client: Client, ctx: Ctx, chat_id: int, user_id: int, method: str, cents: int, replace) -> None:
    """Creates a pending top-up order and shows the payment information. The wallet is credited by
    fulfill_paid_order() once OxaPay confirms the payment / the owner approves the UPI proof."""
    bot_id = str(ctx.clone["_id"])
    gateways = ctx.clone["gateways"]
    pseudo_product = {"_id": "topup", "title": "Wallet top-up", "price_cents": cents}

    if method == "oxapay":
        api_key = db.get_oxapay_key(ctx.clone)
        if not (gateways["oxapay"]["enabled"] and api_key and APP_URL):
            await client.send_message(chat_id, ctx.T("gateway_error"))
            return
        order = await db.create_order(bot_id, user_id, pseudo_product, method="oxapay", status="pending", kind="topup")
        invoice = await create_oxapay_invoice(
            merchant_api_key=api_key,
            amount_usd=Decimal(cents) / 100,
            order_id=order["_id"],
            callback_url=f"{APP_URL}/webhook/oxapay/{bot_id}",
            description="Wallet top-up",
        )
        if not invoice:
            await db.set_order_fields(order["_id"], status="failed")
            await client.send_message(chat_id, ctx.T("gateway_error"))
            return
        await db.set_order_fields(order["_id"], track_id=str(invoice["track_id"]))
        text = (
            f"💰 {ctx.H(ctx.TX('topup_title', 'Top Up Wallet'))}\n💵 <b>{fmt_usd(cents)}</b>{_bonus_note(ctx, cents)}\n\n"
            f"{html.escape(ctx.TX('topup_pay_link_msg', 'Tap the button below to pay. Your wallet is credited automatically once the payment is confirmed.'))}"
        )
        kb = payment_link_keyboard(invoice["payment_url"], order["_id"], ctx.T, ctx.font)
        await show(client, ctx, chat_id, text, kb, banner="oxapay", replace=replace)
        return

    if method == "fampay":
        upi_id = gateways["fampay"]["upi_id"]
        if not (gateways["fampay"]["enabled"] and upi_id):
            await client.send_message(chat_id, ctx.T("gateway_error"))
            return
        amount_inr = cents_to_inr(cents, ctx.clone["usd_to_inr"])
        order = await db.create_order(bot_id, user_id, pseudo_product, method="upi", status="pending", amount_inr=str(amount_inr), kind="topup")
        text = (
            f"💰 {ctx.H(ctx.TX('topup_title', 'Top Up Wallet'))}  ·  <b>{fmt_usd(cents)}</b>{_bonus_note(ctx, cents)}\n\n"
            + ctx.T("upi_instructions", amount=amount_inr, upi=html.escape(upi_id), order_id=order["_id"])
        )
        await show(client, ctx, chat_id, text, cancel_keyboard(order["_id"], ctx.T, ctx.font), banner="fampay", replace=replace)


def _bonus_note(ctx: Ctx, cents: int) -> str:
    bonus = topup_bonus_cents(ctx.clone, cents)
    return f"\n🎁 {html.escape(ctx.TX('bonus_label', 'Bonus'))}: <b>+{fmt_usd(bonus)}</b>" if bonus else ""


async def _handle_custom_amount(client: Client, message: Message, ctx: Ctx, key: Tuple[str, int]) -> None:
    method = TOPUP_WAIT.get(key)
    cents = parse_usd_to_cents(message.text or "")
    if cents is None or not MIN_TOPUP_CENTS <= cents <= MAX_TOPUP_CENTS:
        await message.reply_text(
            f"❌ Please send a valid amount between {fmt_usd(MIN_TOPUP_CENTS)} and {fmt_usd(MAX_TOPUP_CENTS)} (example: <code>12.5</code>)."
        )
        return
    TOPUP_WAIT.pop(key, None)
    oxapay_on, fampay_on = _gateway_flags(ctx)
    if not {"oxapay": oxapay_on, "fampay": fampay_on}.get(method or ""):
        await message.reply_text(ctx.T("gateway_error"))
        return
    await _create_topup(client, ctx, message.chat.id, message.from_user.id, method, cents, None)


async def _show_products(client: Client, ctx: Ctx, chat_id: int, msg, folder_id) -> None:
    """Folder list (if the store uses folders) -> product list. folder_id: None = top level, '_' = ungrouped."""
    bot_id = str(ctx.clone["_id"])
    folders = ctx.clone.get("folders") or []
    known = {f["id"]: f["name"] for f in folders}
    everything = await db.list_products(bot_id)
    title = f"🛍 {ctx.H(ctx.T('collection_title'))}"

    if folder_id is None and folders:
        counts: dict = {}
        for p in everything:
            key = p.get("folder") if p.get("folder") in known else ""
            counts[key] = counts.get(key, 0) + 1
        shown = [(f, counts[f["id"]]) for f in folders if counts.get(f["id"])]
        if shown:
            text = f"{title}\n{DIVIDER}\n<i>{html.escape(ctx.T('choose_category'))}</i>"
            await show(client, ctx, chat_id, text, folders_keyboard(shown, counts.get("", 0), ctx.T, ctx.font), "catalog", msg)
            return

    back = "menu:home"
    if folder_id is None:
        items, head = everything, title
    elif folder_id == "_":
        items = [p for p in everything if p.get("folder") not in known]
        head, back = f"📦 {ctx.H(ctx.T('other_products'))}", "menu:products"
    else:
        items = [p for p in everything if p.get("folder") == folder_id]
        head, back = f"🗂 {ctx.H(known.get(folder_id, 'Folder'))}", "menu:products"

    if not items:
        text = f"{head}\n{DIVIDER}\n{html.escape(ctx.T('no_products'))}"
        await show(client, ctx, chat_id, text, back_keyboard(ctx.T, ctx.font), "catalog", msg)
        return
    text = f"{head}  <b>·</b>  {len(items)} {html.escape(ctx.T('items_label'))}\n{DIVIDER}\n<i>{html.escape(ctx.T('tap_to_view'))}</i>"
    await show(client, ctx, chat_id, text, products_keyboard(items, ctx.T, ctx.font, fmt_usd, back), "catalog", msg)


async def _handle_product(client: Client, query: CallbackQuery, ctx: Ctx, product_id: str) -> None:
    bot_id = client.nohara_bot_id
    msg = query.message
    chat_id = msg.chat.id if msg else query.from_user.id
    product = await db.get_product(bot_id, product_id)
    if not product:
        await query.answer(ctx.T("something_wrong"), show_alert=True)
        return
    await query.answer()
    await _render_product(client, ctx, chat_id, query.from_user.id, product, msg)


async def _render_product(
    client: Client, ctx: Ctx, chat_id: int, user_id: int, product: dict, msg, back_cb: Optional[str] = None
) -> None:
    """The product buy page. Used by the product button AND by direct product links (back_cb = menu:home)."""
    product_id = product["_id"]
    known = {f["id"] for f in ctx.clone.get("folders") or []}
    if back_cb:
        lock_back = back_cb
    elif known:
        lock_back = f"fold:{product['folder']}" if product.get("folder") in known else "fold:_"
    else:
        lock_back = "menu:products"
    if await _vip_gate(client, ctx, chat_id, user_id, product, msg, lock_back):
        return

    gateways = ctx.clone["gateways"]
    oxapay_on = bool(gateways["oxapay"]["enabled"] and APP_URL)
    fampay_on = bool(gateways["fampay"]["enabled"] and gateways["fampay"]["upi_id"])
    in_stock = product["stock"] != 0

    perks = await get_user_perks(ctx.clone, user_id)
    final_cents, info = await price_for(ctx.clone, perks, user_id, product)
    if info["pct"]:
        tag = "💎 VIP" if info["source"] == "vip" else f"🎟 {html.escape(info['code'])}"
        price = f"<s>{fmt_usd(info['list_cents'])}</s> → <b>{fmt_usd(final_cents)}</b>  ({tag} −{info['pct']}%)"
    else:
        price = f"<b>{fmt_usd(final_cents)}</b>"
    if fampay_on:
        price += f"  (≈ ₹{cents_to_inr(final_cents, ctx.clone['usd_to_inr'])})"
    stock_text = (
        ctx.T("unlimited_label") if product["stock"] == -1 else f"{product['stock']} {ctx.T('left_label')}"
    )
    text = (
        f"💎 {ctx.H(product['title'])}\n{DIVIDER}\n"
        f"💵 {html.escape(ctx.T('price_label'))}: {price}\n"
        f"📦 {html.escape(ctx.T('stock_label'))}: <b>{html.escape(stock_text)}</b>\n"
        f"⚡ {html.escape(ctx.T('instant_delivery_line'))}"
    )
    if not in_stock:
        text += f"\n\n{html.escape(ctx.T('out_of_stock_msg'))}"

    kb = product_keyboard(product_id, ctx.T, ctx.font, in_stock, oxapay_on, fampay_on, lock_back)
    await show(client, ctx, chat_id, text, kb, "product" if in_stock else "stock", msg)


async def _handle_pay(client: Client, query: CallbackQuery, ctx: Ctx, method: str, product_id: str) -> None:
    bot_id = client.nohara_bot_id
    user_id = query.from_user.id
    msg = query.message
    chat_id = msg.chat.id if msg else user_id

    product = await db.get_product(bot_id, product_id)
    if not product:
        await query.answer(ctx.T("something_wrong"), show_alert=True)
        return
    if product["stock"] == 0:
        await query.answer(ctx.T("out_of_stock_msg"), show_alert=True)
        return

    perks = await get_user_perks(ctx.clone, user_id)
    if product.get("vip_only") and is_premium(ctx.clone) and not perks["vip_active"] and not _is_owner(ctx, user_id):
        await query.answer(ctx.TX("vip_only_alert", "VIP-only product. Join VIP to unlock it."), show_alert=True)
        return
    # VIP / coupon discount: from here on `product["price_cents"]` is the price this user really pays
    final_cents, info = await price_for(ctx.clone, perks, user_id, product)
    product = {
        **product,
        "price_cents": final_cents,
        "list_price_cents": info["list_cents"],
        "coupon": info["code"] if info["source"] == "coupon" else "",
    }

    gateways = ctx.clone["gateways"]

    # ───────────── Wallet ─────────────
    if method == "wallet":
        # 1) take one unit of stock (atomic)  2) take the money (atomic)  3) deliver
        if not await db.reserve_stock(bot_id, product_id):
            await query.answer(ctx.T("out_of_stock_msg"), show_alert=True)
            return
        if not await db.debit(bot_id, user_id, product["price_cents"]):
            await db.release_stock(bot_id, product_id)
            await query.answer(ctx.T("insufficient_balance"), show_alert=True)
            return
        order = await db.create_order(bot_id, user_id, product, method="wallet", status="paid")
        await query.answer()
        try:
            if msg:
                await msg.delete()
        except Exception:
            pass
        await fulfill_paid_order(order, stock_reserved=True)
        return

    # ───────────── OxaPay (crypto) ─────────────
    if method == "oxapay":
        api_key = db.get_oxapay_key(ctx.clone)
        if not (gateways["oxapay"]["enabled"] and api_key and APP_URL):
            await query.answer(ctx.T("gateway_error"), show_alert=True)
            return
        await query.answer()
        order = await db.create_order(bot_id, user_id, product, method="oxapay", status="pending")
        invoice = await create_oxapay_invoice(
            merchant_api_key=api_key,
            amount_usd=Decimal(product["price_cents"]) / 100,
            order_id=order["_id"],
            callback_url=f"{APP_URL}/webhook/oxapay/{bot_id}",
            description=product["title"],
        )
        if not invoice:
            await db.set_order_fields(order["_id"], status="failed")
            await client.send_message(chat_id, ctx.T("gateway_error"))
            return
        await db.set_order_fields(order["_id"], track_id=str(invoice["track_id"]))
        text = f"{ctx.H(product['title'])}\n💵 <b>{fmt_usd(product['price_cents'])}</b>\n\n{html.escape(ctx.T('pay_link_msg'))}"
        kb = payment_link_keyboard(invoice["payment_url"], order["_id"], ctx.T, ctx.font)
        await show(client, ctx, chat_id, text, kb, banner="oxapay", replace=msg)
        return

    # ───────────── UPI / FamPay (manual verification by the owner) ─────────────
    if method == "fampay":
        upi_id = gateways["fampay"]["upi_id"]
        if not (gateways["fampay"]["enabled"] and upi_id):
            await query.answer(ctx.T("gateway_error"), show_alert=True)
            return
        await query.answer()
        amount_inr = cents_to_inr(product["price_cents"], ctx.clone["usd_to_inr"])
        order = await db.create_order(bot_id, user_id, product, method="upi", status="pending", amount_inr=str(amount_inr))
        text = ctx.T("upi_instructions", amount=amount_inr, upi=html.escape(upi_id), order_id=order["_id"])
        await show(client, ctx, chat_id, text, cancel_keyboard(order["_id"], ctx.T, ctx.font), banner="fampay", replace=msg)
        return

    await query.answer()


async def _handle_upi_review(client: Client, query: CallbackQuery, ctx: Ctx, decision: str, order_id: str) -> None:
    """Owner presses Approve / Reject under a payment proof."""
    bot_id = client.nohara_bot_id
    if not _is_owner(ctx, query.from_user.id):
        await query.answer(ctx.T("owner_only"), show_alert=True)
        return
    order = await db.get_order(order_id)
    if not order or order["bot_id"] != bot_id or order["method"] != "upi":
        await query.answer("Order not found.", show_alert=True)
        return

    if decision == "ok":
        paid = await db.mark_paid(order_id)  # atomic -> a double click can never deliver twice
        if not paid:
            await query.answer("Already processed.", show_alert=True)
            return
        await query.answer("Approved ✅")
        await _append_to_message(query.message, "✅ <b>Approved</b>")
        spawn(fulfill_paid_order(paid, stock_reserved=False))
    else:
        if not await db.cancel_order(order_id):
            await query.answer("Already processed.", show_alert=True)
            return
        await query.answer("Rejected")
        await _append_to_message(query.message, "❌ <b>Rejected</b>")
        buyer_ctx = await get_ctx(bot_id, order["user_id"])
        if buyer_ctx:
            try:
                await client.send_message(order["user_id"], buyer_ctx.T("upi_rejected"))
            except Exception:
                pass


async def store_user_message(client: Client, message: Message) -> None:
    """A buyer sends a UTR / screenshot after choosing UPI payment -> forward to the owner for review."""
    if not message.from_user:
        return
    bot_id = client.nohara_bot_id
    user_id = message.from_user.id
    ctx = await get_ctx(bot_id, user_id)
    if not ctx:
        return

    if message.text:
        if await handle_link_input(client, message, ctx):  # owner is typing an Updates / Support link
            return
        if (bot_id, user_id) in COUPON_WAIT:  # user is typing a coupon code
            await _handle_coupon_input(client, message, ctx)
            return
        if (bot_id, user_id) in TOPUP_WAIT:  # user is typing a custom top-up amount
            await _handle_custom_amount(client, message, ctx, (bot_id, user_id))
            return

    order = await db.get_open_upi_order(bot_id, user_id)
    if not order:
        return

    summary = (
        f"💸 <b>UPI payment proof</b>\n"
        f"• Order: <code>{order['_id']}</code>\n"
        f"• Buyer: <code>{user_id}</code> {html.escape(message.from_user.first_name or '')}\n"
        f"• Product: <b>{html.escape(order['title'])}</b>\n"
        f"• Amount: <b>₹{order.get('amount_inr', '?')}</b> ({fmt_usd(order['price_cents'])})"
    )
    try:
        await message.forward(ctx.owner_id)
        await send_ui(client, ctx.owner_id, summary, upi_review_keyboard(order["_id"]))
    except Exception:
        log.exception("Could not forward UPI proof to owner")
        await message.reply_text(ctx.T("something_wrong"))
        return
    await message.reply_text(ctx.T("upi_proof_received"))
    
