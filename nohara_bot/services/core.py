"""
NOHARA STORE BOT — Shared services (nohara_bot/services/core.py)

* ACTIVE_CLONE_CLIENTS / MASTER : running Hydrogram clients
* Ctx                           : per-user language / font / translation helper
* deliver + refund logic        : used by handlers.py (wallet, UPI) and webhooks.py (OxaPay)
* audit logging                 : clone log channel + master audit channel (metadata only)
"""

import asyncio
import html
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Optional

from hydrogram import Client
from hydrogram.enums import ParseMode
from hydrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from nohara_bot import botapi
from nohara_bot.config import API_ID, API_HASH, MASTER_AUDIT_CHANNEL_ID, DEFAULT_FONT_STYLE
from nohara_bot.database import db
from nohara_bot.fonts import style_html
from nohara_bot.i18n import get_text

log = logging.getLogger("nohara.services")

# bot_id (str) -> running clone Client
ACTIVE_CLONE_CLIENTS: Dict[str, Client] = {}
# Holder for the master bot client (set in main.py)
MASTER: Dict[str, Optional[Client]] = {"client": None}


# ───────────────────────── Client factory ─────────────────────────
def make_client(name: str, bot_token: str) -> Client:
    client = Client(
        name=name,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=bot_token,
        in_memory=True,
    )
    # All our texts are Telegram-HTML. Set it defensively (works on every Pyrogram-style API).
    try:
        client.set_parse_mode(ParseMode.HTML)
    except Exception:
        client.parse_mode = ParseMode.HTML
    client.nohara_token = bot_token  # needed for Bot API calls (coloured buttons)
    return client


# ───────────────────────── Sending messages WITH buttons ─────────────────────────
def to_hydrogram_markup(markup: Dict[str, Any]) -> InlineKeyboardMarkup:
    """Colour-less fallback keyboard used only if the Bot API call fails."""
    rows = []
    for row in markup.get("inline_keyboard", []):
        buttons = []
        for b in row:
            if "url" in b:
                buttons.append(InlineKeyboardButton(b["text"], url=b["url"]))
            else:
                buttons.append(InlineKeyboardButton(b["text"], callback_data=b["callback_data"]))
        rows.append(buttons)
    return InlineKeyboardMarkup(rows)


async def send_ui(client: Client, chat_id: int, text: str, markup: Optional[Dict[str, Any]] = None) -> None:
    """Sends text (+ optional keyboard). Coloured buttons go through the Bot API; Hydrogram is the fallback."""
    if not markup:
        await client.send_message(chat_id, text)
        return
    try:
        await botapi.send_message(client.nohara_token, chat_id, text, markup)
    except Exception as exc:
        log.warning("Bot API send failed (%s) — falling back to Hydrogram without colours", exc)
        await client.send_message(chat_id, text, reply_markup=to_hydrogram_markup(markup))


async def copy_ui(
    client: Client,
    chat_id: int,
    from_chat_id: int,
    message_id: int,
    caption: str,
    markup: Optional[Dict[str, Any]] = None,
) -> None:
    """Copies a stored banner (photo/video/gif) with a caption and keyboard."""
    try:
        await botapi.copy_message(client.nohara_token, chat_id, from_chat_id, message_id, caption, markup)
    except Exception as exc:
        log.warning("Bot API copy failed (%s) — falling back to Hydrogram without colours", exc)
        await client.copy_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            caption=caption,
            reply_markup=to_hydrogram_markup(markup) if markup else None,
        )


# ───────────────────────── Background tasks ─────────────────────────
_TASKS: set = set()


def _log_task_result(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        log.error("Background task failed", exc_info=task.exception())


def spawn(coro) -> None:
    """Runs a coroutine in the background and keeps a reference so it is not garbage collected."""
    task = asyncio.create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    task.add_done_callback(_log_task_result)


# ───────────────────────── Money helpers ─────────────────────────
def fmt_usd(cents: int) -> str:
    return f"${Decimal(int(cents)) / 100:,.2f}"


def parse_usd_to_cents(text: str) -> Optional[int]:
    """'9.99' -> 999. Returns None for invalid / negative / zero values."""
    try:
        value = Decimal(text.strip().replace("$", "").replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    if not value.is_finite() or value <= 0 or value > Decimal("1000000"):
        return None
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def cents_to_inr(cents: int, rate: float) -> Decimal:
    return (Decimal(int(cents)) / 100 * Decimal(str(rate))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ───────────────────────── Per-user UI context ─────────────────────────
class Ctx:
    """Language + font + translation helper for one user of one clone bot."""

    def __init__(self, clone: Dict[str, Any], lang: str):
        self.clone = clone
        self.lang = lang
        self.font = clone.get("font_style") or DEFAULT_FONT_STYLE
        self.overrides = clone.get("custom_translations") or {}

    def T(self, key: str, **kwargs: Any) -> str:
        text = get_text(self.lang, key, self.overrides)
        if kwargs:
            try:
                return text.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                return text
        return text

    def H(self, text: str) -> str:
        """Message-body font styling (HTML-safe)."""
        return style_html(text, self.font)

    @property
    def is_master(self) -> bool:
        return bool(self.clone.get("is_master"))

    @property
    def owner_id(self) -> int:
        return int(self.clone["owner_id"])


async def get_ctx(bot_id: str, user_id: int) -> Optional[Ctx]:
    clone = await db.get_clone(bot_id)
    if not clone:
        return None
    lang = await db.get_user_lang(bot_id, user_id, clone.get("default_language", "en"))
    return Ctx(clone, lang)


# ───────────────────────── Audit & notifications ─────────────────────────
async def send_audit(clone: Dict[str, Any], text: str) -> None:
    """Posts a METADATA-ONLY audit line to the clone's log channel and the master audit channel."""
    bot_id = str(clone["_id"])
    client = ACTIVE_CLONE_CLIENTS.get(bot_id)
    if client and clone.get("log_channel_id"):
        try:
            await client.send_message(clone["log_channel_id"], text)
        except Exception as exc:
            log.warning("Clone log channel post failed (%s): %s", bot_id, exc)

    master = MASTER.get("client")
    audit_id = clone.get("master_audit_channel_id") or MASTER_AUDIT_CHANNEL_ID
    if master and audit_id:
        try:
            await master.send_message(audit_id, text)
        except Exception as exc:
            log.warning("Master audit post failed: %s", exc)


async def notify_owner(clone: Dict[str, Any], text: str, **kwargs: Any) -> None:
    client = ACTIVE_CLONE_CLIENTS.get(str(clone["_id"]))
    if not client:
        return
    try:
        await client.send_message(int(clone["owner_id"]), text, **kwargs)
    except Exception as exc:
        log.warning("Could not notify owner of %s: %s", clone["_id"], exc)


def _audit_line(title: str, clone: Dict[str, Any], order: Dict[str, Any], extra: str = "") -> str:
    return (
        f"<b>[NOHARA AUDIT — {title}]</b>\n"
        f"• Clone Bot: <code>{html.escape(str(clone.get('bot_username', '')))}</code> (<code>{clone['_id']}</code>)\n"
        f"• Buyer ID: <code>{order['user_id']}</code>\n"
        f"• Product: <b>{html.escape(order['title'])}</b> (<code>{order['product_id']}</code>)\n"
        f"• Order: <code>{order['_id']}</code> | Method: <b>{order['method']}</b> | Amount: <b>{fmt_usd(order['price_cents'])}</b>"
        + (f"\n{extra}" if extra else "")
    )


# ───────────────────────── Delivery & refund ─────────────────────────
async def fulfill_paid_order(order: Dict[str, Any], stock_reserved: bool = False) -> bool:
    """
    Called ONCE per order, right after db.mark_paid() (or after the wallet debit).
      1. reserves stock (if not reserved yet)
      2. copies the file from the owner's private DB channel to the buyer
      3. if anything fails -> the price is credited back to the buyer's wallet (no money is lost)
    """
    bot_id = str(order["bot_id"])
    user_id = int(order["user_id"])
    clone = await db.get_clone(bot_id)
    client = ACTIVE_CLONE_CLIENTS.get(bot_id)
    ctx = await get_ctx(bot_id, user_id)
    product = await db.get_product(bot_id, order["product_id"])

    delivered = False
    if clone and client and ctx and product and clone.get("db_channel_id"):
        if not stock_reserved:
            stock_reserved = await db.reserve_stock(bot_id, order["product_id"])
        if stock_reserved:
            try:
                await client.send_message(user_id, ctx.T("order_delivered_msg"))
                await client.copy_message(
                    chat_id=user_id,
                    from_chat_id=int(clone["db_channel_id"]),
                    message_id=int(product["db_channel_message_id"]),
                )
                delivered = True
            except Exception:
                log.exception("Delivery failed for order %s", order["_id"])

    if delivered and clone:
        await db.set_order_fields(order["_id"], status="delivered")
        await send_audit(clone, _audit_line("ORDER DELIVERED", clone, order, f"• DB Msg Pointer: <code>#{product['db_channel_message_id']}</code>"))
        return True

    # ── failure path: give the stock back and refund the buyer into the wallet ──
    if stock_reserved:
        await db.release_stock(bot_id, order["product_id"])
    await db.credit(bot_id, user_id, int(order["price_cents"]))
    await db.set_order_fields(order["_id"], status="refunded")
    if client and ctx:
        try:
            await client.send_message(user_id, ctx.T("delivery_failed_refund"))
        except Exception:
            pass
    if clone:
        await notify_owner(
            clone,
            f"⚠️ <b>Delivery failed</b> for order <code>{order['_id']}</code> "
            f"(product <code>{order['product_id']}</code>). The buyer was refunded to their wallet.\n"
            f"Check that the bot is ADMIN in your DB channel and the product still exists.",
        )
        await send_audit(clone, _audit_line("DELIVERY FAILED — REFUNDED", clone, order))
    return False
