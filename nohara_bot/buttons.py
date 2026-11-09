"""
NOHARA STORE BOT — Inline keyboards (nohara_bot/buttons.py)

Keyboards are plain Bot-API dictionaries:  {"inline_keyboard": [[{...button...}, ...], ...]}

NATIVE COLOURS (no emoji needed): a button can carry  "style": "primary" | "success" | "danger"
  primary = blue   success = green   danger = red   default = Telegram's normal button colour
The messages are delivered through the official Bot API (see botapi.py / services/core.py send_ui).
Set BUTTON_COLORS=false in the environment to turn colours off.
"""

from typing import Any, Callable, Dict, List, Optional

from nohara_bot.config import BUTTON_COLORS
from nohara_bot.fonts import style_text
from nohara_bot.i18n import LANGUAGES

Button = Dict[str, Any]
Markup = Dict[str, Any]


def _btn(text: str, font: str, style: str = "success", callback: str = "", url: str = "") -> Button:
    button: Button = {"text": style_text(text, font)[:64] or "•"}
    if url:
        button["url"] = url
    else:
        button["callback_data"] = callback
    if BUTTON_COLORS and style in ("primary", "success", "danger"):
        button["style"] = style
    return button


def _markup(rows: List[List[Button]]) -> Markup:
    return {"inline_keyboard": rows}


# public names used by the admin panel
btn = _btn
markup = _markup


def _t(T: Callable[..., str], key: str, default: str) -> str:
    """Translation with an English default for keys that have no translation yet."""
    value = T(key)
    return default if value == key else value


TOPUP_AMOUNTS_CENTS = (100, 500, 1000, 2000, 5000, 10000)  # $1 $5 $10 $20 $50 $100


def main_menu(
    T: Callable[..., str],
    font: str,
    is_owner: bool = False,
    is_master: bool = False,
    links: Optional[Dict[str, str]] = None,
    vip_on: bool = True,
    ref_on: bool = True,
    coupon_on: bool = True,
) -> Markup:
    rows = [
        [_btn("🛍 " + T("btn_view_products"), font, "primary", "menu:products")],
        [_btn("👛 " + T("btn_wallet"), font, "success", "menu:wallet"), _btn("🌐 " + T("btn_change_lang"), font, "success", "menu:lang")],
    ]
    perks = []
    if vip_on:
        perks.append(_btn("💎 " + _t(T, "btn_vip", "VIP Membership"), font, "primary", "menu:vip"))
    if ref_on:
        perks.append(_btn("🎁 " + _t(T, "btn_refer", "Refer & Earn"), font, "primary", "menu:ref"))
    if perks:
        rows.append(perks)
    coupon = _btn("🎟 " + _t(T, "btn_coupon", "Redeem Coupon"), font, "success", "menu:coupon")
    topup = _btn("💰 " + _t(T, "btn_topup", "Top Up"), font, "success", "menu:topup")
    if is_master:
        if coupon_on:
            rows.append([coupon])
        rows.append([_btn("🧬 " + T("btn_clone_bot"), font, "primary", "menu:clone"), topup])
    else:
        rows.append([coupon, topup] if coupon_on else [topup])
    links = links or {}
    link_row = []
    if links.get("updates_url"):
        link_row.append(_btn("📢 " + _t(T, "btn_updates", "Updates Channel"), font, "primary", url=links["updates_url"]))
    if links.get("support_url"):
        link_row.append(_btn("💬 " + _t(T, "btn_support", "Support"), font, "primary", url=links["support_url"]))
    if link_row:
        rows.append(link_row)
    if is_owner:
        rows.append([_btn("⚙️ " + T("btn_open_dashboard"), font, "success", "menu:dash")])
    return _markup(rows)


def vip_keyboard(T: Callable[..., str], font: str, plans: List[Any], price_fmt: Callable[[int], str]) -> Markup:
    """One button per VIP plan: (months, price_cents). Two per row."""
    rows: List[List[Button]] = []
    row: List[Button] = []
    for months, cents in plans:
        label = f"💎 {months} Month{'' if months == 1 else 's'} — {price_fmt(cents)}"
        row.append(_btn(label, font, "primary", f"vip:buy:{months}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("💰 " + _t(T, "btn_topup", "Top Up"), font, "success", "menu:topup"), _btn("🏠 " + T("btn_home"), font, "success", "menu:home")])
    return _markup(rows)


def clone_keyboard(T: Callable[..., str], font: str, create_url: str) -> Markup:
    """'Create Your Own Bot' screen: one URL button that opens Telegram's ready-made create-bot screen (Managed Bots)."""
    rows = []
    if create_url:
        rows.append([_btn("🚀 " + _t(T, "btn_create_bot", "Create My Bot"), font, "primary", url=create_url)])
    rows.append([_btn("🏠 " + T("btn_home"), font, "success", "menu:home")])
    return _markup(rows)


def vip_locked_keyboard(T: Callable[..., str], font: str, back_cb: str = "menu:products") -> Markup:
    return _markup([
        [_btn("💎 " + _t(T, "btn_vip_get", "Get VIP Access"), font, "primary", "menu:vip")],
        [_btn("◀️ " + T("btn_back"), font, "success", back_cb)],
    ])


def referral_keyboard(T: Callable[..., str], font: str, share_url: str) -> Markup:
    return _markup([
        [_btn("📤 " + _t(T, "btn_share", "Share My Link"), font, "primary", url=share_url)],
        [_btn("🏠 " + T("btn_home"), font, "success", "menu:home")],
    ])


def after_coupon_keyboard(T: Callable[..., str], font: str) -> Markup:
    return _markup([
        [_btn("🛍 " + T("btn_view_products"), font, "primary", "menu:products")],
        [_btn("🏠 " + T("btn_home"), font, "success", "menu:home")],
    ])


def products_keyboard(products: List[Dict], T: Callable[..., str], font: str, price_fmt: Callable[[int], str], back_cb: str = "menu:home") -> Markup:
    rows = []
    for p in products:
        out = p["stock"] == 0
        label = ("💎 " if p.get("vip_only") else "") + f"{p['title']} — {price_fmt(p['price_cents'])}" + (" ✖" if out else "")
        rows.append([_btn(label, font, "danger" if out else "primary", f"prod:{p['_id']}")])
    label = ("🏠 " + T("btn_home")) if back_cb == "menu:home" else ("◀️ " + T("btn_back"))
    rows.append([_btn(label, font, "success", back_cb)])
    return _markup(rows)


def folders_keyboard(shown: List, other_count: int, T: Callable[..., str], font: str) -> Markup:
    rows = [[_btn(f"🗂 {f['name']}  ({n})", font, "primary", f"fold:{f['id']}")] for f, n in shown]
    if other_count:
        rows.append([_btn(f"📦 {T('other_products')}  ({other_count})", font, "success", "fold:_")])
    rows.append([_btn("🏠 " + T("btn_home"), font, "success", "menu:home")])
    return _markup(rows)


def product_keyboard(
    product_id: str,
    T: Callable[..., str],
    font: str,
    in_stock: bool,
    oxapay_enabled: bool,
    fampay_enabled: bool,
    back_cb: str = "menu:products",
) -> Markup:
    if not in_stock:
        return _markup([
            [_btn("🚫 " + T("btn_out_of_stock"), font, "danger", "noop")],
            [_btn("🛍 " + T("btn_view_products"), font, "primary", "menu:products")],
        ])
    rows = [[_btn("👛 " + T("btn_pay_wallet"), font, "success", f"pay:wallet:{product_id}")]]
    gateway_row = []
    if oxapay_enabled:
        gateway_row.append(_btn("🪙 " + T("btn_pay_oxapay"), font, "primary", f"pay:oxapay:{product_id}"))
    if fampay_enabled:
        gateway_row.append(_btn("🇮🇳 " + T("btn_pay_fampay"), font, "primary", f"pay:fampay:{product_id}"))
    if gateway_row:
        rows.append(gateway_row)
    rows.append([
        _btn("◀️ " + T("btn_back"), font, "success", back_cb),
        _btn("✖️ " + T("btn_cancel_order"), font, "danger", "menu:home"),
    ])
    return _markup(rows)


def payment_link_keyboard(url: str, order_id: str, T: Callable[..., str], font: str) -> Markup:
    return _markup([
        [_btn("💳 " + T("btn_open_payment"), font, "success", url=url)],
        [_btn("✖️ " + T("btn_cancel_order"), font, "danger", f"cancel:{order_id}")],
    ])


def cancel_keyboard(order_id: str, T: Callable[..., str], font: str) -> Markup:
    return _markup([[_btn("✖️ " + T("btn_cancel_order"), font, "danger", f"cancel:{order_id}")]])


def back_keyboard(T: Callable[..., str], font: str) -> Markup:
    return _markup([[_btn("🏠 " + T("btn_home"), font, "success", "menu:home")]])


def wallet_keyboard(T: Callable[..., str], font: str) -> Markup:
    return _markup([
        [_btn("💰 " + _t(T, "btn_topup", "Top Up"), font, "success", "menu:topup")],
        [_btn("🏠 " + T("btn_home"), font, "success", "menu:home")],
    ])


def topup_methods_keyboard(T: Callable[..., str], font: str, oxapay_on: bool, fampay_on: bool) -> Markup:
    row = []
    if oxapay_on:
        row.append(_btn("🪙 " + T("btn_pay_oxapay"), font, "primary", "top:m:oxapay"))
    if fampay_on:
        row.append(_btn("🇮🇳 " + T("btn_pay_fampay"), font, "primary", "top:m:fampay"))
    rows = [row] if row else []
    rows.append([_btn("🏠 " + T("btn_home"), font, "success", "menu:home")])
    return _markup(rows)


def topup_amount_keyboard(method: str, T: Callable[..., str], font: str) -> Markup:
    rows: List[List[Button]] = []
    row: List[Button] = []
    for cents in TOPUP_AMOUNTS_CENTS:
        row.append(_btn(f"${cents // 100}", font, "primary", f"top:a:{method}:{cents}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("✍️ " + _t(T, "btn_custom_amount", "Custom amount"), font, "success", f"top:c:{method}")])
    rows.append([_btn("◀️ " + T("btn_back"), font, "success", "menu:topup")])
    return _markup(rows)


def topup_cancel_keyboard(T: Callable[..., str], font: str) -> Markup:
    return _markup([[_btn("✖️ " + T("btn_cancel_order"), font, "danger", "menu:topup")]])


def language_keyboard(T: Callable[..., str], font: str) -> Markup:
    rows: List[List[Button]] = []
    row: List[Button] = []
    for code, native in LANGUAGES:
        # Native names are written as-is (no font styling) so every script stays readable.
        row.append(_btn(native, "default_html", "success", f"lang:{code}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("🏠 " + T("btn_home"), font, "success", "menu:home")])
    return _markup(rows)


def fsub_keyboard(channels: List[Dict], T: Callable[..., str], font: str) -> Markup:
    rows: List[List[Button]] = []
    for ch in channels:
        if ch.get("invite_link"):
            rows.append([_btn(f"📢 {ch.get('title') or 'Channel'}", "default_html", "primary", url=ch["invite_link"])])
    rows.append([_btn("✅ " + T("btn_joined"), font, "success", "fsub:check")])
    return _markup(rows)


def upi_review_keyboard(order_id: str) -> Markup:
    """Shown to the STORE OWNER only (always English)."""
    return _markup([[
        _btn("Approve", "default_html", "success", f"upi:ok:{order_id}"),
        _btn("Reject", "default_html", "danger", f"upi:no:{order_id}"),
    ]])
