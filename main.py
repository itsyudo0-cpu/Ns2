"""
NOHARA STORE BOT — Production Entrypoint (main.py)

Runs, in ONE process (works on Render Free Web Service):
  1. aiohttp webhook + health server  (started FIRST so Render sees the open port quickly)
  2. Hydrogram MASTER bot
  3. every saved CLONE bot (a broken clone is skipped, it never stops the platform)
"""

import asyncio
import logging

from aiohttp import web
from hydrogram import idle

# config is imported first: it stops the program with a clear message if an env var is missing.
from nohara_bot.config import ADMIN_IDS, MASTER_BOT_TOKEN, PORT
from nohara_bot.botapi import close_session
from nohara_bot.database import db
from nohara_bot.handlers.menu import apply_command_menu
from nohara_bot.handlers.registry import register_store_handlers
from nohara_bot.handlers.master import boot_clone, register_master_handlers, stop_clone_client
from nohara_bot.services.core import ACTIVE_CLONE_CLIENTS, MASTER, make_client
from nohara_bot.webhooks import DynamicWebhookServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("nohara.main")


async def boot_platform() -> None:
    await db.init_indexes()

    # 1) Web server first (health check + payment webhooks)
    webhook_server = DynamicWebhookServer()
    runner = web.AppRunner(webhook_server.app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("Web server listening on port %s", PORT)

    # 2) Master bot = the platform owner's OWN STORE + the clone commands
    master_id = MASTER_BOT_TOKEN.split(":")[0]
    master_bot = make_client("nohara_master_bot", MASTER_BOT_TOKEN)
    register_store_handlers(master_bot, master_id)   # products, wallet, payments, banners ... (owner = ADMIN_IDS[0])
    register_master_handlers(master_bot)             # /clone /myclones /deleteclone
    await master_bot.start()
    me = await master_bot.get_me()
    await db.ensure_master_store(master_id, ADMIN_IDS[0], f"@{me.username}")
    await apply_command_menu(master_bot, ADMIN_IDS, is_master=True)
    ACTIVE_CLONE_CLIENTS[master_id] = master_bot     # lets delivery / webhooks find the master client
    MASTER["client"] = master_bot
    log.info("Master bot started: @%s (owner %s)", me.username, ADMIN_IDS[0])

    # 3) Saved clones (one by one; failures are logged and skipped)
    started = 0
    for clone_doc in await db.get_running_clones():
        if await boot_clone(clone_doc):
            started += 1
    log.info("Platform ready — %s clone(s) running", started)

    try:
        await idle()
    finally:
        log.info("Shutting down…")
        for bot_id in list(ACTIVE_CLONE_CLIENTS.keys()):
            if bot_id != master_id:
                await stop_clone_client(bot_id)
        await master_bot.stop()
        await close_session()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(boot_platform())
