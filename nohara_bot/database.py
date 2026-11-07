"""
NOHARA STORE BOT — MongoDB Atlas State Layer (nohara_bot/database.py)

* Motor (async) — metadata and Telegram message-id pointers only. No files in MongoDB.
* All money is stored as INTEGER CENTS of USD (never float) -> no rounding bugs.
* Wallet debit and stock reservation are ATOMIC -> no negative balance, no overselling.
* Bot tokens and gateway keys are stored ENCRYPTED (see config.encrypt / decrypt).
"""

import time
import uuid
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from nohara_bot.config import (
    MONGO_URI,
    MASTER_AUDIT_CHANNEL_ID,
    DEFAULT_LANGUAGE,
    DEFAULT_FONT_STYLE,
    DEFAULT_USD_TO_INR,
    encrypt,
    decrypt,
)


def _now() -> int:
    return int(time.time())


class NoharaDatabase:
    def __init__(self, uri: str):
        self.client = AsyncIOMotorClient(uri, maxPoolSize=20, serverSelectionTimeoutMS=8000)
        self.db = self.client["nohara_multi_tenant"]
        self.clones = self.db["clone_settings"]
        self.products = self.db["products_meta"]
        self.wallets = self.db["user_wallets"]
        self.orders = self.db["orders_ledger"]
        self.users = self.db["bot_users"]
        self.coupons = self.db["coupons"]

    async def init_indexes(self) -> None:
        await self.clones.create_index("owner_id")
        await self.products.create_index("bot_id")
        await self.wallets.create_index([("bot_id", 1), ("user_id", 1)], unique=True)
        await self.orders.create_index([("bot_id", 1), ("user_id", 1), ("created_at", -1)])
        await self.users.create_index([("bot_id", 1), ("user_id", 1)], unique=True)
        await self.coupons.create_index([("bot_id", 1), ("code", 1)], unique=True)

    # ───────────────────────── Clones ─────────────────────────
    async def register_clone(self, bot_id: str, owner_id: int, bot_username: str, token: str) -> Dict[str, Any]:
        """Creates (or refreshes) a clone document. The token is stored ENCRYPTED."""
        bot_id = str(bot_id)
        defaults = {
            "owner_id": int(owner_id),
            "db_channel_id": 0,
            "log_channel_id": 0,
            "master_audit_channel_id": MASTER_AUDIT_CHANNEL_ID,
            "default_language": DEFAULT_LANGUAGE,
            "font_style": DEFAULT_FONT_STYLE,
            "welcome_msg": "",
            "links": {"updates_url": "", "support_url": ""},
            "ui_branding": {
                "start_photo_message_id": 0,
                "catalog_photo_message_id": 0,
                "wallet_photo_message_id": 0,
                "out_of_stock_photo_message_id": 0,
                "product_photo_message_id": 0,
                "oxapay_photo_message_id": 0,
                "fampay_photo_message_id": 0,
            },
            "gateways": {
                "oxapay": {"enabled": False, "api_key_encrypted": ""},
                "fampay": {"enabled": False, "upi_id": "", "webhook_secret_encrypted": ""},
            },
            "usd_to_inr": float(DEFAULT_USD_TO_INR),
            "fsub_channels": [],
            "folders": [],
            "custom_translations": {},
            "created_at": _now(),
        }
        await self.clones.update_one(
            {"_id": bot_id},
            {
                "$set": {
                    "bot_username": bot_username,
                    "token_encrypted": encrypt(token),
                    "status": "running",
                },
                "$setOnInsert": defaults,
            },
            upsert=True,
        )
        return await self.get_clone(bot_id)  # type: ignore[return-value]

    async def ensure_master_store(self, bot_id: str, owner_id: int, bot_username: str) -> Dict[str, Any]:
        """The MASTER bot is also a normal store (owner = first admin). Marked is_master, never booted as a clone."""
        await self.register_clone(bot_id, owner_id, bot_username, "")
        await self.clones.update_one(
            {"_id": str(bot_id)},
            {"$set": {"status": "master", "is_master": True, "owner_id": int(owner_id), "token_encrypted": ""}},
        )
        return await self.get_clone(bot_id)  # type: ignore[return-value]

    async def get_clone(self, bot_id: str) -> Optional[Dict[str, Any]]:
        return await self.clones.find_one({"_id": str(bot_id)})

    async def get_clones_by_owner(self, owner_id: int) -> List[Dict[str, Any]]:
        return await self.clones.find({"owner_id": int(owner_id), "is_master": {"$ne": True}}).to_list(length=100)

    async def get_running_clones(self) -> List[Dict[str, Any]]:
        return await self.clones.find({"status": "running"}).to_list(length=1000)

    async def count_clones(self) -> int:
        return await self.clones.count_documents({"is_master": {"$ne": True}})

    @staticmethod
    def get_clone_token(clone: Dict[str, Any]) -> str:
        return decrypt(clone.get("token_encrypted", ""))

    async def delete_clone(self, bot_id: str) -> None:
        """Removes the clone and its product list. Wallets & orders are kept for audit."""
        await self.products.delete_many({"bot_id": str(bot_id)})
        await self.clones.delete_one({"_id": str(bot_id)})

    async def update_clone_fields(self, bot_id: str, fields: Dict[str, Any]) -> None:
        await self.clones.update_one({"_id": str(bot_id)}, {"$set": fields})

    async def set_link(self, bot_id: str, which: str, url: str) -> None:
        """which = 'updates' | 'support'. Empty url removes the button from the main menu."""
        await self.update_clone_fields(bot_id, {f"links.{which}_url": url})

    async def set_channels(self, bot_id: str, db_channel_id: Optional[int] = None, log_channel_id: Optional[int] = None) -> None:
        fields: Dict[str, Any] = {}
        if db_channel_id is not None:
            fields["db_channel_id"] = int(db_channel_id)
        if log_channel_id is not None:
            fields["log_channel_id"] = int(log_channel_id)
        if fields:
            await self.update_clone_fields(bot_id, fields)

    async def set_oxapay(self, bot_id: str, enabled: bool, api_key: Optional[str] = None) -> None:
        fields: Dict[str, Any] = {"gateways.oxapay.enabled": bool(enabled)}
        if api_key is not None:
            fields["gateways.oxapay.api_key_encrypted"] = encrypt(api_key)
        await self.update_clone_fields(bot_id, fields)

    async def set_fampay(
        self,
        bot_id: str,
        enabled: Optional[bool] = None,
        upi_id: Optional[str] = None,
        webhook_secret: Optional[str] = None,
    ) -> None:
        fields: Dict[str, Any] = {}
        if enabled is not None:
            fields["gateways.fampay.enabled"] = bool(enabled)
        if upi_id is not None:
            fields["gateways.fampay.upi_id"] = upi_id
        if webhook_secret is not None:
            fields["gateways.fampay.webhook_secret_encrypted"] = encrypt(webhook_secret)
        if fields:
            await self.update_clone_fields(bot_id, fields)

    @staticmethod
    def get_oxapay_key(clone: Dict[str, Any]) -> str:
        return decrypt(clone.get("gateways", {}).get("oxapay", {}).get("api_key_encrypted", ""))

    @staticmethod
    def get_fampay_secret(clone: Dict[str, Any]) -> str:
        return decrypt(clone.get("gateways", {}).get("fampay", {}).get("webhook_secret_encrypted", ""))

    async def add_fsub(self, bot_id: str, channel_id: int, title: str, invite_link: str) -> None:
        await self.clones.update_one({"_id": str(bot_id)}, {"$pull": {"fsub_channels": {"channel_id": int(channel_id)}}})
        await self.clones.update_one(
            {"_id": str(bot_id)},
            {"$push": {"fsub_channels": {"channel_id": int(channel_id), "title": title, "invite_link": invite_link}}},
        )

    async def remove_fsub(self, bot_id: str, channel_id: int) -> bool:
        res = await self.clones.update_one({"_id": str(bot_id)}, {"$pull": {"fsub_channels": {"channel_id": int(channel_id)}}})
        return res.modified_count > 0

    # ───────────────────────── Users ─────────────────────────
    async def get_user_lang(self, bot_id: str, user_id: int, default: str) -> str:
        doc = await self.users.find_one({"bot_id": str(bot_id), "user_id": int(user_id)})
        return (doc or {}).get("lang") or default

    async def set_user_lang(self, bot_id: str, user_id: int, lang: str) -> None:
        await self.users.update_one(
            {"bot_id": str(bot_id), "user_id": int(user_id)},
            {"$set": {"lang": lang}},
            upsert=True,
        )

    async def touch_user(self, bot_id: str, user_id: int) -> bool:
        """Registers the user. Returns True when this is the FIRST time we see him."""
        res = await self.users.update_one(
            {"bot_id": str(bot_id), "user_id": int(user_id)},
            {"$setOnInsert": {"joined_at": _now()}},
            upsert=True,
        )
        return res.upserted_id is not None

    async def count_users(self, bot_id: str) -> int:
        return await self.users.count_documents({"bot_id": str(bot_id)})

    async def get_user(self, bot_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        return await self.users.find_one({"bot_id": str(bot_id), "user_id": int(user_id)})

    async def list_user_ids(self, bot_id: str) -> List[int]:
        docs = await self.users.find({"bot_id": str(bot_id)}, {"user_id": 1}).to_list(length=None)
        return [int(d["user_id"]) for d in docs]

    # ── VIP ──
    async def extend_vip(self, bot_id: str, user_id: int, days: int) -> int:
        """Adds `days` to the VIP time (stacks on top of any VIP time that is left). Returns the new expiry."""
        doc = await self.get_user(bot_id, user_id) or {}
        start = max(_now(), int(doc.get("vip_until") or 0))
        until = start + int(days) * 86400
        await self.users.update_one(
            {"bot_id": str(bot_id), "user_id": int(user_id)},
            {"$set": {"vip_until": until}},
            upsert=True,
        )
        return until

    # ── Referral ──
    async def set_referrer(self, bot_id: str, user_id: int, referrer_id: int) -> None:
        """Only the first referrer counts, and nobody can refer himself."""
        if int(user_id) == int(referrer_id):
            return
        await self.users.update_one(
            {"bot_id": str(bot_id), "user_id": int(user_id), "referred_by": {"$exists": False}},
            {"$set": {"referred_by": int(referrer_id)}},
        )

    async def claim_referral_reward(self, bot_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        """Atomic: returns the user document ONLY the first time (so a referrer is rewarded once per friend)."""
        return await self.users.find_one_and_update(
            {"bot_id": str(bot_id), "user_id": int(user_id), "referred_by": {"$exists": True}, "ref_rewarded": {"$ne": True}},
            {"$set": {"ref_rewarded": True}},
            return_document=ReturnDocument.AFTER,
        )

    async def add_referral_stats(self, bot_id: str, referrer_id: int, cents: int) -> None:
        await self.users.update_one(
            {"bot_id": str(bot_id), "user_id": int(referrer_id)},
            {"$inc": {"ref_paid_count": 1, "ref_earned_cents": int(cents)}},
        )

    async def count_referrals(self, bot_id: str, user_id: int) -> int:
        return await self.users.count_documents({"bot_id": str(bot_id), "referred_by": int(user_id)})

    # ── Active coupon of a user ──
    async def set_user_coupon(self, bot_id: str, user_id: int, code: str, pct: int) -> None:
        await self.users.update_one(
            {"bot_id": str(bot_id), "user_id": int(user_id)},
            {"$set": {"coupon": {"code": code, "pct": int(pct)}}},
            upsert=True,
        )

    async def clear_user_coupon(self, bot_id: str, user_id: int) -> None:
        await self.users.update_one({"bot_id": str(bot_id), "user_id": int(user_id)}, {"$unset": {"coupon": ""}})

    # ───────────────────────── Products ─────────────────────────
    async def save_product(self, bot_id: str, product_id: str, title: str, price_cents: int, stock: int, db_channel_msg_id: int, folder: str = "") -> None:
        """Stores ONLY metadata + the message_id pointer inside the owner's private DB channel."""
        doc = {
            "_id": product_id,
            "bot_id": str(bot_id),
            "title": title,
            "price_cents": int(price_cents),
            "stock": int(stock),  # -1 = unlimited
            "db_channel_message_id": int(db_channel_msg_id),
            "folder": folder,
            "created_at": _now(),
        }
        await self.products.replace_one({"_id": product_id}, doc, upsert=True)

    async def list_products(self, bot_id: str) -> List[Dict[str, Any]]:
        return await self.products.find({"bot_id": str(bot_id)}).sort("created_at", 1).to_list(length=200)

    async def add_folder(self, bot_id: str, name: str) -> str:
        folder_id = uuid.uuid4().hex[:6]
        await self.clones.update_one({"_id": str(bot_id)}, {"$push": {"folders": {"id": folder_id, "name": name}}})
        return folder_id

    async def delete_folder(self, bot_id: str, folder_id: str) -> bool:
        """Removes the folder; its products are kept and move to 'Other products'."""
        res = await self.clones.update_one({"_id": str(bot_id)}, {"$pull": {"folders": {"id": folder_id}}})
        await self.products.update_many({"bot_id": str(bot_id), "folder": folder_id}, {"$set": {"folder": ""}})
        return res.modified_count > 0

    async def set_product_folder(self, bot_id: str, product_id: str, folder_id: str) -> bool:
        res = await self.products.update_one({"_id": product_id, "bot_id": str(bot_id)}, {"$set": {"folder": folder_id}})
        return res.matched_count > 0

    async def list_all_clones(self, limit: int = 15) -> List[Dict[str, Any]]:
        return await self.clones.find({"is_master": {"$ne": True}}).sort("created_at", -1).to_list(length=limit)

    async def get_product(self, bot_id: str, product_id: str) -> Optional[Dict[str, Any]]:
        return await self.products.find_one({"_id": product_id, "bot_id": str(bot_id)})

    async def delete_product(self, bot_id: str, product_id: str) -> bool:
        res = await self.products.delete_one({"_id": product_id, "bot_id": str(bot_id)})
        return res.deleted_count > 0

    async def reserve_stock(self, bot_id: str, product_id: str) -> bool:
        """Atomically takes ONE unit. Unlimited products (stock = -1) always succeed."""
        product = await self.get_product(bot_id, product_id)
        if not product:
            return False
        if product["stock"] == -1:
            return True
        res = await self.products.update_one(
            {"_id": product_id, "bot_id": str(bot_id), "stock": {"$gt": 0}},
            {"$inc": {"stock": -1}},
        )
        return res.modified_count == 1

    async def release_stock(self, bot_id: str, product_id: str) -> None:
        """Gives ONE unit back (only for limited-stock products)."""
        await self.products.update_one(
            {"_id": product_id, "bot_id": str(bot_id), "stock": {"$gte": 0}},
            {"$inc": {"stock": 1}},
        )

    # ───────────────────────── Wallet (integer cents) ─────────────────────────
    async def get_balance(self, bot_id: str, user_id: int) -> int:
        doc = await self.wallets.find_one({"bot_id": str(bot_id), "user_id": int(user_id)})
        return int((doc or {}).get("balance_cents", 0))

    async def credit(self, bot_id: str, user_id: int, cents: int) -> int:
        doc = await self.wallets.find_one_and_update(
            {"bot_id": str(bot_id), "user_id": int(user_id)},
            {"$inc": {"balance_cents": int(cents)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["balance_cents"])

    async def debit(self, bot_id: str, user_id: int, cents: int) -> bool:
        """Atomic: succeeds only if the balance is enough. Balance can NEVER go negative."""
        doc = await self.wallets.find_one_and_update(
            {"bot_id": str(bot_id), "user_id": int(user_id), "balance_cents": {"$gte": int(cents)}},
            {"$inc": {"balance_cents": -int(cents)}},
            return_document=ReturnDocument.AFTER,
        )
        return doc is not None

    # ───────────────────────── Orders ─────────────────────────
    async def create_order(
        self,
        bot_id: str,
        user_id: int,
        product: Dict[str, Any],
        method: str,
        status: str = "pending",
        **extra: Any,
    ) -> Dict[str, Any]:
        order = {
            "_id": uuid.uuid4().hex[:12],
            "bot_id": str(bot_id),
            "user_id": int(user_id),
            "product_id": product["_id"],
            "title": product["title"],
            "price_cents": int(product["price_cents"]),
            "method": method,  # wallet | oxapay | upi
            "status": status,  # pending | paid | delivered | cancelled | failed | refunded
            "created_at": _now(),
        }
        order["list_price_cents"] = int(product.get("list_price_cents", product["price_cents"]))
        if product.get("coupon"):
            order["coupon"] = str(product["coupon"])
        order.update(extra)
        await self.orders.insert_one(order)
        return order

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return await self.orders.find_one({"_id": order_id})

    async def set_order_fields(self, order_id: str, **fields: Any) -> None:
        await self.orders.update_one({"_id": order_id}, {"$set": fields})

    async def mark_paid(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Atomic pending -> paid transition. Returns the order ONLY for the first caller,
        so a replayed webhook / double click can never deliver the product twice.
        (A user-cancelled order can still be paid later, so 'cancelled' is allowed too.)
        """
        return await self.orders.find_one_and_update(
            {"_id": order_id, "status": {"$in": ["pending", "cancelled"]}},
            {"$set": {"status": "paid", "paid_at": _now()}},
            return_document=ReturnDocument.AFTER,
        )

    async def cancel_order(self, order_id: str) -> bool:
        res = await self.orders.update_one({"_id": order_id, "status": "pending"}, {"$set": {"status": "cancelled"}})
        return res.modified_count == 1

    async def get_open_upi_order(self, bot_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        return await self.orders.find_one(
            {"bot_id": str(bot_id), "user_id": int(user_id), "method": "upi", "status": "pending"},
            sort=[("created_at", -1)],
        )

    # ───────────────────────── Coupons ─────────────────────────
    async def add_coupon(self, bot_id: str, code: str, pct: int, max_uses: int = 0, expires_at: int = 0) -> None:
        """max_uses 0 = unlimited · expires_at 0 = never. Percent discount on ONE purchase per user."""
        await self.coupons.replace_one(
            {"bot_id": str(bot_id), "code": code},
            {"bot_id": str(bot_id), "code": code, "pct": int(pct), "max_uses": int(max_uses),
             "expires_at": int(expires_at), "used": 0, "used_by": [], "created_at": _now()},
            upsert=True,
        )

    async def get_coupon(self, bot_id: str, code: str) -> Optional[Dict[str, Any]]:
        return await self.coupons.find_one({"bot_id": str(bot_id), "code": code.upper()})

    async def list_coupons(self, bot_id: str) -> List[Dict[str, Any]]:
        return await self.coupons.find({"bot_id": str(bot_id)}).sort("created_at", -1).to_list(length=50)

    async def delete_coupon(self, bot_id: str, code: str) -> bool:
        res = await self.coupons.delete_one({"bot_id": str(bot_id), "code": code.upper()})
        return res.deleted_count > 0

    async def consume_coupon(self, bot_id: str, code: str, user_id: int) -> None:
        """Called after a discounted order was DELIVERED: counts the use and clears the user's active coupon."""
        await self.coupons.update_one(
            {"bot_id": str(bot_id), "code": code.upper()},
            {"$inc": {"used": 1}, "$addToSet": {"used_by": int(user_id)}},
        )
        await self.users.update_one(
            {"bot_id": str(bot_id), "user_id": int(user_id), "coupon.code": code.upper()},
            {"$unset": {"coupon": ""}},
        )

    async def set_product_vip(self, bot_id: str, product_id: str, flag: bool) -> bool:
        res = await self.products.update_one({"_id": product_id, "bot_id": str(bot_id)}, {"$set": {"vip_only": bool(flag)}})
        return res.matched_count > 0

    async def get_stats(self, bot_id: str) -> Dict[str, int]:
        # wallet top-ups are stored as orders too (kind = "topup") but are not store sales
        paid_filter = {"bot_id": str(bot_id), "kind": {"$ne": "topup"}, "status": {"$in": ["paid", "delivered", "refunded"]}}
        return {
            "products": await self.products.count_documents({"bot_id": str(bot_id)}),
            "orders": await self.orders.count_documents(paid_filter),
            "delivered": await self.orders.count_documents({"bot_id": str(bot_id), "kind": {"$ne": "topup"}, "status": "delivered"}),
            "users": await self.count_users(bot_id),
        }


db = NoharaDatabase(MONGO_URI)
