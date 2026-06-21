import os

API_ID = int(os.environ.get("API_ID", "38498066"))
API_HASH = os.environ.get("API_HASH", "c9696114751feacdeb1b4487f5839a1a")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

OWNER = int(os.environ.get("OWNER", "8446475678"))

AUTH_USER = os.environ.get(
    "AUTH_USERS",
    "6660248311,6446087354,8480660521,8680968748,8446475678,7988815969,8429278856,7920113547,8723278238"
).split(',')
AUTH_USERS = [int(uid) for uid in AUTH_USER if uid.strip()]
if OWNER not in AUTH_USERS:
    AUTH_USERS.append(OWNER)

# ── Logging Channel ────────────────────────────────────────────────────────
# Format: -100123456789 (negative group chat ID)
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "-1003597599758"))

# ── Video Player Configuration ─────────────────────────────────────────────
HEROKU_VIDEO_URL = os.environ.get(
    "HEROKU_VIDEO_URL",
    "https://anonymouspwplayerrrr-c95d81521328.herokuapp.com/pw"
)

# ── Backward-compatible lowercase aliases ──────────────────────────────────
api_id = API_ID
api_hash = API_HASH
bot_token = BOT_TOKEN
auth_users = AUTH_USERS
log_channel = LOG_CHANNEL
heroku_video_url = HEROKU_VIDEO_URL
