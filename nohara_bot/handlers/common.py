"""
NOHARA STORE BOT — Shared helpers for all handlers: screens (show), banners, force-subscribe, owner guard.
(nohara_bot/handlers/common.py)
"""

import html
import logging
from hydrogram import Client
from hydrogram.enums import ChatMemberStatus
from hydrogram.errors import UserNotParticipant
from hydrogram.types import Message
from typing import Any, Dict, List, Optional

from nohara_bot.buttons import fsub_keyboard, main_menu
from nohara_bot.config import ADMIN_IDS
from nohara_bot.database import db
from nohara_bot.services.core import (
    Ctx,
    copy_ui,
    fmt_date,
    fmt_usd,
    get_ctx,
    get_user_perks,
    referral_cfg,
    send_ui,
    topup_tiers,
    vip_cfg,
)


log = logging.getLogger("nohara.handlers.common")


BANNER_FIELDS = {
    "start": "start_photo_message_id",
    "catalog": "catalog_photo_message_id",
    "wallet": "wallet_photo_message_id",
    "stock": "out_of_stock_photo_message_id",
    "product": "product_photo_message_id",
    "oxapay": "oxapay_photo_message_id",
    "fampay": "fampay_photo_message_id",
}
# If a banner is not set, show this one instead.
BANNER_FALLBACKS = {
    "product": "catalog",
    "oxapay": "wallet",
    "fampay": "wallet",
}
BANNER_ALIASES = {
    "welcome": "start", "home": "start",
    "products": "catalog", "shop": "catalog", "store": "catalog",
    "outofstock": "stock", "out_of_stock": "stock", "soldout": "stock",
    "item": "product", "crypto": "oxapay", "upi": "fampay",
}


def _banner_key(raw: str) -> Optional[str]:
    key = (raw or "").strip().lower()
    key = BANNER_ALIASES.get(key, key)
    return key if key in BANNER_FIELDS else None


# ═════════════════════════════════════════════════════════════════════
#  Small helpers
# ═════════════════════════════════════════════════════════════════════
def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _is_owner(ctx: Ctx, user_id: int) -> bool:
    return user_id == ctx.owner_id or _is_admin(user_id)


def _args(message: Message) -> List[str]:
    return message.command[1:] if message.command else []


def _rest_of_text(message: Message) -> str:
    raw = message.text or message.caption or ""
    parts = raw.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def show(
    client: Client,
    ctx: Ctx,
    chat_id: int,
    text: str,
    kb,
    banner: Optional[str] = None,
    replace: Optional[Message] = None,
) -> None:
    """Sends a fresh screen (optionally with the owner's banner photo) and removes the old one."""
    if replace is not None:
        try:
            await replace.delete()
        except Exception:
            pass
    branding = ctx.clone.get("ui_branding") or {}
    field = BANNER_FIELDS.get(banner or "", "")
    banner_msg_id = int(branding.get(field) or 0) if field else 0
    if not banner_msg_id and banner in BANNER_FALLBACKS:
        fb_field = BANNER_FIELDS.get(BANNER_FALLBACKS[banner], "")
        banner_msg_id = int(branding.get(fb_field) or 0) if fb_field else 0
    if banner_msg_id and ctx.clone.get("db_channel_id") and len(text) <= 1000:
        try:
            await copy_ui(client, chat_id, int(ctx.clone["db_channel_id"]), banner_msg_id, text, kb)
            return
        except Exception as exc:
            log.warning("Banner copy failed, sending text instead: %s", exc)
    await send_ui(client, chat_id, text, kb)


DIVIDER = "━" * 18


def welcome_text(ctx: Ctx, name: str = "", status_lines: Optional[List[str]] = None) -> str:
    subtitle = ctx.clone.get("welcome_msg") or (
        ctx.T("welcome_tagline") if ctx.lang in ("en", "hi") else ctx.T("welcome_subtitle")
    )
    lines = [f"✨ {ctx.H(ctx.T('welcome_title'))} ✨", DIVIDER]
    if name:
        lines.append(f"👋 <b>{ctx.T('greeting', name=html.escape(name))}</b>")
        lines.append("")
    lines += [f"<i>{html.escape(subtitle)}</i>", "", html.escape(ctx.T("trust_line")), DIVIDER]
    if status_lines:
        lines += status_lines + [DIVIDER]
    return "\n".join(lines)


async def _status_lines(ctx: Ctx, user_id: int) -> List[str]:
    """The small 'your account' block on the welcome screen: balance, VIP status, bonus and referral hints."""
    clone = ctx.clone
    vip, ref, tiers = vip_cfg(clone), referral_cfg(clone), topup_tiers(clone)
    try:
        balance = await db.get_balance(str(clone["_id"]), user_id)
        perks = await get_user_perks(clone, user_id)
    except Exception:
        return []
    lines = [f"👛 {html.escape(ctx.T('balance_label'))}: <b>{fmt_usd(balance)}</b>"]
    if perks["vip_active"]:
        lines.append(f"💎 <b>{html.escape(ctx.TX('vip_member', 'VIP member'))}</b> · {html.escape(ctx.TX('vip_until', 'until'))} {fmt_date(perks['vip_until'])}")
    elif vip["enabled"]:
        lines.append("⭐ " + html.escape(ctx.TX("vip_join_line", "Join VIP and save {pct}% on every product", pct=vip["discount_pct"])))
    if tiers:
        lines.append("🎁 " + html.escape(ctx.TX("bonus_line", "Top up and get up to {pct}% bonus", pct=max(int(t["pct"]) for t in tiers))))
    if ref["enabled"] and int(ref["bonus_cents"]) > 0:
        lines.append("🤝 " + html.escape(ctx.TX("ref_line", "Invite friends and earn {amount} each", amount=fmt_usd(int(ref["bonus_cents"])))))
    return lines


async def show_home(
    client: Client, ctx: Ctx, chat_id: int, user_id: int, replace: Optional[Message] = None, name: str = ""
) -> None:
    kb = main_menu(
        ctx.T, ctx.font, _is_owner(ctx, user_id), ctx.is_master, ctx.clone.get("links") or {},
        vip_on=bool(vip_cfg(ctx.clone)["enabled"]), ref_on=bool(referral_cfg(ctx.clone)["enabled"]),
    )
    await show(client, ctx, chat_id, welcome_text(ctx, name, await _status_lines(ctx, user_id)), kb, "start", replace)


# ═════════════════════════════════════════════════════════════════════
#  Force-subscribe
# ═════════════════════════════════════════════════════════════════════
async def missing_fsub(client: Client, ctx: Ctx, user_id: int) -> List[Dict[str, Any]]:
    if _is_owner(ctx, user_id):
        return []
    missing = []
    for ch in ctx.clone.get("fsub_channels", []):
        try:
            member = await client.get_chat_member(ch["channel_id"], user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.BANNED):
                missing.append(ch)
        except UserNotParticipant:
            missing.append(ch)
        except Exception as exc:
            # Usually "bot is not admin in that channel" — do NOT lock every user out because of it.
            log.warning("FSub check skipped for channel %s: %s", ch.get("channel_id"), exc)
    return missing


async def ensure_fsub(client: Client, ctx: Ctx, chat_id: int, user_id: int, replace: Optional[Message] = None) -> bool:
    missing = await missing_fsub(client, ctx, user_id)
    if not missing:
        return True
    await show(client, ctx, chat_id, ctx.T("fsub_required_msg"), fsub_keyboard(missing, ctx.T, ctx.font), replace=replace)
    return False




def owner_only(func):
    async def wrapper(client: Client, message: Message) -> None:
        if not message.from_user:
            return
        ctx = await get_ctx(client.nohara_bot_id, message.from_user.id)
        if not ctx:
            return
        if not _is_owner(ctx, message.from_user.id):
            await message.reply_text(ctx.T("owner_only"))
            return
        try:
            await func(client, message, ctx)
        except Exception:
            log.exception("Owner command failed: %s", func.__name__)
            await message.reply_text("⚠️ Something went wrong. Please check the server logs.")

    wrapper.__name__ = func.__name__
    return wrapper
    
