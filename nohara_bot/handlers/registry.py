"""
NOHARA STORE BOT — Attaches every store-bot handler to one clone client (call BEFORE client.start()).
(nohara_bot/handlers/registry.py)
"""

from hydrogram import Client, filters
from hydrogram.handlers import CallbackQueryHandler, MessageHandler

from nohara_bot.handlers.owner import (
    cmd_addfolder,
    cmd_delfolder,
    cmd_folders,
    cmd_setfolder,
    cmd_addfsub,
    cmd_addmoney,
    cmd_addproduct,
    cmd_dashboard,
    cmd_delbanner,
    cmd_delproduct,
    cmd_fsublist,
    cmd_products,
    cmd_removefsub,
    cmd_removemoney,
    cmd_setbanner,
    cmd_setdbchannel,
    cmd_setfont,
    cmd_setgateway,
    cmd_setlang,
    cmd_setlogchannel,
    cmd_setrate,
    cmd_setwelcome,
)
from nohara_bot.handlers.store import (
    store_callback,
    store_changelang,
    store_help,
    store_start,
    store_user_message,
)


def register_store_handlers(client: Client, bot_id: str) -> None:
    """Attaches every store-bot handler to ONE clone client (call BEFORE client.start())."""
    client.nohara_bot_id = str(bot_id)
    private = filters.private

    def cmd(names, func) -> None:
        client.add_handler(MessageHandler(func, private & filters.command(names)))

    cmd("start", store_start)
    cmd("changelang", store_changelang)
    cmd("help", store_help)
    cmd(["admin", "dashboard"], cmd_dashboard)
    cmd("setdbchannel", cmd_setdbchannel)
    cmd("setlogchannel", cmd_setlogchannel)
    cmd("setgateway", cmd_setgateway)
    cmd("setrate", cmd_setrate)
    cmd("setfont", cmd_setfont)
    cmd("setlang", cmd_setlang)
    cmd("setwelcome", cmd_setwelcome)
    cmd(["setbanner", "setimage", "setstartimage", "setcatalogimage", "setwalletimage", "setstockimage", "setproductimage", "setoxapayimage", "setfampayimage"], cmd_setbanner)
    cmd(["delbanner", "delimage", "delstartimage", "delcatalogimage", "delwalletimage", "delstockimage", "delproductimage", "deloxapayimage", "delfampayimage"], cmd_delbanner)
    cmd("addproduct", cmd_addproduct)
    cmd("products", cmd_products)
    cmd("delproduct", cmd_delproduct)
    cmd("addfolder", cmd_addfolder)
    cmd("folders", cmd_folders)
    cmd("delfolder", cmd_delfolder)
    cmd("setfolder", cmd_setfolder)
    cmd("addfsub", cmd_addfsub)
    cmd("removefsub", cmd_removefsub)
    cmd("fsublist", cmd_fsublist)
    cmd("addmoney", cmd_addmoney)
    cmd("removemoney", cmd_removemoney)

    client.add_handler(CallbackQueryHandler(store_callback))
    client.add_handler(
        MessageHandler(
            store_user_message,
            private & (filters.text | filters.photo | filters.document) & ~filters.regex(r"^/"),
        )
    )
