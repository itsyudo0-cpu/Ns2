"""
NOHARA STORE BOT — Store bot — public handlers: /start, language, menus, product pages, payments, UPI proof.
(nohara_bot/handlers/store.py)
"""

import html
import logging
from decimal import Decimal
from hydrogram import Client
from hydrogram.types import CallbackQuery, Message
from typing import Optional

from nohara_bot.buttons import (
    back_keyboard,
    cancel_keyboard,
    language_keyboard,
    payment_link_keyboard,
    product_keyboard,
    products_keyboard,
    upi_review_keyboard,
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
from nohara_bot.handlers.owner import dashboard_text
from nohara_bot.i18n import SUPPORTED_LANG_CODES
from nohara_bot.payments import create_oxapay_invoice
from nohara_bot.services.core import (
    Ctx,
    cents_to_inr,
    fmt_usd,
    fulfill_paid_order,
    get_ctx,
    send_ui,
    spawn,
)


log = logging.getLogger("nohara.handlers.store")


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
    await db.touch_user(bot_id, user_id)
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
        await message.reply_text(await dashboard_text(client.nohara_bot_id, ctx))
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
        products = await db.list_products(bot_id)
        if not products:
            empty = f"🛍 {ctx.H(ctx.T('collection_title'))}\n{DIVIDER}\n{html.escape(ctx.T('no_products'))}"
            await show(client, ctx, chat_id, empty, back_keyboard(ctx.T, ctx.font), "catalog", msg)
            return
        kb = products_keyboard(products, ctx.T, ctx.font, fmt_usd)
        text = (
            f"🛍 {ctx.H(ctx.T('collection_title'))}  <b>·</b>  {len(products)} {html.escape(ctx.T('items_label'))}\n"
            f"{DIVIDER}\n<i>{html.escape(ctx.T('tap_to_view'))}</i>"
        )
        await show(client, ctx, chat_id, text, kb, "catalog", msg)

    elif target == "wallet":
        await query.answer()
        balance = await db.get_balance(bot_id, user_id)
        text = (
            f"👛 {ctx.H(ctx.T('my_wallet'))}\n{DIVIDER}\n"
            f"💰 {html.escape(ctx.T('balance_label'))}:  <b>{fmt_usd(balance)}</b>"
        )
        if ctx.clone["gateways"]["fampay"]["enabled"]:
            text += f"\n🇮🇳 ≈ <b>₹{cents_to_inr(balance, ctx.clone['usd_to_inr'])}</b>"
        await show(client, ctx, chat_id, text, back_keyboard(ctx.T, ctx.font), "wallet", msg)

    elif target == "lang":
        await query.answer()
        await show(client, ctx, chat_id, ctx.T("select_language"), language_keyboard(ctx.T, ctx.font), replace=msg)

    elif target == "dash":
        if not _is_owner(ctx, user_id):
            await query.answer(ctx.T("owner_only"), show_alert=True)
            return
        await query.answer()
        await show(client, ctx, chat_id, await dashboard_text(bot_id, ctx), back_keyboard(ctx.T, ctx.font), replace=msg)

    elif target == "clone" and ctx.is_master:
        await query.answer()
        await show(client, ctx, chat_id, ctx.T("clone_info"), back_keyboard(ctx.T, ctx.font), "start", msg)

    else:
        await query.answer()


async def _handle_product(client: Client, query: CallbackQuery, ctx: Ctx, product_id: str) -> None:
    bot_id = client.nohara_bot_id
    msg = query.message
    chat_id = msg.chat.id if msg else query.from_user.id
    product = await db.get_product(bot_id, product_id)
    if not product:
        await query.answer(ctx.T("something_wrong"), show_alert=True)
        return
    await query.answer()

    gateways = ctx.clone["gateways"]
    oxapay_on = bool(gateways["oxapay"]["enabled"] and APP_URL)
    fampay_on = bool(gateways["fampay"]["enabled"] and gateways["fampay"]["upi_id"])
    in_stock = product["stock"] != 0

    price = f"<b>{fmt_usd(product['price_cents'])}</b>"
    if fampay_on:
        price += f"  (≈ ₹{cents_to_inr(product['price_cents'], ctx.clone['usd_to_inr'])})"
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

    kb = product_keyboard(product_id, ctx.T, ctx.font, in_stock, oxapay_on, fampay_on)
    await show(client, ctx, chat_id, text, kb, "catalog" if in_stock else "stock", msg)


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
        await show(client, ctx, chat_id, text, kb, replace=msg)
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
        await show(client, ctx, chat_id, text, cancel_keyboard(order["_id"], ctx.T, ctx.font), replace=msg)
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
    order = await db.get_open_upi_order(bot_id, user_id)
    if not order:
        return
    ctx = await get_ctx(bot_id, user_id)
    if not ctx:
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
