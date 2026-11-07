"""
NOHARA STORE BOT — Central configuration (nohara_bot/config.py)

- Reads every secret from environment variables (never hardcode keys in the repo).
- Fails fast with a clear message if something important is missing.
- Provides encrypt()/decrypt() (Fernet) so bot tokens and gateway keys are never
  stored as plain text in MongoDB.
"""

import base64
import hashlib
import os
import sys
from cryptography.fernet import Fernet, InvalidToken

# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ✏️  अपनी वैल्यू सिर्फ़ यहाँ भरें (FILL YOUR VALUES HERE — only inside the "")  ║
# ║                                                                          ║
# ║  Render के Environment में कुछ भरने की ज़रूरत नहीं।                         ║
# ║  (अगर Render में कोई variable भरा होगा तो वो यहाँ की वैल्यू से ऊपर चलेगा।)    ║
# ║                                                                          ║
# ║  ⚠️  ज़रूरी: इस फ़ाइल में आपके secrets होंगे, इसलिए GitHub repo हमेशा         ║
# ║      PRIVATE रखें। Public repo में रखा तो आपके बॉट टोकन, DB पासवर्ड और       ║
# ║      keys कोई भी देख सकता है। इस फ़ाइल को किसी को न भेजें।                   ║
# ╚══════════════════════════════════════════════════════════════════════════╝
MY_VALUES = {
    # my.telegram.org से (सिर्फ़ नंबर), जैसे "1234567"
    "API_ID": "",
    # my.telegram.org से
    "API_HASH": "",
    # @BotFather से master बॉट का टोकन, जैसे "123456789:AAH..."
    "MASTER_BOT_TOKEN": "",
    # MongoDB Atlas connection string, जैसे "mongodb+srv://user:pass@cluster0.xxxx.mongodb.net/?retryWrites=true&w=majority"
    "MONGO_URI": "",
    # कोई भी लंबा रैंडम टेक्स्ट (कम से कम 32, बेहतर 50 अक्षर, बिना space). एक बार भरकर कभी न बदलें!
    "ENCRYPTION_KEY": "",
    # आपकी Telegram user ID; एक से ज़्यादा हों तो कॉमा से अलग करें: "111,222"
    "ADMIN_IDS": "",
    # (वैकल्पिक) master audit channel की ID, जैसे "-1001234567890". नहीं चाहिए तो "0"
    "MASTER_AUDIT_CHANNEL_ID": "0",
    # (वैकल्पिक) अपना custom domain हो तभी भरें, वरना खाली छोड़ दें (Render का URL अपने आप मिल जाता है)
    "APP_URL": "",

    # ── वैकल्पिक सेटिंग (जैसी हैं वैसी छोड़ सकते हैं) ──
    "DEFAULT_LANGUAGE": "en",
    "DEFAULT_FONT_STYLE": "default_html",
    "DEFAULT_USD_TO_INR": "88.0",
    "MAX_CLONES_PER_USER": "3",
    "MAX_CLONES_TOTAL": "15",
    "BUTTON_COLORS": "true",
    "OXAPAY_SANDBOX": "false",  # टेस्ट के लिए "true", असली पेमेंट के लिए "false"
}


def _env(name: str, default: str = "") -> str:
    """Render Environment value if set, otherwise the value written in MY_VALUES above."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    return str(MY_VALUES.get(name, default) or default).strip()


def _fail(message: str) -> None:
    sys.stderr.write(f"\n[CONFIG ERROR] {message}\n\n")
    sys.exit(1)


def _required(name: str) -> str:
    value = _env(name)
    if not value:
        _fail(f"'{name}' is empty. Fill it in nohara_bot/config.py (MY_VALUES at the top) or in Render -> Environment.")
    return value


def _required_int(name: str) -> int:
    raw = _required(name)
    try:
        return int(raw)
    except ValueError:
        _fail(f"Environment variable '{name}' must be a number, got: {raw!r}")
    return 0  # unreachable


def _optional_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _fail(f"Environment variable '{name}' must be a number, got: {raw!r}")
    return default  # unreachable


def _int_list(name: str) -> list:
    raw = _env(name)
    result = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                _fail(f"Environment variable '{name}' must be comma separated numbers, got: {raw!r}")
    return result


# ───────────────────────── Telegram ─────────────────────────
API_ID = _required_int("API_ID")
API_HASH = _required("API_HASH")
MASTER_BOT_TOKEN = _required("MASTER_BOT_TOKEN")

# Telegram user IDs of platform admins (comma separated). They can manage any clone.
ADMIN_IDS = _int_list("ADMIN_IDS")
if not ADMIN_IDS:
    _fail("'ADMIN_IDS' is empty. Put YOUR Telegram user id in MY_VALUES (you are the owner of the master store).")

# Private channel where the MASTER bot posts audit logs (master bot must be admin there).
# 0 = disabled.
MASTER_AUDIT_CHANNEL_ID = _optional_int("MASTER_AUDIT_CHANNEL_ID", 0)

# ───────────────────────── Database ─────────────────────────
MONGO_URI = _required("MONGO_URI")

# ───────────────────────── Security ─────────────────────────
_ENCRYPTION_KEY = _required("ENCRYPTION_KEY")


def _build_fernet(raw: str) -> Fernet:
    """
    ENCRYPTION_KEY can be EITHER
      * a classic Fernet key (44 characters, made by Fernet.generate_key()), OR
      * any long random text (at least 32 characters) — handy when you only have a phone.
    Long random text is turned into a proper key with SHA-256, so no computer is needed.
    """
    try:
        return Fernet(raw.encode())  # classic Fernet key
    except Exception:
        pass
    if len(raw) < 32:
        _fail(
            "ENCRYPTION_KEY is too short. Use at least 32 random letters/digits "
            "(for example 40-60 characters from your phone's 'suggest strong password')."
        )
    derived = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    return Fernet(derived)


_FERNET = _build_fernet(_ENCRYPTION_KEY)

# ───────────────────────── Web server ─────────────────────────
PORT = _optional_int("PORT", 10000)
# Public HTTPS URL of this service (Render sets RENDER_EXTERNAL_URL automatically).
APP_URL = (_env("APP_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")

# ───────────────────────── Store defaults ─────────────────────────
DEFAULT_LANGUAGE = _env("DEFAULT_LANGUAGE", "en") or "en"
DEFAULT_FONT_STYLE = _env("DEFAULT_FONT_STYLE", "default_html") or "default_html"
DEFAULT_USD_TO_INR = float(_env("DEFAULT_USD_TO_INR", "88.0") or 88.0)

# Free Render instances have little RAM, so keep the number of running clones limited.
MAX_CLONES_PER_USER = _optional_int("MAX_CLONES_PER_USER", 3)
MAX_CLONES_TOTAL = _optional_int("MAX_CLONES_TOTAL", 15)

# Native Telegram coloured buttons (green / red / blue). Needs Telegram apps updated after
# Feb 2026 to SHOW the colours; older apps show normal buttons. Set BUTTON_COLORS=false to disable.
BUTTON_COLORS = _env("BUTTON_COLORS", "true").lower() != "false"

# Set OXAPAY_SANDBOX=true while testing — invoices are created in OxaPay sandbox mode.
OXAPAY_SANDBOX = _env("OXAPAY_SANDBOX", "false").lower() == "true"


# ───────────────────────── Encryption helpers ─────────────────────────
def encrypt(plain_text: str) -> str:
    """Encrypts a secret (bot token / API key) before it is stored in MongoDB."""
    if not plain_text:
        return ""
    return _FERNET.encrypt(plain_text.encode()).decode()


def decrypt(token: str) -> str:
    """Returns the original secret, or '' if the value is empty / was encrypted with another key."""
    if not token:
        return ""
    try:
        return _FERNET.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
