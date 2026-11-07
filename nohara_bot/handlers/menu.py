"""
NOHARA STORE BOT — Automatic command menu (nohara_bot/handlers/menu.py)

Fills the "/" command menu of a bot by itself, so nobody has to use @BotFather:
  * EVERY user sees the user commands (default scope; Hindi descriptions for Hindi users)
  * the bot OWNER additionally sees all admin commands (chat scope, only in the owner's own chat)
  * the MASTER bot also shows /clone /myclones /deleteclone to everyone

It runs automatically when a clone is created, every time the platform boots (so old clones are
fixed too) and when the owner sends /start. A failure here is only logged — it never stops the bot.
"""

import logging
from typing import Iterable, List, Tuple

from nohara_bot import botapi

log = logging.getLogger("nohara.handlers.menu")

Cmd = Tuple[str, str]

USER_COMMANDS: List[Cmd] = [
    ("start", "Open the store menu"),
    ("changelang", "Change language"),
    ("help", "Help"),
]
USER_COMMANDS_HI: List[Cmd] = [
    ("start", "स्टोर मेन्यू खोलें"),
    ("changelang", "भाषा बदलें"),
    ("help", "मदद"),
]
PLATFORM_COMMANDS: List[Cmd] = [
    ("clone", "Create your own store bot"),
    ("myclones", "List your bots"),
    ("deleteclone", "Remove one of your bots"),
]
PLATFORM_COMMANDS_HI: List[Cmd] = [
    ("clone", "अपना स्टोर बॉट बनाएँ"),
    ("myclones", "मेरे बॉट देखें"),
    ("deleteclone", "अपना बॉट हटाएँ"),
]
OWNER_COMMANDS: List[Cmd] = [
    ("admin", "Open the admin panel"),
    ("setdbchannel", "Connect your private files channel"),
    ("setlogchannel", "Connect your order log channel"),
    ("addproduct", "Add a product (send a file with caption)"),
    ("products", "List your products with IDs"),
    ("delproduct", "Delete a product by ID"),
    ("addfolder", "Create a product folder"),
    ("folders", "List your folders"),
    ("delfolder", "Delete a folder"),
    ("setfolder", "Move a product into a folder"),
    ("setgateway", "Set up OxaPay / UPI payments"),
    ("setrate", "Set the USD to INR rate"),
    ("setfont", "Change the font style"),
    ("setlang", "Set the default language"),
    ("setwelcome", "Change the welcome message"),
    ("setstartimage", "Set the start / welcome image"),
    ("setcatalogimage", "Set the product list image"),
    ("setwalletimage", "Set the wallet image"),
    ("setstockimage", "Set the out-of-stock image"),
    ("setproductimage", "Set the product page image"),
    ("setoxapayimage", "Set the crypto payment image"),
    ("setfampayimage", "Set the UPI payment image"),
    ("delbanner", "Remove a banner image by name"),
    ("addfsub", "Add a force-subscribe channel"),
    ("removefsub", "Remove a force-subscribe channel"),
    ("fsublist", "List force-subscribe channels"),
    ("addmoney", "Add wallet balance to a user"),
    ("removemoney", "Remove wallet balance from a user"),
    ("broadcast", "Send a message to all your users (free)"),
    ("setvip", "Set VIP price, days and discount"),
    ("setreferral", "Set the referral bonus"),
    ("settopupbonus", "Set the top-up bonus tiers"),
    ("addcoupon", "Create a discount coupon"),
    ("delcoupon", "Delete a coupon"),
    ("coupons", "List your coupons"),
    ("vipproduct", "Make a product VIP-only"),
]

_OWNER_MENU_DONE: set = set()


def _payload(commands: Iterable[Cmd]) -> list:
    return [{"command": name, "description": text} for name, text in commands]


async def _set(token: str, commands: Iterable[Cmd], scope: dict, language_code: str = "") -> None:
    body = {"commands": _payload(commands), "scope": scope}
    if language_code:
        body["language_code"] = language_code
    await botapi.call(token, "setMyCommands", body)


async def set_owner_menu(client, owner_id: int, is_master: bool = False) -> bool:
    """User + admin commands, shown only inside the owner's own chat with the bot."""
    commands = USER_COMMANDS + OWNER_COMMANDS + (PLATFORM_COMMANDS if is_master else [])
    try:
        await _set(client.nohara_token, commands, {"type": "chat", "chat_id": int(owner_id)})
        return True
    except Exception as exc:
        # Telegram only accepts a personal menu after that person has opened the bot.
        # ensure_owner_menu() retries it when the owner sends /start.
        log.info("Owner menu for %s not set yet (%s)", owner_id, exc)
        return False


async def apply_command_menu(client, owner_ids: Iterable[int], is_master: bool = False) -> None:
    """Sets the menu for everybody + the owner(s). Never raises."""
    user = USER_COMMANDS + (PLATFORM_COMMANDS if is_master else [])
    user_hi = USER_COMMANDS_HI + (PLATFORM_COMMANDS_HI if is_master else [])
    try:
        await _set(client.nohara_token, user, {"type": "default"})
        await _set(client.nohara_token, user_hi, {"type": "default"}, "hi")
    except Exception as exc:
        log.warning("Could not set the public command menu: %s", exc)
    for owner_id in owner_ids:
        if await set_owner_menu(client, owner_id, is_master):
            _OWNER_MENU_DONE.add((client.nohara_token.split(":")[0], int(owner_id)))


async def ensure_owner_menu(client, bot_id: str, user_id: int, is_master: bool = False) -> None:
    """Called when an owner sends /start: sets their personal menu if it is not set yet."""
    key = (str(bot_id), int(user_id))
    if key in _OWNER_MENU_DONE:
        return
    if await set_owner_menu(client, user_id, is_master):
        _OWNER_MENU_DONE.add(key)
     
