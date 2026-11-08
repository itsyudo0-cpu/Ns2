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
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

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

    def TX(self, key: str, default: str, **kwargs: Any) -> str:
        """Like T(), but uses `default` (English) when the key has no translation."""
        text = get_text(self.lang, key, self.overrides)
        if text == key:
            text = default
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


# ───────────────────────── Premium features: VIP · coupons · referral · top-up bonus ─────────────────────────
# Every setting has a default, so old stores (created before these features) work without any migration.
VIP_DEFAULTS = {"enabled": True, "price_cents": 500, "days": 30, "discount_pct": 15}
REFERRAL_DEFAULTS = {"enabled": True, "bonus_cents": 50, "min_paid_cents": 100}
TOPUP_BONUS_DEFAULT = [{"min_cents": 1000, "pct": 5}, {"min_cents": 2500, "pct": 10}, {"min_cents": 5000, "pct": 15}]


def vip_cfg(clone: Dict[str, Any]) -> Dict[str, Any]:
    return {**VIP_DEFAULTS, **(clone.get("vip") or {})}


def referral_cfg(clone: Dict[str, Any]) -> Dict[str, Any]:
    return {**REFERRAL_DEFAULTS, **(clone.get("referral") or {})}


def topup_tiers(clone: Dict[str, Any]) -> List[Dict[str, int]]:
    """[] = bonus switched off. Missing setting = the default tiers."""
    tiers = clone.get("topup_bonus")
    if tiers is None:
        tiers = TOPUP_BONUS_DEFAULT
    return sorted(tiers, key=lambda t: int(t["min_cents"]))


def topup_bonus_cents(clone: Dict[str, Any], cents: int) -> int:
    pct = 0
    for tier in topup_tiers(clone):
        if int(cents) >= int(tier["min_cents"]):
            pct = int(tier["pct"])
    return int(cents) * pct // 100


def fmt_date(ts: int) -> str:
    return time.strftime("%d %b %Y", time.gmtime(int(ts)))


def coupon_is_valid(coupon: Optional[Dict[str, Any]], user_id: int) -> bool:
    if not coupon:
        return False
    if coupon.get("expires_at") and int(coupon["expires_at"]) < int(time.time()):
        return False
    if coupon.get("max_uses") and int(coupon.get("used", 0)) >= int(coupon["max_uses"]):
        return False
    if int(user_id) in (coupon.get("used_by") or []):
        return False
    return True


async def get_user_perks(clone: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    user = await db.get_user(str(clone["_id"]), user_id) or {}
    vip_until = int(user.get("vip_until") or 0)
    return {
        "user": user,
        "vip_until": vip_until,
        "vip_active": bool(is_premium(clone) and vip_cfg(clone)["enabled"] and vip_until > int(time.time())),
    }


async def price_for(clone: Dict[str, Any], perks: Dict[str, Any], user_id: int, product: Dict[str, Any]):
    """Best single discount wins (VIP or coupon — they do not stack).
    Returns (final_cents, {"pct", "source": 'vip'|'coupon'|'', "code", "list_cents"})."""
    list_cents = int(product["price_cents"])
    pct, source, code = 0, "", ""
    if perks.get("vip_active"):
        pct, source = int(vip_cfg(clone)["discount_pct"]), "vip"
    active = (perks.get("user") or {}).get("coupon")
    if active and is_premium(clone):
        coupon = await db.get_coupon(str(clone["_id"]), active.get("code", ""))
        if coupon_is_valid(coupon, user_id) and int(coupon["pct"]) > pct:
            pct, source, code = int(coupon["pct"]), "coupon", coupon["code"]
    final = list_cents - list_cents * pct // 100
    return max(final, 1), {"pct": pct, "source": source, "code": code, "list_cents": list_cents}


async def reward_referrer(bot_id: str, user_id: int, paid_cents: int) -> None:
    """The first real payment of an invited friend pays the referrer a wallet bonus (once per friend)."""
    try:
        clone = await db.get_clone(bot_id)
        if not clone:
            return
        cfg = referral_cfg(clone)
        if not is_premium(clone) or not cfg["enabled"] or int(cfg["bonus_cents"]) <= 0 or int(paid_cents) < int(cfg["min_paid_cents"]):
            return
        doc = await db.claim_referral_reward(bot_id, user_id)
        if not doc:
            return
        referrer_id, bonus = int(doc["referred_by"]), int(cfg["bonus_cents"])
        await db.credit(bot_id, referrer_id, bonus)
        await db.add_referral_stats(bot_id, referrer_id, bonus)
        client = ACTIVE_CLONE_CLIENTS.get(str(bot_id))
        if client:
            try:
                await client.send_message(
                    referrer_id,
                    f"🎁 <b>Referral bonus!</b>\n{fmt_usd(bonus)} was added to your wallet because a friend you invited just made a payment.",
                )
            except Exception:
                pass
    except Exception:
        log.exception("Referral reward failed")


# ───────────────────────── Premium plan (for clone owners) ─────────────────────────
# VIP membership, Referral and Coupons are PREMIUM features of a clone: the clone owner buys a Premium plan
# (1–12 months, price per duration set by the MASTER owner) with the wallet he has in the MASTER bot.
PREMIUM_FEATURES = "VIP Membership · Refer & Earn · Coupons"


def is_premium(clone: Optional[Dict[str, Any]]) -> bool:
    if not clone:
        return False
    return bool(clone.get("is_master")) or int(clone.get("premium_until") or 0) > int(time.time())


def month_label(months: int) -> str:
    return f"{months} Month" + ("" if months == 1 else "s")


def plans_from(raw: Any) -> List[Any]:
    """{"1": 500, "3": 1200} -> [(1, 500), (3, 1200)]  (only months 1..12 with a price above 0)."""
    plans = []
    for key, cents in (raw or {}).items():
        try:
            months, price = int(key), int(cents or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= months <= 12 and price > 0:
            plans.append((months, price))
    return sorted(plans)


def vip_plans(clone: Dict[str, Any]) -> List[Any]:
    """The VIP plans a buyer can choose from. No plan configured yet -> one default 1-month plan."""
    return plans_from(clone.get("vip_plans")) or [(1, int(vip_cfg(clone)["price_cents"]))]


def product_link(clone: Dict[str, Any], product_id: str) -> str:
    """Direct link to ONE product's buy page (no folders, no menu). '' when the bot username is unknown."""
    username = str(clone.get("bot_username") or "").lstrip("@")
    return f"https://t.me/{username}?start=p_{product_id}" if username else ""


async def get_master_clone() -> Optional[Dict[str, Any]]:
    return await db.get_master_clone()


async def premium_plans() -> List[Any]:
    master = await get_master_clone()
    return plans_from((master or {}).get("premium_plans"))


async def purchase_premium(clone: Dict[str, Any], user_id: int, months: int) -> Dict[str, Any]:
    """Pays with the buyer's wallet in the MASTER bot. Returns {"status": ok|no_plan|no_balance|error, ...}."""
    master = await get_master_clone()
    price = dict(plans_from((master or {}).get("premium_plans"))).get(int(months))
    if not master or not price:
        return {"status": "no_plan"}
    if not await db.debit(str(master["_id"]), user_id, price):
        return {"status": "no_balance", "price": price, "balance": await db.get_balance(str(master["_id"]), user_id)}
    until = await db.extend_premium(str(clone["_id"]), int(months) * 30)
    text = (
        f"💎 <b>[PREMIUM PURCHASED]</b>\n• Bot: <code>{html.escape(str(clone.get('bot_username', '')))}</code> (<code>{clone['_id']}</code>)\n"
        f"• Buyer: <code>{user_id}</code> | Plan: <b>{month_label(months)}</b> | Paid: <b>{fmt_usd(price)}</b> | Until: {fmt_date(until)}"
    )
    master_client = MASTER.get("client")
    if master_client and master.get("owner_id"):
        try:
            await master_client.send_message(int(master["owner_id"]), text)
        except Exception:
            log.warning("Could not notify the master owner about a premium purchase")
    return {"status": "ok", "price": price, "until": until}


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


# ───────────────────────── Wallet top-up ─────────────────────────
async def fulfill_topup(order: Dict[str, Any]) -> bool:
    """Called ONCE per paid top-up order (after db.mark_paid): credits the buyer's wallet."""
    bot_id = str(order["bot_id"])
    user_id = int(order["user_id"])
    cents = int(order["price_cents"])
    clone = await db.get_clone(bot_id)
    client = ACTIVE_CLONE_CLIENTS.get(bot_id)
    ctx = await get_ctx(bot_id, user_id)

    bonus = topup_bonus_cents(clone, cents) if clone else 0
    balance = await db.credit(bot_id, user_id, cents + bonus)
    await db.set_order_fields(order["_id"], status="delivered", bonus_cents=bonus)
    if client:
        try:
            bonus_line = f"\n🎁 Bonus: <b>+{fmt_usd(bonus)}</b>" if bonus else ""
            await client.send_message(
                user_id,
                f"✅ <b>Top-up successful</b>\n💰 {fmt_usd(cents)} added to your wallet.{bonus_line}\n👛 Balance: <b>{fmt_usd(balance)}</b>",
            )
        except Exception:
            log.warning("Could not notify user %s about the top-up", user_id)
    if clone:
        await send_audit(
            clone,
            f"<b>[NOHARA AUDIT — WALLET TOP-UP]</b>\n"
            f"• Clone Bot: <code>{html.escape(str(clone.get('bot_username', '')))}</code> (<code>{clone['_id']}</code>)\n"
            f"• User: <code>{user_id}</code> | Amount: <b>{fmt_usd(cents)}</b> (+bonus {fmt_usd(bonus)}) | Method: <b>{order['method']}</b> | Order: <code>{order['_id']}</code>",
        )
    await reward_referrer(bot_id, user_id, cents)
    return True


# ───────────────────────── Delivery & refund ─────────────────────────
async def fulfill_paid_order(order: Dict[str, Any], stock_reserved: bool = False) -> bool:
    """
    Called ONCE per order, right after db.mark_paid() (or after the wallet debit).
      1. reserves stock (if not reserved yet)
      2. copies the file from the owner's private DB channel to the buyer
      3. if anything fails -> the price is credited back to the buyer's wallet (no money is lost)
    """
    if order.get("kind") == "topup":
        return await fulfill_topup(order)

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
        if order.get("coupon"):
            await db.consume_coupon(bot_id, order["coupon"], user_id)
        await reward_referrer(bot_id, user_id, int(order["price_cents"]))
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
