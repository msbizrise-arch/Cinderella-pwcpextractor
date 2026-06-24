import requests
from PIL import Image
import asyncio
import aiohttp
import json
import zipfile
from typing import Dict, List, Any, Tuple
from collections import defaultdict
from base64 import b64encode, b64decode
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import os
import base64
from pyrogram import Client, filters
import sys
import re
import uuid
import random
import string
import hashlib
from flask import Flask
import threading
from pyrogram.types.messages_and_media import message
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.errors import FloodWait
from pyromod import listen
from pyromod.exceptions.listener_timeout import ListenerTimeout
from pyrogram.types import Message
import pyrogram
from pyrogram import Client, filters
from pyrogram.types import User, Message
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.raw.functions.channels import GetParticipants
from config import api_id, api_hash, bot_token, auth_users, OWNER, LOG_CHANNEL, HEROKU_VIDEO_URL
from datetime import datetime, timezone, timedelta
import time
from concurrent.futures import ThreadPoolExecutor

# ThreadPool for running async functions in separate threads
THREADPOOL = ThreadPoolExecutor(max_workers=1000)

import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Bot credentials from environment variables (Render compatible)
API_ID = int(os.environ.get("API_ID", 38498066))
API_HASH = os.environ.get("API_HASH", "c9696114751feacdeb1b4487f5839a1a")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Initialize Bot Globally
bot = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ── Persistent Auth Users (JSON-backed, survives bot restart) ────────────────
AUTH_FILE = "auth_users.json"

def _load_auth_users():
    try:
        with open(AUTH_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_auth_users(users: set):
    try:
        with open(AUTH_FILE, "w") as f:
            json.dump(list(users), f)
    except Exception:
        pass

auth_users = _load_auth_users()
# Always include the base list coming from config.py (env AUTH_USERS)
from config import auth_users as _CONFIG_AUTH_USERS
auth_users.update(_CONFIG_AUTH_USERS)
_save_auth_users(auth_users)
# ─────────────────────────────────────────────────────────────────────────────

# ── Persistent Broadcast Users (JSON-backed, survives bot restart) ───────────
BROADCAST_FILE = "broadcast_users.json"

def _load_broadcast_users():
    try:
        with open(BROADCAST_FILE, "r") as f:
            return set(json.load(f))
    except Exception:
        return set()

def _save_broadcast_users(users: set):
    try:
        with open(BROADCAST_FILE, "w") as f:
            json.dump(list(users), f)
    except Exception:
        pass

broadcast_users = _load_broadcast_users()
# ─────────────────────────────────────────────────────────────────────────────

# Flask app for Render
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app_flask.run(host="0.0.0.0", port=8000)

image_list = [
    "https://graph.org/file/28339f6c961ca96a84f47-1a070fdc1632724513.jpg",
    "https://graph.org/file/9db3816e75336ecc45959-6d49ddd4d0e92f1aae.jpg",
    "https://graph.org/file/1d1548631e6d1d3b3796e-b6647f0434c20f100a.jpg",
    "https://graph.org/file/a1c4b27984bb61183048c-d11e4d6c9ea09fcedb.jpg",
    "https://graph.org/file/1d1dab8f4dc33df10e38c-a3c92d386be28422ac.jpg",
    "https://graph.org/file/7831481e4c899748ee8a1-b976b5e72df8c3618c.jpg",
    "https://graph.org/file/41b150f2461004c4fd99a-d29d2bc307f0fe6491.jpg",
    "https://graph.org/file/ce8ebdb5c2ba8932ec780-1737059c6bb976617d.jpg",
    "https://graph.org/file/1f2bd4b7d0747a432e3fe-b1229343f6557ba344.jpg",
    "https://graph.org/file/b07088988e66447aeb92f-f8c4f26ad5b867aa5a.jpg",
]
print(4321)

# ── Thumbnail for all document files (persistent, survives restarts) ────────
# IMPORTANT: Telegram's thumb requirement (Pyrogram docs):
#   - Must be a valid JPEG
#   - Must be < 200 KB in size
#   - Width & height must NOT exceed 320px
# The previous implementation downloaded the raw image with ZERO validation,
# so if the source image was not a valid/compliant JPEG, Telegram silently
# dropped the thumbnail (no crash, no error -> looked like "thumbnail nahi
# lag raha"). It also only ran once at import time with no retry, so a
# single transient failure on startup left THUMBNAIL_FILE = None forever.
THUMB_URL = "https://ibb.co/tpTLJ5wv"
# Fallback URLs if primary fails (all are direct graph.org JPEGs)
THUMB_FALLBACK_URLS = [
    "https://graph.org/file/28339f6c961ca96a84f47-1a070fdc1632724513.jpg",
    "https://graph.org/file/9db3816e75336ecc45959-6d49ddd4d0e92f1aae.jpg",
    "https://graph.org/file/1d1548631e6d1d3b3796e-b6647f0434c20f100a.jpg",
]
THUMB_PATH = "document_thumb_v2.jpg"
THUMB_MAX_SIDE = 320       # Telegram hard limit (matches the doc note above)
THUMB_MAX_BYTES = 200 * 1024  # Telegram hard limit (< 200KB)


def _process_thumbnail_bytes(raw_bytes: bytes, dest_path: str) -> bool:
    """Save raw image bytes as a Telegram-compliant thumbnail at dest_path.

    IMPORTANT: we ALWAYS force a full re-encode here, even if the source
    image already looks compliant on dims/size/format. Telegram's thumb
    uploader silently drops thumbnails that are progressive-encoded JPEGs
    or carry an ICC/EXIF color profile - both are common for images served
    by web CDNs (like graph.org) even though they "look like" a normal
    JPEG. A byte-for-byte passthrough of such a file looked fine locally
    (valid JPEG, right dims, right size) but Telegram would still reject
    it with zero error/log on our side. Forcing every image through
    Pillow's RGB + baseline-JPEG save path guarantees the output is
    actually compliant, not just superficially compliant.
    """
    try:
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(raw_bytes))
        img.load()  # force-decode now so corrupt files raise immediately

        if img.mode != "RGB":
            img = img.convert("RGB")

        w, h = img.size
        scale = min(THUMB_MAX_SIDE / w, THUMB_MAX_SIDE / h, 1.0)
        if scale < 1.0:
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

        quality = 95
        buf = io.BytesIO()
        while quality >= 35:
            buf.seek(0)
            buf.truncate(0)
            # progressive=False -> baseline JPEG (required by Telegram's thumb uploader)
            img.save(buf, format="JPEG", quality=quality, optimize=True, progressive=False)
            if buf.tell() < THUMB_MAX_BYTES:
                break
            quality -= 10

        with open(dest_path, "wb") as f:
            f.write(buf.getvalue())

        logging.info(
            f"Thumbnail re-encoded (baseline JPEG, forced): {img.size[0]}x{img.size[1]}px, "
            f"{buf.tell() // 1024}KB, quality={quality}"
        )
        return True
    except Exception as e:
        logging.error(f"Thumbnail processing failed: {e}", exc_info=True)
        return False


def ensure_thumbnail_exists(force: bool = False):
    """Ensure a Telegram-compliant thumbnail file exists, downloading and
    re-processing it if needed. Safe to call again later (e.g. before each
    send) to self-heal if the file ever goes missing or got corrupted."""
    try:
        if os.path.exists(THUMB_PATH) and not force:
            # Sanity-check the cached file still meets Telegram's limits;
            # if not, fall through and regenerate it instead of trusting it blindly.
            try:
                from PIL import Image
                with Image.open(THUMB_PATH) as im:
                    w, h = im.size
                size_ok = os.path.getsize(THUMB_PATH) < THUMB_MAX_BYTES
                dim_ok = w <= THUMB_MAX_SIDE and h <= THUMB_MAX_SIDE
                if size_ok and dim_ok:
                    return THUMB_PATH
                logging.warning("Cached thumbnail fails Telegram limits, regenerating...")
            except Exception:
                logging.warning("Cached thumbnail unreadable, regenerating...")

        last_error = None
        for attempt in range(1, 4):
            try:
                logging.info(f"Downloading thumbnail from {THUMB_URL} (attempt {attempt})...")
                resp = requests.get(THUMB_URL, timeout=15)
                if resp.status_code == 200 and resp.content:
                    if _process_thumbnail_bytes(resp.content, THUMB_PATH):
                        logging.info(f"Thumbnail saved to {THUMB_PATH}")
                        return THUMB_PATH
                    last_error = "processing failed"
                else:
                    last_error = f"HTTP {resp.status_code}"
            except Exception as e:
                last_error = str(e)
            time.sleep(1.5)

        logging.warning(f"Failed to prepare thumbnail after 3 attempts: {last_error}")
        return None
    except Exception as e:
        logging.error(f"Thumbnail error: {e}", exc_info=True)
        return None


# Ensure thumbnail is ready when bot starts
THUMBNAIL_FILE = ensure_thumbnail_exists()


def get_thumbnail():
    """Always use this instead of the THUMBNAIL_FILE global directly.
    Self-heals if the thumbnail is missing/corrupted at send time, so a
    startup hiccup can't permanently disable thumbnails for the whole run."""
    global THUMBNAIL_FILE
    if not THUMBNAIL_FILE or not os.path.exists(THUMBNAIL_FILE):
        THUMBNAIL_FILE = ensure_thumbnail_exists(force=True)
    return THUMBNAIL_FILE


async def _resolve_direct_image_url(session, url: str) -> str:
    """Resolve ibb.co page URL to direct image URL."""
    import re as _re
    ibb_hosts = ("ibb.co", "imgbb.com", "www.ibb.co", "www.imgbb.com")
    parsed_host = url.split("/")[2] if url.startswith("http") else ""
    if parsed_host not in ibb_hosts:
        return url
    try:
        ua = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36"}
        async with session.get(url, headers=ua, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                logging.warning(f"[thumb] ibb page HTTP {resp.status}")
                return url
            html = await resp.text(errors="replace")
        # og:image is most reliable
        m1 = _re.search(r"""property=["']og:image["'][^>]+content=["']([^"']+)["']""", html)
        if not m1:
            m1 = _re.search(r"""content=["']([^"']+)["'][^>]+property=["']og:image["']""", html)
        if m1:
            logging.info(f"[thumb] ibb og:image -> {m1.group(1).strip()}")
            return m1.group(1).strip()
        # fallback: scan for i.ibb.co direct URLs
        imgs = _re.findall("https://i[.]ibb[.]co/[^\\s\"'<>]+[.](?:jpg|jpeg|png|webp)", html)
        if imgs:
            logging.info(f"[thumb] ibb scan -> {imgs[0]}")
            return imgs[0]
    except Exception as e:
        logging.warning(f"[thumb] ibb resolve error: {e}")
    return url



async def get_thumbnail_async() -> str:
    """Async-safe thumbnail getter.
    Downloads & processes the thumbnail via aiohttp if the cached file is
    missing or invalid, so the async event loop is never blocked.
    Handles ibb.co/imgbb.com page URLs by extracting the direct image URL.
    Returns a valid local file path string, or None if everything fails."""
    global THUMBNAIL_FILE

    # Fast path: cached file is already good
    if THUMBNAIL_FILE and os.path.exists(THUMBNAIL_FILE):
        try:
            from PIL import Image
            with Image.open(THUMBNAIL_FILE) as im:
                w, h = im.size
            if (os.path.getsize(THUMBNAIL_FILE) < THUMB_MAX_BYTES and
                    w <= THUMB_MAX_SIDE and h <= THUMB_MAX_SIDE):
                return THUMBNAIL_FILE
        except Exception:
            pass  # fall through to re-download

    # Slow path: download via aiohttp (non-blocking)
    for attempt in range(1, 5):
        try:
            async with aiohttp.ClientSession() as _tsess:
                # Resolve ibb.co/imgbb page to actual direct image URL first
                direct_url = await _resolve_direct_image_url(_tsess, THUMB_URL)
                logging.info(f"[thumb_async] Downloading from {direct_url} (attempt {attempt})")
                async with _tsess.get(direct_url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        raw = await resp.read()
                        if raw and _process_thumbnail_bytes(raw, THUMB_PATH):
                            THUMBNAIL_FILE = THUMB_PATH
                            logging.info(f"[thumb_async] Thumbnail ready (attempt {attempt})")
                            return THUMBNAIL_FILE
                        else:
                            logging.warning(f"[thumb_async] Processing failed (attempt {attempt})")
                    else:
                        logging.warning(f"[thumb_async] HTTP {resp.status} (attempt {attempt})")
        except Exception as te:
            logging.warning(f"[thumb_async] Download error attempt {attempt}: {te}")
        await asyncio.sleep(1.5)

    logging.error("[thumb_async] All attempts failed, sending without thumbnail")
    return None
# ─────────────────────────────────────────────────────────────────────────────


# ===============================================================
# GLOBAL STATE: Pagination tracking for batch selection
# ===============================================================
# Stores batch list and current page per user
# Format: {user_id: {"batches": [...], "page": 0, "message_id": int}}
user_batch_pages = {}

# Batch selection pending state (to know if we're waiting for index)
user_batch_selecting = set()


# ===============================================================
# IST TIMEZONE HELPER
# ===============================================================
IST = timezone(timedelta(hours=5, minutes=30))


def get_ist_now():
    """Get current datetime in IST."""
    return datetime.now(IST)


def utc_to_ist(dt_utc: datetime) -> datetime:
    """Convert UTC datetime to IST."""
    return dt_utc.astimezone(IST)


def ist_to_utc(dt_ist: datetime) -> datetime:
    """Convert IST datetime to UTC."""
    if dt_ist.tzinfo is None:
        dt_ist = dt_ist.replace(tzinfo=IST)
    return dt_ist.astimezone(timezone.utc)


# ===============================================================
# CRITICAL FIX: Safe string/topic extraction helpers
# ===============================================================
def safe_str(value: Any, default: str = "") -> str:
    """
    Safely convert ANY value to a string.
    Handles: None, dict, list, int, bool, and nested structures.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Try to extract a meaningful string from dict
        for key in ["topic", "name", "title", "subject", "text", "value", "label", "display", "en", "hi"]:
            if key in value and value[key] is not None:
                return safe_str(value[key], default)
        # If no known key, return the first string value found
        for k, v in value.items():
            if isinstance(v, str):
                return v
        # Last resort: JSON string
        try:
            return json.dumps(value, ensure_ascii=False)
        except:
            return default
    if isinstance(value, list):
        if not value:
            return default
        return safe_str(value[0], default)
    # For int, float, bool, etc.
    try:
        return str(value)
    except:
        return default


def safe_topic(value: Any, default: str = "Unknown Topic") -> str:
    """
    Safely extract a topic/title string from ANY value.
    Replaces : and / with - to avoid parsing issues.
    Handles dict, None, list, and nested structures gracefully.
    """
    topic = safe_str(value, default)
    # Sanitize: replace problematic characters
    topic = topic.replace(":", "_").replace("/", "-").replace("\n", " ").replace("\r", " ")
    # Remove extra whitespace
    topic = " ".join(topic.split())
    # Limit length
    if len(topic) > 200:
        topic = topic[:200]
    if not topic or topic.lower() == "none":
        topic = default
    return topic


# ===============================================================
# ADVANCED MOBILE HEADERS for PW API
# ===============================================================
def get_pw_mobile_headers(token: str) -> Dict[str, str]:
    """Get properly configured MOBILE headers for PW API access.
    Using MOBILE client-type with proper device-meta gives broader
    access to ALL batches (purchased + non-purchased)."""
    headers = {
        "client-id": "5eb393ee95fab7468a79d189",
        "client-type": "MOBILE",
        "client-version": "538",
        "device-meta": '{"APP_VERSION":"538","APP_VERSION_NAME":"15.32.0","DEVICE_MAKE":"Samsung","DEVICE_MODEL":"SM-A707F","OS_VERSION":"11","PACKAGE_NAME":"xyz.penpencil.physicswala","network":"wifi_data","carrier":"UNDEFINED"}',
        "randomId": "3d3b49f068728fa3",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://android.pw.live",
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 11; SM-A707F Build/RP1A.200720.012)"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_pw_login_headers() -> Dict[str, str]:
    """Headers for login/OTP operations"""
    return {
        "client-id": "5eb393ee95fab7468a79d189",
        "client-version": "12.84",
        "Client-Type": "MOBILE",
        "randomId": "e4307177362e86f1",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json"
    }


# ===============================================================
# VIDEO URL EXTRACTOR (Enhanced for ALL batches)
# ===============================================================
def extract_video_data_from_schedule(schedule_details_data):
    """Extract MPD/CloudFront video URL with DRM keys from schedule-details response."""
    video_info = {}
    if not schedule_details_data or not schedule_details_data.get("data"):
        return video_info

    data = schedule_details_data["data"]
    video_details = data.get("videoDetails", {})

    if video_details:
        # Primary: Get the MPD URL (CloudFront signed URL)
        video_url = video_details.get("videoUrl") or video_details.get("url") or ""
        if video_url and (".mpd" in video_url.lower() or ".m3u8" in video_url.lower() or "cloudfront" in video_url.lower()):
            video_info["mpd_url"] = video_url

        # Also check for embedCode as fallback
        embed_code = video_details.get("embedCode", "")
        if embed_code and not video_info.get("mpd_url"):
            if embed_code.startswith("http"):
                video_info["video_url"] = embed_code
            else:
                src_match = re.search(r'src=["\'](.*?)["\']', embed_code)
                if src_match:
                    video_info["video_url"] = src_match.group(1)

        # Extract DRM/ClearKey info if available
        drm_type = video_details.get("drmType", "")
        key_id = video_details.get("keyId", "") or video_details.get("kid", "")
        if drm_type and key_id:
            video_info["drm_type"] = drm_type
            video_info["key_id"] = key_id

        # Also check drmDetails
        drm_details = video_details.get("drmDetails", {})
        if drm_details and not video_info.get("drm_type"):
            dt = drm_details.get("drmType", "") or drm_details.get("type", "")
            if dt:
                video_info["drm_type"] = dt
            keys = drm_details.get("keys", [])
            if keys:
                video_info["drm_keys"] = keys
            kid = drm_details.get("keyId", "") or drm_details.get("kid", "")
            if kid:
                video_info["key_id"] = kid

        # Get video ID for reference
        vid = video_details.get("id", "") or video_details.get("_id", "")
        if vid:
            video_info["video_id"] = vid

        # Check videoMapping
        vmap = video_details.get("videoMapping", {})
        if vmap and not video_info.get("mpd_url") and not video_info.get("video_url"):
            for cdn_key in ["cdn", "alisg-cdn", "mux", "cloudfront"]:
                cdn_data = vmap.get(cdn_key, {})
                if isinstance(cdn_data, dict):
                    for ukey in ["url", "videoUrl", "mpdUrl", "m3u8Url"]:
                        if cdn_data.get(ukey):
                            video_info["video_url"] = cdn_data[ukey]
                            break
                elif isinstance(cdn_data, str) and cdn_data.startswith("http"):
                    video_info["video_url"] = cdn_data
                if video_info.get("video_url") or video_info.get("mpd_url"):
                    break

    # Also check for direct url in data
    if not video_info.get("mpd_url") and not video_info.get("video_url"):
        direct_url = data.get("url", "")
        if direct_url and (".mpd" in direct_url or ".m3u8" in direct_url or "cloudfront" in direct_url):
            video_info["video_url"] = direct_url

    return video_info


def format_video_line(topic, video_info, parent_id="", child_id="", video_id=""):
    """Format video data into extractable line with ALL info including IDs"""
    lines = []
    topic_clean = safe_topic(topic)

    if video_info.get("mpd_url"):
        # Append parentId, childId, videoId to the URL
        final_url = append_video_params(video_info['mpd_url'], parent_id, child_id, video_info.get('video_id', video_id))
        line = f"{topic_clean}:{final_url}"
        if video_info.get("drm_type") and video_info.get("key_id"):
            line += f" | DRM:{video_info['drm_type']} | KID:{video_info['key_id']}"
        if video_info.get("drm_keys"):
            line += f" | Keys:{'|'.join(str(k) for k in video_info['drm_keys'])}"
        if video_info.get("video_id"):
            line += f" | VideoID:{video_info['video_id']}"
        lines.append(line)
    elif video_info.get("video_url"):
        # Append parentId, childId, videoId to the URL
        final_url = append_video_params(video_info['video_url'], parent_id, child_id, video_info.get('video_id', video_id))
        line = f"{topic_clean}:{final_url}"
        if video_info.get("drm_type") and video_info.get("key_id"):
            line += f" | DRM:{video_info['drm_type']} | KID:{video_info['key_id']}"
        if video_info.get("video_id"):
            line += f" | VideoID:{video_info['video_id']}"
        lines.append(line)

    return lines


# ===============================================================
# ADVANCED: Extract ParentId, ChildId, VideoId from PW API data
# ===============================================================
def extract_pw_ids(video_details: dict, schedule_data: dict = None, schedule_id: str = "", batch_id: str = "") -> Tuple[str, str, str]:
    """
    Advanced ID extraction from PW API response data.
    Returns: (parent_id, child_id, video_id)
    """
    parent_id = ""
    child_id = ""
    video_id = ""

    if not video_details:
        return parent_id, child_id, video_id

    # video_id: from videoDetails
    video_id = video_details.get('_id') or video_details.get('id') or video_details.get('videoId') or video_details.get('contentId', '')

    # parent_id: Batch ID
    parent_id = batch_id or (schedule_data.get('batchId') if schedule_data else '') or (schedule_data.get('batchSubjectId') if schedule_data else '')

    # child_id: Schedule/Content ID
    child_id = schedule_id or (schedule_data.get('_id') if schedule_data else '') or (schedule_data.get('id') if schedule_data else '') or (schedule_data.get('scheduleId') if schedule_data else '')

    return parent_id, child_id, video_id


def append_video_params(video_url: str, parent_id: str = "", child_id: str = "", video_id: str = "") -> str:
    """Append parentId, childId, videoId to video URL with & separator."""
    if not video_url:
        return video_url

    params = []
    if parent_id and str(parent_id).strip():
        params.append(f"parentId={str(parent_id).strip()}")
    if child_id and str(child_id).strip():
        params.append(f"childId={str(child_id).strip()}")
    if video_id and str(video_id).strip():
        params.append(f"videoId={str(video_id).strip()}")

    if not params:
        return video_url

    # Always use & as the first separator after master.mpd (not ?)
    param_string = '&' + '&'.join(params)
    return video_url + param_string


# ===============================================================
# COMPREHENSIVE: Extract video URL from ALL PW API response formats
# ===============================================================
def extract_comprehensive_video_url(video_details: dict, parent_id: str = "", child_id: str = "", video_id: str = "") -> Tuple[str, str]:
    """Extract video URL and DRM keys from PW videoDetails with ADVANCED IDs - ALL formats."""
    if not video_details:
        return "", ""

    video_url = ""
    drm_info = ""

    # 1. Try direct videoUrl field
    if video_details.get('videoUrl'):
        video_url = video_details['videoUrl']

    # 2. Try videoMapping (most common for newer PW videos)
    video_mapping = video_details.get('videoMapping', {})
    if not video_url and video_mapping:
        if video_mapping.get('mux'):
            mux = video_mapping['mux']
            if isinstance(mux, dict):
                video_url = mux.get('url') or mux.get('playbackId', '')
                if video_url and not video_url.startswith('http'):
                    video_url = f"https://stream.mux.com/{video_url}.m3u8"
            elif isinstance(mux, str):
                video_url = mux
        if not video_url and video_mapping.get('alisg-cdn'):
            cdn = video_mapping['alisg-cdn']
            if isinstance(cdn, dict):
                video_url = cdn.get('url') or cdn.get('videoUrl', '')
            elif isinstance(cdn, str):
                video_url = cdn
        if not video_url and video_mapping.get('cdn'):
            cdn = video_mapping['cdn']
            if isinstance(cdn, dict):
                video_url = cdn.get('url') or cdn.get('videoUrl', '')
            elif isinstance(cdn, str):
                video_url = cdn
        # Generic: iterate all mapping keys
        if not video_url:
            for key, val in video_mapping.items():
                if isinstance(val, dict):
                    for sub_key in ['url', 'videoUrl', 'playbackId', 'mpdUrl', 'm3u8Url', 'dashUrl', 'hlsUrl']:
                        if val.get(sub_key):
                            video_url = val[sub_key]
                            break
                elif isinstance(val, str) and val.startswith('http'):
                    video_url = val
                if video_url:
                    break

    # 3. Try embedCode
    if not video_url and video_details.get('embedCode'):
        embed = video_details['embedCode']
        src_match = re.search(r'src=["\'](.*?)["\']', embed)
        if src_match:
            video_url = src_match.group(1)

    # 4. Try various other fields
    if not video_url:
        for key in ['url', 'playbackUrl', 'streamUrl', 'dashUrl', 'hlsUrl', 'mpdUrl', 'm3u8Url', 'cdnUrl']:
            if video_details.get(key):
                video_url = video_details[key]
                break

    # DRM / ClearKey Extraction
    drm_details = video_details.get('drmDetails') or video_details.get('drm') or {}
    if drm_details:
        drm_type = drm_details.get('drmType', '') or drm_details.get('type', '')
        if drm_type and str(drm_type).lower() == 'clearkey':
            keys_list = []
            if drm_details.get('keys'):
                keys_list = drm_details['keys']
            elif drm_details.get('key_strings'):
                keys_list = drm_details['key_strings']
            elif drm_details.get('keyId') and drm_details.get('key'):
                keys_list = [f"{drm_details['keyId']}:{drm_details['key']}"]
            elif drm_details.get('kid') and drm_details.get('key'):
                keys_list = [f"{drm_details['kid']}:{drm_details['key']}"]

            if keys_list:
                formatted_keys = []
                for k in keys_list:
                    if isinstance(k, str):
                        if k.startswith('--key'):
                            formatted_keys.append(k.replace('--key ', ''))
                        else:
                            formatted_keys.append(k)
                    elif isinstance(k, dict):
                        # Handle dict format: {kid: ..., key: ...}
                        kid = k.get('kid', k.get('keyId', ''))
                        key_val = k.get('key', k.get('k', ''))
                        if kid and key_val:
                            formatted_keys.append(f"{kid}:{key_val}")
                drm_key_str = ' | '.join(formatted_keys)
                drm_info = f" | DRM: ClearKey | Key: {drm_key_str}"

    # Add protocol if missing
    if video_url and not video_url.startswith('http'):
        video_url = f"https:{video_url}"

    # Append parentId, childId, videoId to URL
    if video_url and (parent_id or child_id or video_id):
        video_url = append_video_params(video_url, parent_id, child_id, video_id)

    return video_url, drm_info


# ===============================================================
# CRITICAL FIX: Extract video URL from content list item directly
# This works for non-purchased batches where schedule-details fails
# ===============================================================
def extract_video_from_content_item(item: dict) -> Tuple[str, str]:
    """
    Extract video URL directly from a content list item.
    This is a fallback when schedule-details endpoint is not accessible.
    """
    video_url = ""
    drm_info = ""

    if not item:
        return video_url, drm_info

    # 1. Check direct url field
    url = item.get('url', '')
    if url and ('.mpd' in url or '.m3u8' in url or 'cloudfront' in url or 'video' in url):
        video_url = url

    # 2. Check videoDetails at item level
    if not video_url:
        vd = item.get('videoDetails', {})
        if vd:
            video_url, drm_info = extract_comprehensive_video_url(vd)

    # 3. Check assignment/video at item level
    if not video_url:
        assignment = item.get('assignment', {})
        if assignment:
            vid_url = assignment.get('videoUrl', assignment.get('url', ''))
            if vid_url and ('.mpd' in vid_url or '.m3u8' in vid_url or 'cloudfront' in vid_url):
                video_url = vid_url

    # 4. Check content url
    if not video_url:
        content_url = item.get('contentUrl', item.get('content_url', ''))
        if content_url and ('.mpd' in content_url or '.m3u8' in content_url):
            video_url = content_url

    # 5. Check for any field that looks like a video URL
    if not video_url:
        for key, val in item.items():
            if isinstance(val, str) and val.startswith('http') and ('.mpd' in val or '.m3u8' in val):
                video_url = val
                break

    return video_url, drm_info


# ===============================================================
# DEDUPLICATION FIX: Proper handling of colons in titles
# ===============================================================
def split_title_and_url(line: str) -> Tuple[str, str]:
    """
    Split a line into (title, url) by finding the URL (starts with http).
    This properly handles titles that contain colons.
    """
    if not line or ':' not in line:
        return line, ""

    # Find the position where URL starts (http:// or https://)
    http_match = re.search(r'https?://', line)
    if http_match:
        url_start = http_match.start()
        title = line[:url_start].rstrip(':').strip()
        url_part = line[url_start:].strip()
        return title, url_part
    else:
        # No URL found, treat entire line as title
        return line.strip(), ""


class ContentDeduplicator:
    """
    Advanced deduplicator that removes entries with the SAME URL AND SAME TITLE.
    This prevents DPP notes from appearing twice.

    Logic:
    - For each line, extract (normalized_url, normalized_title) using split_title_and_url
    - If (url, title) pair was already seen, skip it
    - Also track URLs alone for extra safety
    """
    def __init__(self):
        self.seen = set()  # Set of (normalized_url, normalized_title) tuples
        self.seen_urls = set()  # Also track URLs alone for extra safety

    def _normalize_url(self, url: str) -> str:
        """Normalize URL for comparison"""
        if not url:
            return ""
        # Remove query params and trailing slashes
        return url.split('?')[0].rstrip('/').lower()

    def _normalize_title(self, title: str) -> str:
        """Normalize title for comparison"""
        if not title:
            return ""
        return str(title).strip().lower()

    def add_and_check_unique(self, line: str) -> bool:
        """
        Add entry and return True if unique, False if duplicate.
        """
        if not line:
            return False

        title, url_part = split_title_and_url(line)

        # Extract just the URL part (remove DRM info etc.)
        url = url_part
        if ' | ' in url_part:
            url = url_part.split(' | ')[0].strip()

        # Ensure both are strings (fix unhashable type: dict error)
        title = str(title) if title else ""
        url = str(url) if url else ""

        normalized_url = self._normalize_url(url)
        normalized_title = self._normalize_title(title)

        # Create hashable key - both parts MUST be strings
        key = (normalized_url, normalized_title)

        # Check both (url+title) pair AND url alone
        if key in self.seen:
            return False
        if normalized_url in self.seen_urls:
            return False

        self.seen.add(key)
        if normalized_url:
            self.seen_urls.add(normalized_url)
        return True

    def filter_unique(self, content_list: List[str]) -> List[str]:
        """Filter a list of content lines, keeping only unique (URL+Title) entries."""
        if not content_list:
            return []
        unique = []
        for line in content_list:
            if self.add_and_check_unique(line):
                unique.append(line)
        return unique

    def is_duplicate(self, title: str, url: str) -> bool:
        """Check if a specific (title, url) pair is a duplicate."""
        norm_url = self._normalize_url(str(url))
        norm_title = self._normalize_title(str(title))
        key = (norm_url, norm_title)
        if key in self.seen or norm_url in self.seen_urls:
            return True
        self.seen.add(key)
        if norm_url:
            self.seen_urls.add(norm_url)
        return False


def deduplicate_by_url_and_title(content_list: List[str]) -> List[str]:
    """Remove duplicate entries based on BOTH URL AND TITLE."""
    dedup = ContentDeduplicator()
    return dedup.filter_unique(content_list)


# ===============================================================
# LOGGING: Send extraction info to log channel
# ===============================================================
_log_channel_resolved = False  # cache so we only force-resolve the peer once per run


async def _ensure_log_channel_resolved(bot):
    """Force Pyrogram to cache the log channel's peer info.

    NOTE (FIXED BUG): the log channel ID itself was always correct
    (-1003597599758, confirmed in the Telegram app) and the bot IS an admin
    there - but Pyrogram raised 'Peer id invalid' / 'ID not found' anyway.
    This happens because Pyrogram's local SQLite peer cache only learns
    about a chat when it receives an *update* from it (a new message, a
    join event, etc.) - simply being an admin doesn't populate the cache.
    Calling get_chat() once forces Telegram to resolve + cache the peer via
    channels.getChannels, which fixes resolve_peer() for every send/copy
    call afterwards without requiring any message to exist there first.
    """
    global _log_channel_resolved
    if _log_channel_resolved:
        return True
    try:
        await bot.get_chat(LOG_CHANNEL)
        _log_channel_resolved = True
        logging.info(f"Log channel {LOG_CHANNEL} peer resolved & cached successfully")
        return True
    except Exception as e:
        logging.error(f"Could not resolve log channel {LOG_CHANNEL} peer: {e}", exc_info=True)
        return False


async def log_extraction_to_channel(bot, user_id, user_name, user_username, batch_name, token_preview, file_types, message_ids=None, chat_id=None):
    """Log extraction details to private log channel + forward files.

    NOTE (FIXED BUG #1): Telegram channel/supergroup IDs are ALWAYS negative
    (e.g. -1003597599758). An earlier guard `LOG_CHANNEL <= 0: return` was
    True for every valid channel ID -> the function always exited
    immediately. Fixed to only treat 0/unset as "not configured".

    NOTE (FIXED BUG #2): `parse_mode="markdown"` (lowercase string) is not
    accepted by this Pyrogram version - it raised
    ValueError: Invalid parse mode "markdown", which silently killed the
    text log message every single time. Fixed to use the proper
    pyrogram.enums.ParseMode.MARKDOWN enum.

    NOTE (FIXED BUG #3): see _ensure_log_channel_resolved() above for the
    "Peer id invalid" / forwarding failure fix.
    """
    try:
        if not LOG_CHANNEL or LOG_CHANNEL == 0:
            logging.warning(f"Log channel not configured (value: {LOG_CHANNEL})")
            return

        # Make sure Pyrogram has this channel cached before we try to use it
        if not await _ensure_log_channel_resolved(bot):
            logging.warning("Skipping log/forward this run - log channel peer could not be resolved")
            return

        log_text = (
            f"📊 **Extraction Logged**\n\n"
            f"👤 User ID: `{user_id}`\n"
            f"👤 Name: {user_name or 'N/A'}\n"
            f"👤 Username: @{user_username if user_username else 'N/A'}\n"
            f"📚 Batch: `{batch_name}`\n"
            f"🔐 Token: `{token_preview}`\n"
            f"📄 Files: {', '.join(file_types)}\n"
            f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}"
        )

        logging.info(f"Attempting to log to channel {LOG_CHANNEL}: {log_text}...")
        try:
            await bot.send_message(LOG_CHANNEL, log_text, parse_mode=ParseMode.MARKDOWN)
            logging.info(f"Successfully logged extraction to channel {LOG_CHANNEL}")
        except FloodWait as e:
            logging.warning(f"FloodWait on log text, sleeping {e.value}s")
            await asyncio.sleep(e.value)
            await bot.send_message(LOG_CHANNEL, log_text, parse_mode=ParseMode.MARKDOWN)
        except (ValueError, KeyError) as e:
            # Peer cache may have gone stale (e.g. bot re-added) - force a
            # fresh resolve and retry once before giving up on the text log.
            logging.warning(f"Peer error sending log text, re-resolving and retrying: {e}")
            global _log_channel_resolved
            _log_channel_resolved = False
            if await _ensure_log_channel_resolved(bot):
                try:
                    await bot.send_message(LOG_CHANNEL, log_text, parse_mode=ParseMode.MARKDOWN)
                    logging.info(f"Successfully logged extraction to channel {LOG_CHANNEL} after re-resolve")
                except Exception as e2:
                    logging.error(f"Still could not send log text to {LOG_CHANNEL}: {e2}", exc_info=True)
        except Exception as e:
            # Don't let a failed text log block file forwarding below
            logging.error(f"Could not send log text to {LOG_CHANNEL}: {e}", exc_info=True)

        # Forward all sent files to log channel
        if message_ids and chat_id:
            forwarded = 0
            for msg_id in message_ids:
                try:
                    await bot.copy_message(LOG_CHANNEL, chat_id, msg_id)
                    forwarded += 1
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    logging.warning(f"FloodWait while forwarding msg {msg_id}, sleeping {e.value}s")
                    await asyncio.sleep(e.value)
                    try:
                        await bot.copy_message(LOG_CHANNEL, chat_id, msg_id)
                        forwarded += 1
                    except Exception as e2:
                        logging.warning(f"Retry failed forwarding message {msg_id} to log channel: {e2}")
                except (ValueError, KeyError) as e:
                    # Stale peer cache - re-resolve once and retry this file
                    logging.warning(f"Peer error forwarding msg {msg_id}, re-resolving and retrying: {e}")
                    _log_channel_resolved = False
                    if await _ensure_log_channel_resolved(bot):
                        try:
                            await bot.copy_message(LOG_CHANNEL, chat_id, msg_id)
                            forwarded += 1
                        except Exception as e2:
                            logging.warning(f"Still failed forwarding message {msg_id} after re-resolve: {e2}")
                except Exception as e:
                    logging.warning(f"Failed to forward message {msg_id} to log channel: {e}", exc_info=True)
            logging.info(f"Forwarded {forwarded}/{len(message_ids)} files to log channel {LOG_CHANNEL}")
    except Exception as e:
        logging.error(f"Failed to log extraction to channel {LOG_CHANNEL}: {e}", exc_info=True)


# ===============================================================
# HTML GENERATION from JSON data
# ===============================================================
def _html_escape(text: str) -> str:
    """Escape text for safe insertion into HTML body content."""
    text = "" if text is None else str(text)
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _js_attr_escape(text: str) -> str:
    """Escape text for safe insertion inside a single-quoted JS string that
    itself sits inside an HTML onclick="..." attribute.

    FIXED BUG: the previous template interpolated raw titles/URLs straight
    into onclick="openVideoPlayer('TITLE', 'URL')". Any apostrophe, quote,
    backslash, or newline in a title (e.g. "Newton's Laws") or in a token-
    bearing CDN URL silently broke the JS string literal, which made the
    Play / View / Copy buttons do nothing when clicked. Escaping backslash,
    single quote, double quote and newlines here, then also HTML-escaping
    the result (because it sits inside an HTML attribute) fixes every
    Play/Download/Copy/View button across both the per-chapter lists and
    the "All Videos" / "All PDFs" grids.
    """
    text = "" if text is None else str(text)
    text = (
        text.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace('"', '\\"')
            .replace("\n", " ")
            .replace("\r", " ")
    )
    # The result still needs to be HTML-attribute-safe since it's inside onclick="..."
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def generate_html_from_json(batch_name: str, json_data: dict, token: str = "") -> str:
    """Generate luxury HTML study page from JSON content data with inline video player & PDF viewer."""
    
    # Collect all unique subjects, chapters, videos and PDFs
    nav_subjects = []
    all_videos = []
    all_pdfs = []
    subject_sections = []
    
    for batch_key, subjects in json_data.items():
        for subject_name, chapters in subjects.items():
            if subject_name in ("date", "total_schedules"):
                continue
            nav_subjects.append(subject_name)
            
            chapter_sections = []
            for chapter_name, items in chapters.items():
                if not isinstance(items, list):
                    continue
                
                video_items = []
                pdf_items = []
                other_items = []
                
                for item in items:
                    title = item.get('title', 'Untitled')
                    url = item.get('url', '#')
                    item_type = item.get('type', 'file').lower()

                    # Pre-computed safe variants used everywhere in the template:
                    #   *_safe   -> HTML-escaped, for visible text content
                    #   *_js     -> JS+HTML escaped, for use inside onclick='...'
                    entry = {
                        'title': title,
                        'url': url,
                        'title_safe': _html_escape(title),
                        'title_js': _js_attr_escape(title),
                        'url_js': _js_attr_escape(url),
                    }

                    if any(v in item_type for v in ['video', 'mpd', 'm3u8']):
                        video_items.append(entry)
                        all_videos.append({**entry, 'subject': subject_name, 'chapter': chapter_name})
                    elif 'pdf' in item_type:
                        pdf_items.append(entry)
                        all_pdfs.append({**entry, 'subject': subject_name, 'chapter': chapter_name})
                    else:
                        other_items.append({**entry, 'type': item_type})
                
                chapter_sections.append({
                    'name': chapter_name,
                    'name_safe': _html_escape(chapter_name),
                    'videos': video_items,
                    'pdfs': pdf_items,
                    'others': other_items
                })
            
            subject_sections.append({
                'name': subject_name,
                'name_safe': _html_escape(subject_name),
                'chapters': chapter_sections
            })
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{batch_name} - Study Hub</title>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <!-- FIXED: a plain <video><source type="application/dash+xml"> tag CANNOT
         play .mpd/.m3u8 streaming manifests in any browser - that's why
         videos never played. dash.js + hls.js add real MSE-based playback
         for DASH (.mpd) and HLS (.m3u8) sources respectively. -->
    <script src="https://cdn.dashjs.org/latest/dash.all.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"></script>
    <style>
        :root {{
            --primary: #ff6b35;
            --primary-dark: #e55a2b;
            --secondary: #1a1a2e;
            --accent: #16213e;
            --surface: #0f0f23;
            --card: #1a1a2e;
            --text: #f0f0f0;
            --text-muted: #a0a0b0;
            --border: #2a2a4a;
            --gold: #d4a574;
            --success: #4ecdc4;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Inter', sans-serif; background: var(--surface); color: var(--text); min-height: 100vh; }}
        
        /* ===== NAVIGATION ===== */
        .navbar {{
            position: fixed; top: 0; left: 0; right: 0; z-index: 1000;
            background: rgba(15, 15, 35, 0.95); backdrop-filter: blur(20px);
            border-bottom: 1px solid var(--border); padding: 0 20px;
        }}
        .nav-container {{
            max-width: 1400px; margin: 0 auto; display: flex;
            align-items: center; justify-content: space-between; height: 60px;
        }}
        .nav-logo {{
            font-family: 'Playfair Display', serif; font-size: 1.4em;
            font-weight: 700; color: var(--primary);
        }}
        .nav-links {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .nav-links a {{
            color: var(--text-muted); text-decoration: none; padding: 6px 14px;
            border-radius: 20px; font-size: 0.8em; font-weight: 500;
            transition: all 0.3s; border: 1px solid transparent;
        }}
        .nav-links a:hover {{
            color: var(--primary); border-color: var(--primary);
            background: rgba(255,107,53,0.1);
        }}
        .nav-links a.active {{ color: var(--primary); background: rgba(255,107,53,0.15); border-color: var(--primary); }}
        
        /* ===== HEADER ===== */
        .main-content {{ padding-top: 60px; }}
        .header-hero {{
            background: linear-gradient(135deg, var(--secondary) 0%, var(--accent) 100%);
            padding: 60px 20px; text-align: center; position: relative; overflow: hidden;
        }}
        .header-hero::before {{
            content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%;
            background: radial-gradient(circle, rgba(255,107,53,0.08) 0%, transparent 60%);
            animation: pulse 8s ease-in-out infinite;
        }}
        @keyframes pulse {{ 0%,100% {{ transform: scale(1); }} 50% {{ transform: scale(1.1); }} }}
        .header-hero h1 {{
            font-family: 'Playfair Display', serif; font-size: 2.8em;
            margin-bottom: 12px; position: relative; z-index: 1;
            background: linear-gradient(135deg, #fff 0%, var(--gold) 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }}
        .header-hero .subtitle {{
            color: var(--text-muted); font-size: 1.05em; position: relative; z-index: 1;
        }}
        .made-by {{
            display: inline-flex; align-items: center; gap: 8px;
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            color: white; padding: 10px 24px; border-radius: 30px;
            font-weight: 600; font-size: 0.85em; margin-top: 20px;
            text-decoration: none; position: relative; z-index: 1;
            box-shadow: 0 4px 15px rgba(255,107,53,0.4);
            transition: transform 0.3s, box-shadow 0.3s;
        }}
        .made-by:hover {{ transform: translateY(-2px); box-shadow: 0 6px 25px rgba(255,107,53,0.5); }}
        .made-by i {{ font-size: 1.1em; }}
        
        /* ===== CONTAINER ===== */
        .container {{ max-width: 1400px; margin: 0 auto; padding: 30px 20px; }}
        
        /* ===== SECTION TABS ===== */
        .section-tabs {{
            display: flex; gap: 10px; margin-bottom: 30px; flex-wrap: wrap;
            position: sticky; top: 60px; z-index: 100; padding: 15px 0;
            background: var(--surface);
        }}
        .section-tab {{
            padding: 10px 22px; border-radius: 10px; cursor: pointer;
            font-weight: 600; font-size: 0.9em; border: 1px solid var(--border);
            background: var(--card); color: var(--text-muted);
            transition: all 0.3s; display: flex; align-items: center; gap: 8px;
        }}
        .section-tab:hover {{ border-color: var(--primary); color: var(--text); }}
        .section-tab.active {{
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            color: white; border-color: var(--primary); box-shadow: 0 4px 15px rgba(255,107,53,0.3);
        }}
        .section-tab .count {{ background: rgba(255,255,255,0.2); padding: 2px 8px; border-radius: 10px; font-size: 0.75em; }}
        
        /* ===== STATS BAR ===== */
        .stats-bar {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px; margin-bottom: 30px;
        }}
        .stat-card {{
            background: var(--card); border: 1px solid var(--border);
            border-radius: 12px; padding: 20px; text-align: center;
            transition: transform 0.3s;
        }}
        .stat-card:hover {{ transform: translateY(-3px); border-color: var(--primary); }}
        .stat-card i {{ font-size: 1.5em; color: var(--primary); margin-bottom: 8px; }}
        .stat-card .number {{ font-size: 1.6em; font-weight: 700; color: var(--text); }}
        .stat-card .label {{ font-size: 0.8em; color: var(--text-muted); margin-top: 4px; }}
        
        /* ===== SUBJECT SECTION ===== */
        .subject-section {{
            background: var(--card); border: 1px solid var(--border);
            border-radius: 16px; margin-bottom: 25px; overflow: hidden;
            scroll-margin-top: 130px;
        }}
        .subject-header {{
            background: linear-gradient(135deg, var(--secondary) 0%, var(--accent) 100%);
            padding: 22px 28px; display: flex; align-items: center; gap: 15px;
            cursor: pointer; border-bottom: 2px solid var(--primary);
        }}
        .subject-header i {{ font-size: 1.4em; color: var(--primary); }}
        .subject-header h2 {{ font-size: 1.3em; font-weight: 700; flex: 1; }}
        .subject-header .toggle-icon {{
            width: 32px; height: 32px; border-radius: 50%;
            background: rgba(255,107,53,0.15); display: flex;
            align-items: center; justify-content: center;
            transition: transform 0.3s; color: var(--primary);
        }}
        .subject-header.collapsed .toggle-icon {{ transform: rotate(-90deg); }}
        .subject-content {{ padding: 20px; }}
        .subject-content.hidden {{ display: none; }}
        
        /* ===== CHAPTER SECTION ===== */
        .chapter-section {{
            background: rgba(255,255,255,0.03); border: 1px solid var(--border);
            border-radius: 12px; margin-bottom: 15px; overflow: hidden;
        }}
        .chapter-header {{
            padding: 15px 20px; background: rgba(255,107,53,0.05);
            display: flex; align-items: center; gap: 10px;
            cursor: pointer; border-bottom: 1px solid var(--border);
        }}
        .chapter-header i {{ color: var(--gold); }}
        .chapter-header h3 {{ font-size: 1.05em; font-weight: 600; flex: 1; color: var(--gold); }}
        .chapter-header .count {{
            background: rgba(212,165,116,0.15); color: var(--gold);
            padding: 3px 10px; border-radius: 10px; font-size: 0.75em;
        }}
        .chapter-content {{ padding: 15px; }}
        .chapter-content.hidden {{ display: none; }}
        
        /* ===== CONTENT ITEM ===== */
        .content-item {{
            display: flex; align-items: center; gap: 12px;
            padding: 12px 16px; margin-bottom: 8px;
            background: rgba(255,255,255,0.04); border-radius: 10px;
            border: 1px solid transparent; transition: all 0.3s;
        }}
        .content-item:hover {{
            border-color: var(--primary); background: rgba(255,107,53,0.05);
            transform: translateX(4px);
        }}
        .content-item .icon {{
            width: 36px; height: 36px; border-radius: 10px;
            display: flex; align-items: center; justify-content: center;
            flex-shrink: 0; font-size: 0.95em;
        }}
        .content-item.video .icon {{ background: rgba(255,107,53,0.15); color: var(--primary); }}
        .content-item.pdf .icon {{ background: rgba(78,205,196,0.15); color: var(--success); }}
        .content-item.file .icon {{ background: rgba(212,165,116,0.15); color: var(--gold); }}
        .content-item .title {{ flex: 1; font-size: 0.88em; font-weight: 500; color: var(--text); }}
        .content-item .actions {{ display: flex; gap: 6px; }}
        .btn {{
            padding: 7px 14px; border-radius: 8px; font-size: 0.78em;
            font-weight: 600; cursor: pointer; border: none;
            transition: all 0.3s; display: inline-flex; align-items: center; gap: 5px;
            text-decoration: none;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
            color: white; box-shadow: 0 3px 10px rgba(255,107,53,0.3);
        }}
        .btn-primary:hover {{ transform: translateY(-2px); box-shadow: 0 5px 15px rgba(255,107,53,0.4); }}
        .btn-secondary {{
            background: var(--border); color: var(--text-muted);
        }}
        .btn-secondary:hover {{ background: var(--primary); color: white; }}
        .btn-success {{
            background: rgba(78,205,196,0.15); color: var(--success); border: 1px solid rgba(78,205,196,0.3);
        }}
        .btn-success:hover {{ background: var(--success); color: var(--secondary); }}
        
        /* ===== VIDEO PLAYER MODAL ===== */
        .modal-overlay {{
            display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.92); z-index: 10000;
            align-items: center; justify-content: center; padding: 20px;
        }}
        .modal-overlay.active {{ display: flex; }}
        .modal-content {{
            width: 100%; max-width: 950px; background: var(--secondary);
            border-radius: 16px; overflow: hidden; border: 1px solid var(--border);
            box-shadow: 0 25px 80px rgba(0,0,0,0.7);
        }}
        .modal-header {{
            padding: 16px 22px; display: flex; align-items: center;
            justify-content: space-between; background: var(--accent);
            border-bottom: 1px solid var(--border);
        }}
        .modal-header h3 {{ font-size: 1em; font-weight: 600; flex: 1; margin-right: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
        .modal-close {{
            width: 34px; height: 34px; border-radius: 50%; border: none;
            background: rgba(255,255,255,0.1); color: var(--text);
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            transition: all 0.3s;
        }}
        .modal-close:hover {{ background: #e74c3c; color: white; }}
        
        /* Orange Video Player */
        .video-player-container {{
            position: relative; background: #000;
            aspect-ratio: 16/9; display: flex;
            align-items: center; justify-content: center;
        }}
        .video-player-container video {{
            width: 100%; height: 100%; object-fit: contain;
        }}
        .video-placeholder {{
            text-align: center; padding: 40px;
        }}
        .video-placeholder i {{
            font-size: 4em; color: var(--primary); margin-bottom: 20px;
            animation: float 3s ease-in-out infinite;
        }}
        @keyframes float {{ 0%,100% {{ transform: translateY(0); }} 50% {{ transform: translateY(-10px); }} }}
        .video-placeholder p {{ color: var(--text-muted); font-size: 0.9em; margin-bottom: 10px; }}
        .video-placeholder .url-display {{
            background: rgba(255,107,53,0.1); border: 1px solid var(--primary);
            border-radius: 8px; padding: 10px 16px; font-size: 0.78em;
            color: var(--primary); font-family: monospace; word-break: break-all;
            max-width: 80%; margin: 10px auto;
        }}
        .video-placeholder .url-note {{
            font-size: 0.75em; color: var(--text-muted); margin-top: 8px;
        }}
        
        /* Custom Video Controls */
        .video-controls {{
            display: flex; align-items: center; gap: 10px;
            padding: 12px 18px; background: var(--accent);
            border-top: 2px solid var(--primary);
        }}
        .vc-btn {{
            width: 36px; height: 36px; border-radius: 8px; border: none;
            background: rgba(255,107,53,0.15); color: var(--primary);
            cursor: pointer; display: flex; align-items: center;
            justify-content: center; transition: all 0.3s; font-size: 0.85em;
        }}
        .vc-btn:hover {{ background: var(--primary); color: white; }}
        .vc-btn.play {{ width: 42px; height: 42px; border-radius: 50%; background: var(--primary); color: white; }}
        .vc-btn.play:hover {{ background: var(--primary-dark); transform: scale(1.05); }}
        .vc-seekbar {{
            flex: 1; height: 5px; border-radius: 3px;
            background: rgba(255,255,255,0.1); cursor: pointer; position: relative;
        }}
        .vc-seekbar-fill {{
            height: 100%; border-radius: 3px;
            background: linear-gradient(90deg, var(--primary), var(--gold));
            width: 0%; transition: width 0.2s;
        }}
        .vc-time {{ font-size: 0.75em; color: var(--text-muted); font-variant-numeric: tabular-nums; min-width: 85px; text-align: center; }}
        .vc-speed {{
            padding: 5px 10px; border-radius: 6px; border: 1px solid var(--border);
            background: transparent; color: var(--text-muted); font-size: 0.78em;
            cursor: pointer; outline: none;
        }}
        .vc-speed:focus {{ border-color: var(--primary); color: var(--primary); }}
        .vc-madeby {{
            margin-left: auto; padding: 5px 12px; border-radius: 6px;
            background: rgba(255,107,53,0.15); color: var(--primary);
            font-size: 0.7em; font-weight: 600; text-decoration: none;
            transition: all 0.3s;
        }}
        .vc-madeby:hover {{ background: var(--primary); color: white; }}
        
        /* ===== PDF VIEWER MODAL ===== */
        .pdf-viewer-container {{
            width: 100%; height: 75vh; background: #2a2a3a;
        }}
        .pdf-viewer-container iframe {{
            width: 100%; height: 100%; border: none;
        }}
        
        /* ===== GRID LAYOUTS ===== */
        .grid-2 {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
            gap: 12px;
        }}
        .grid-item {{
            background: rgba(255,255,255,0.04); border: 1px solid var(--border);
            border-radius: 10px; padding: 14px; transition: all 0.3s;
        }}
        .grid-item:hover {{ border-color: var(--primary); transform: translateY(-2px); }}
        .grid-item .g-title {{ font-size: 0.82em; font-weight: 500; margin-bottom: 10px; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; }}
        .grid-item .g-actions {{ display: flex; gap: 6px; }}
        .grid-item .g-actions .btn {{ flex: 1; justify-content: center; padding: 7px 0; font-size: 0.72em; }}
        
        /* ===== FOOTER ===== */
        .footer {{
            text-align: center; padding: 40px 20px;
            border-top: 1px solid var(--border); margin-top: 40px;
        }}
        .footer p {{ color: var(--text-muted); font-size: 0.85em; }}
        .footer a {{ color: var(--primary); text-decoration: none; font-weight: 600; }}
        .footer .footer-logo {{ font-family: 'Playfair Display', serif; font-size: 1.3em; color: var(--primary); margin-bottom: 10px; }}
        
        /* ===== RESPONSIVE ===== */
        @media (max-width: 768px) {{
            .nav-links {{ display: none; }}
            .header-hero h1 {{ font-size: 1.8em; }}
            .video-controls {{ flex-wrap: wrap; gap: 6px; }}
            .vc-madeby {{ display: none; }}
        }}
        
        /* ===== SCROLLBAR ===== */
        ::-webkit-scrollbar {{ width: 8px; }}
        ::-webkit-scrollbar-track {{ background: var(--surface); }}
        ::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: var(--primary); }}
    </style>
</head>
<body>
    <!-- Navigation -->
    <nav class="navbar">
        <div class="nav-container">
            <div class="nav-logo"><i class="fas fa-graduation-cap"></i> {batch_name[:25]}{'...' if len(batch_name) > 25 else ''}</div>
            <div class="nav-links">
                <a href="#home" class="active"><i class="fas fa-home"></i> Home</a>
                <a href="#subjects"><i class="fas fa-book"></i> Subjects ({len(nav_subjects)})</a>
                <a href="#videos"><i class="fas fa-play-circle"></i> Videos ({len(all_videos)})</a>
                <a href="#pdfs"><i class="fas fa-file-pdf"></i> PDFs ({len(all_pdfs)})</a>
            </div>
        </div>
    </nav>
    
    <div class="main-content">
        <!-- Header -->
        <div class="header-hero" id="home">
            <h1><i class="fas fa-crown"></i> {batch_name}</h1>
            <p class="subtitle">Your Complete Study Material Hub</p>
            <a href="https://t.me/TeamCinderella" target="_blank" class="made-by">
                <i class="fab fa-telegram"></i> MADE BY: 𝐓𝐞𝐚𝐦 𝐂𝐢𝐧𝐝𝐞𝐫𝐞𝐥𝐥𝐚
            </a>
        </div>
        
        <div class="container">
            <!-- Stats Bar -->
            <div class="stats-bar">
                <div class="stat-card">
                    <i class="fas fa-book"></i>
                    <div class="number">{len(nav_subjects)}</div>
                    <div class="label">Subjects</div>
                </div>
                <div class="stat-card">
                    <i class="fas fa-play-circle"></i>
                    <div class="number">{len(all_videos)}</div>
                    <div class="label">Videos</div>
                </div>
                <div class="stat-card">
                    <i class="fas fa-file-pdf"></i>
                    <div class="number">{len(all_pdfs)}</div>
                    <div class="label">PDFs</div>
                </div>
                <div class="stat-card">
                    <i class="fas fa-layer-group"></i>
                    <div class="number">{sum(len(s['chapters']) for s in subject_sections)}</div>
                    <div class="label">Chapters</div>
                </div>
            </div>
            
            <!-- Section Tabs -->
            <div class="section-tabs">
                <div class="section-tab active" onclick="showSection('subjects')"><i class="fas fa-book"></i> All Subjects <span class="count">{len(nav_subjects)}</span></div>
                <div class="section-tab" onclick="showSection('videos')"><i class="fas fa-play-circle"></i> All Videos <span class="count">{len(all_videos)}</span></div>
                <div class="section-tab" onclick="showSection('pdfs')"><i class="fas fa-file-pdf"></i> All PDFs <span class="count">{len(all_pdfs)}</span></div>
            </div>
            
            <!-- Subjects Section -->
            <div id="subjects-section" class="tab-content">
"""
    
    # Build subject sections
    for idx, subj in enumerate(subject_sections):
        subj_id = f"subj-{idx}"
        total_vids = sum(len(c['videos']) for c in subj['chapters'])
        total_pdfs = sum(len(c['pdfs']) for c in subj['chapters'])
        
        html += f"""                <div class="subject-section" id="{subj_id}">
                    <div class="subject-header" onclick="toggleChapter(this)">
                        <i class="fas fa-book-open"></i>
                        <h2>{subj['name_safe']}</h2>
                        <span style="color: var(--text-muted); font-size: 0.8em;">
                            <i class="fas fa-play-circle"></i> {total_vids} &nbsp;
                            <i class="fas fa-file-pdf"></i> {total_pdfs} &nbsp;
                            <i class="fas fa-layer-group"></i> {len(subj['chapters'])}
                        </span>
                        <span class="toggle-icon"><i class="fas fa-chevron-down"></i></span>
                    </div>
                    <div class="subject-content">
"""
        
        for cidx, ch in enumerate(subj['chapters']):
            ch_id = f"{subj_id}-ch-{cidx}"
            total_items = len(ch['videos']) + len(ch['pdfs']) + len(ch['others'])
            
            html += f"""                        <div class="chapter-section" id="{ch_id}">
                            <div class="chapter-header" onclick="toggleContent(this)">
                                <i class="fas fa-folder-open"></i>
                                <h3>{ch['name_safe']}</h3>
                                <span class="count">{total_items} items</span>
                                <span class="toggle-icon" style="width:26px;height:26px;font-size:0.8em;"><i class="fas fa-chevron-down"></i></span>
                            </div>
                            <div class="chapter-content">
"""
            # Videos
            if ch['videos']:
                html += """                                <div style="margin-bottom:12px;">
                                    <h4 style="color: var(--primary); font-size: 0.85em; margin-bottom: 10px;"><i class="fas fa-play-circle"></i> Videos ({len(ch['videos'])})</h4>
""".replace("{len(ch['videos'])}", str(len(ch['videos'])))
                for vidx, vid in enumerate(ch['videos']):
                    html += f"""                                    <div class="content-item video">
                                        <div class="icon"><i class="fas fa-play"></i></div>
                                        <span class="title">{vid['title_safe']}</span>
                                        <div class="actions">
                                            <button class="btn btn-primary" onclick="openVideoPlayer('{vid['title_js']}', '{vid['url_js']}')"><i class="fas fa-play"></i> Play</button>
                                            <button class="btn btn-secondary" onclick="copyToClipboard('{vid['url_js']}')"><i class="fas fa-copy"></i></button>
                                        </div>
                                    </div>
"""
                html += """                                </div>
"""
            
            # PDFs
            if ch['pdfs']:
                html += """                                <div style="margin-bottom:12px;">
                                    <h4 style="color: var(--success); font-size: 0.85em; margin-bottom: 10px;"><i class="fas fa-file-pdf"></i> PDFs ({len(ch['pdfs'])})</h4>
""".replace("{len(ch['pdfs'])}", str(len(ch['pdfs'])))
                for pdf in ch['pdfs']:
                    html += f"""                                    <div class="content-item pdf">
                                        <div class="icon"><i class="fas fa-file-pdf"></i></div>
                                        <span class="title">{pdf['title_safe']}</span>
                                        <div class="actions">
                                            <button class="btn btn-success" onclick="openPdfViewer('{pdf['title_js']}', '{pdf['url_js']}')"><i class="fas fa-eye"></i> View</button>
                                            <button class="btn btn-secondary" onclick="copyToClipboard('{pdf['url_js']}')"><i class="fas fa-copy"></i></button>
                                        </div>
                                    </div>
"""
                html += """                                </div>
"""
            
            # Others
            if ch['others']:
                html += """                                <div>
                                    <h4 style="color: var(--gold); font-size: 0.85em; margin-bottom: 10px;"><i class="fas fa-file"></i> Files ({len(ch['others'])})</h4>
""".replace("{len(ch['others'])}", str(len(ch['others'])))
                for oth in ch['others']:
                    html += f"""                                    <div class="content-item file">
                                        <div class="icon"><i class="fas fa-file-alt"></i></div>
                                        <span class="title">{oth['title_safe']}</span>
                                        <div class="actions">
                                            <a href="{oth['url']}" target="_blank" class="btn btn-secondary"><i class="fas fa-download"></i> Open</a>
                                            <button class="btn btn-secondary" onclick="copyToClipboard('{oth['url_js']}')"><i class="fas fa-copy"></i></button>
                                        </div>
                                    </div>
"""
                html += """                                </div>
"""
            
            html += """                            </div>
                        </div>
"""
        
        html += """                    </div>
                </div>
"""
    
    html += """            </div>
            
            <!-- All Videos Section -->
            <div id="videos-section" class="tab-content" style="display:none;">
                <div class="subject-section">
                    <div class="subject-header" style="cursor: default;">
                        <i class="fas fa-play-circle"></i>
                        <h2>All Videos ({len(all_videos)})</h2>
                    </div>
                    <div class="subject-content">
                        <div class="grid-2">
""".replace("{len(all_videos)}", str(len(all_videos)))
    
    for vid in all_videos:
        html += f"""                            <div class="grid-item">
                                <div class="g-title"><i class="fas fa-play-circle" style="color: var(--primary);"></i> {vid['title_safe']}</div>
                                <div style="font-size: 0.72em; color: var(--text-muted); margin-bottom: 10px;">
                                    <i class="fas fa-book"></i> {_html_escape(vid['subject'])} &nbsp; <i class="fas fa-folder"></i> {_html_escape(vid['chapter'])}
                                </div>
                                <div class="g-actions">
                                    <button class="btn btn-primary" onclick="openVideoPlayer('{vid['title_js']}', '{vid['url_js']}')"><i class="fas fa-play"></i> Play</button>
                                    <button class="btn btn-secondary" onclick="copyToClipboard('{vid['url_js']}')"><i class="fas fa-copy"></i></button>
                                </div>
                            </div>
"""
    
    html += """                        </div>
                    </div>
                </div>
            </div>
            
            <!-- All PDFs Section -->
            <div id="pdfs-section" class="tab-content" style="display:none;">
                <div class="subject-section">
                    <div class="subject-header" style="cursor: default;">
                        <i class="fas fa-file-pdf"></i>
                        <h2>All PDFs ({len(all_pdfs)})</h2>
                    </div>
                    <div class="subject-content">
                        <div class="grid-2">
""".replace("{len(all_pdfs)}", str(len(all_pdfs)))
    
    for pdf in all_pdfs:
        html += f"""                            <div class="grid-item">
                                <div class="g-title"><i class="fas fa-file-pdf" style="color: var(--success);"></i> {pdf['title_safe']}</div>
                                <div style="font-size: 0.72em; color: var(--text-muted); margin-bottom: 10px;">
                                    <i class="fas fa-book"></i> {_html_escape(pdf['subject'])} &nbsp; <i class="fas fa-folder"></i> {_html_escape(pdf['chapter'])}
                                </div>
                                <div class="g-actions">
                                    <button class="btn btn-success" onclick="openPdfViewer('{pdf['title_js']}', '{pdf['url_js']}')"><i class="fas fa-eye"></i> View</button>
                                    <button class="btn btn-secondary" onclick="copyToClipboard('{pdf['url_js']}')"><i class="fas fa-copy"></i></button>
                                </div>
                            </div>
"""
    
    html += """                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Footer -->
            <div class="footer">
                <div class="footer-logo"><i class="fas fa-crown"></i> Team Cinderella</div>
                <p>Made with <i class="fas fa-heart" style="color: var(--primary);"></i> by <a href="https://t.me/TeamCinderella" target="_blank">@TeamCinderella</a></p>
                <p style="margin-top: 8px; font-size: 0.78em;">Study Smart, Study Better</p>
            </div>
        </div>
    </div>
    
    <!-- Video Player Modal -->
    <div class="modal-overlay" id="videoModal">
        <div class="modal-content">
            <div class="modal-header">
                <h3 id="videoTitle"><i class="fas fa-play-circle" style="color: var(--primary);"></i> Video Player</h3>
                <button class="modal-close" onclick="closeVideoPlayer()"><i class="fas fa-times"></i></button>
            </div>
            <div class="video-player-container" id="videoPlayerContainer">
                <div class="video-placeholder">
                    <i class="fas fa-play-circle"></i>
                    <p>Click Play to start streaming</p>
                    <div class="url-display" id="videoUrlDisplay"></div>
                    <div class="url-note"><i class="fas fa-info-circle"></i> Copy this URL to use in external players like VLC, MX Player, or NPlayer</div>
                </div>
            </div>
            <div class="video-controls">
                <button class="vc-btn play" id="playBtn" onclick="togglePlay()"><i class="fas fa-play"></i></button>
                <button class="vc-btn" onclick="skip(-10)"><i class="fas fa-backward"></i></button>
                <button class="vc-btn" onclick="skip(10)"><i class="fas fa-forward"></i></button>
                <div class="vc-seekbar" onclick="seek(event)">
                    <div class="vc-seekbar-fill" id="seekbarFill"></div>
                </div>
                <span class="vc-time" id="timeDisplay">0:00 / 0:00</span>
                <select class="vc-speed" id="speedSelect" onchange="changeSpeed()">
                    <option value="0.5">0.5x</option>
                    <option value="0.75">0.75x</option>
                    <option value="1" selected>1x</option>
                    <option value="1.25">1.25x</option>
                    <option value="1.5">1.5x</option>
                    <option value="2">2x</option>
                </select>
                <button class="vc-btn" onclick="toggleFullscreen()"><i class="fas fa-expand"></i></button>
                <a href="https://t.me/TeamCinderella" target="_blank" class="vc-madeby"><i class="fab fa-telegram"></i> Made by: Team Cinderella</a>
            </div>
        </div>
    </div>
    
    <!-- PDF Viewer Modal -->
    <div class="modal-overlay" id="pdfModal">
        <div class="modal-content" style="max-width: 1100px;">
            <div class="modal-header">
                <h3 id="pdfTitle"><i class="fas fa-file-pdf" style="color: var(--success);"></i> PDF Viewer</h3>
                <div style="display: flex; gap: 8px;">
                    <a id="pdfDownloadBtn" href="#" target="_blank" class="btn btn-primary" style="font-size: 0.8em; padding: 6px 14px;"><i class="fas fa-download"></i> Download</a>
                    <button class="modal-close" onclick="closePdfViewer()"><i class="fas fa-times"></i></button>
                </div>
            </div>
            <div class="pdf-viewer-container" id="pdfViewerContainer">
                <iframe id="pdfFrame" src=""></iframe>
            </div>
        </div>
    </div>
    
    <script>
    // ===== TAB SWITCHING =====
    function showSection(section) {{
        document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
        document.querySelectorAll('.section-tab').forEach(el => el.classList.remove('active'));
        document.getElementById(section + '-section').style.display = 'block';
        event.target.closest('.section-tab').classList.add('active');
        // Update nav active state
        document.querySelectorAll('.nav-links a').forEach(el => el.classList.remove('active'));
        document.querySelector('.nav-links a[href="#' + section + '"]').classList.add('active');
    }}
    
    // ===== TOGGLE COLLAPSIBLE =====
    function toggleChapter(header) {{
        const content = header.nextElementSibling;
        header.classList.toggle('collapsed');
        content.classList.toggle('hidden');
    }}
    function toggleContent(header) {{
        const content = header.nextElementSibling;
        header.classList.toggle('collapsed');
        content.classList.toggle('hidden');
    }}
    
    // ===== VIDEO PLAYER =====
    // FIXED: previously this just dropped a <source type="application/dash+xml">
    // into a plain <video> tag, which NO browser can decode natively - that's
    // why videos never played. We now detect the stream type from the URL and
    // attach the correct MSE-based player: dash.js for .mpd, hls.js for
    // .m3u8, and native playback for everything else (plain mp4 etc).
    let currentVideo = null;
    let isPlaying = false;
    let playInterval = null;
    let currentTime = 0;
    let duration = 0;
    let dashPlayerInstance = null;
    let hlsPlayerInstance = null;

    function destroyActivePlayers() {{
        if (dashPlayerInstance) {{
            try {{ dashPlayerInstance.reset(); }} catch (e) {{}}
            dashPlayerInstance = null;
        }}
        if (hlsPlayerInstance) {{
            try {{ hlsPlayerInstance.destroy(); }} catch (e) {{}}
            hlsPlayerInstance = null;
        }}
    }}

    function openVideoPlayer(title, url) {{
        document.getElementById('videoTitle').innerHTML = '<i class="fas fa-play-circle" style="color: var(--primary);"></i> ' + escapeHtml(title);
        document.getElementById('videoUrlDisplay').textContent = url;
        document.getElementById('videoModal').classList.add('active');
        document.body.style.overflow = 'hidden';

        destroyActivePlayers();

        // Create a fresh, plain <video> element (no <source> tag - the
        // stream is attached programmatically below based on its type)
        const container = document.getElementById('videoPlayerContainer');
        container.innerHTML = '<video id="activeVideo" controlsList="nodownload" playsinline></video>';
        const video = document.getElementById('activeVideo');
        currentVideo = video;
        isPlaying = false;
        currentTime = 0;
        updatePlayButton();

        const lowerUrl = url.toLowerCase();
        let loadError = false;

        try {{
            if (lowerUrl.indexOf('.mpd') !== -1) {{
                // DASH stream
                if (window.dashjs) {{
                    dashPlayerInstance = dashjs.MediaPlayer().create();
                    dashPlayerInstance.initialize(video, url, false);
                    dashPlayerInstance.on(dashjs.MediaPlayer.events['ERROR'], function(e) {{
                        console.warn('dash.js error', e);
                    }});
                }} else {{
                    loadError = true;
                }}
            }} else if (lowerUrl.indexOf('.m3u8') !== -1) {{
                // HLS stream
                if (window.Hls && Hls.isSupported()) {{
                    hlsPlayerInstance = new Hls();
                    hlsPlayerInstance.loadSource(url);
                    hlsPlayerInstance.attachMedia(video);
                }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{
                    // Safari has native HLS support
                    video.src = url;
                }} else {{
                    loadError = true;
                }}
            }} else {{
                // Plain file (mp4 etc) - native playback works fine
                video.src = url;
            }}
        }} catch (e) {{
            console.error('Player init failed', e);
            loadError = true;
        }}

        if (loadError) {{
            container.innerHTML = (
                '<div class="video-placeholder">' +
                '<i class="fas fa-exclamation-triangle"></i>' +
                '<p>This browser/connection could not load the stream player.</p>' +
                '<div class="url-display">' + escapeHtml(url) + '</div>' +
                '<div class="url-note"><i class="fas fa-info-circle"></i> Copy this URL and open it in VLC, MX Player, or NPlayer instead.</div>' +
                '</div>'
            );
            return;
        }}

        video.addEventListener('timeupdate', function() {{
            currentTime = video.currentTime;
            duration = video.duration || 0;
            updateSeekbar();
            updateTimeDisplay();
        }});
        video.addEventListener('loadedmetadata', function() {{
            duration = video.duration || 0;
            updateTimeDisplay();
        }});
        video.addEventListener('ended', function() {{
            isPlaying = false;
            updatePlayButton();
        }});
        video.addEventListener('play', function() {{
            isPlaying = true;
            updatePlayButton();
        }});
        video.addEventListener('pause', function() {{
            isPlaying = false;
            updatePlayButton();
        }});
        video.addEventListener('error', function() {{
            console.warn('Video element error - stream may be DRM-protected or blocked by CORS.');
        }});
    }}
    
    function closeVideoPlayer() {{
        const video = document.getElementById('activeVideo');
        if (video) {{
            video.pause();
            video.removeAttribute('src');
            video.load();
        }}
        destroyActivePlayers();
        currentVideo = null;
        isPlaying = false;
        document.getElementById('videoModal').classList.remove('active');
        document.body.style.overflow = '';
        if (playInterval) {{ clearInterval(playInterval); playInterval = null; }}
    }}
    
    function togglePlay() {{
        if (!currentVideo) return;
        if (currentVideo.paused) {{
            currentVideo.play().catch(function(e) {{
                // If direct play fails, show the URL for external player
                console.log('Direct playback not supported, showing URL');
            }});
        }} else {{
            currentVideo.pause();
        }}
    }}
    
    function updatePlayButton() {{
        const btn = document.getElementById('playBtn');
        if (isPlaying) {{
            btn.innerHTML = '<i class="fas fa-pause"></i>';
        }} else {{
            btn.innerHTML = '<i class="fas fa-play"></i>';
        }}
    }}
    
    function skip(seconds) {{
        if (!currentVideo) return;
        currentVideo.currentTime = Math.max(0, Math.min(currentVideo.duration || 0, currentVideo.currentTime + seconds));
    }}
    
    function seek(event) {{
        if (!currentVideo || !duration) return;
        const rect = event.currentTarget.getBoundingClientRect();
        const percent = (event.clientX - rect.left) / rect.width;
        currentVideo.currentTime = percent * duration;
    }}
    
    function updateSeekbar() {{
        if (!duration) return;
        const percent = (currentTime / duration) * 100;
        document.getElementById('seekbarFill').style.width = percent + '%';
    }}
    
    function updateTimeDisplay() {{
        document.getElementById('timeDisplay').textContent = formatTime(currentTime) + ' / ' + formatTime(duration);
    }}
    
    function formatTime(t) {{
        if (!t || isNaN(t)) return '0:00';
        const m = Math.floor(t / 60);
        const s = Math.floor(t % 60);
        return m + ':' + (s < 10 ? '0' : '') + s;
    }}
    
    function changeSpeed() {{
        if (!currentVideo) return;
        currentVideo.playbackRate = parseFloat(document.getElementById('speedSelect').value);
    }}
    
    function toggleFullscreen() {{
        const container = document.getElementById('videoPlayerContainer');
        if (!document.fullscreenElement) {{
            container.requestFullscreen().catch(function(){{}});
        }} else {{
            document.exitFullscreen();
        }}
    }}
    
    // ===== PDF VIEWER =====
    // FIXED: Google's docs.google.com/gview embed is widely known to be
    // unreliable - it randomly fails to load and frequently rejects signed/
    // token-bearing CDN URLs (exactly what these PDF links are), which is
    // why "pdf view nahi ho pa raha" was happening. Mozilla's PDF.js viewer
    // is far more reliable for this. We also add a visible fallback link in
    // case the iframe still can't load a particular PDF (e.g. strict CORS).
    function openPdfViewer(title, url) {{
        document.getElementById('pdfTitle').innerHTML = '<i class="fas fa-file-pdf" style="color: var(--success);"></i> ' + escapeHtml(title);
        const viewerUrl = 'https://mozilla.github.io/pdf.js/web/viewer.html?file=' + encodeURIComponent(url);
        const frame = document.getElementById('pdfFrame');
        const pdfContainer = document.getElementById('pdfViewerContainer');
        frame.src = viewerUrl;
        document.getElementById('pdfDownloadBtn').href = url;
        document.getElementById('pdfModal').classList.add('active');
        document.body.style.overflow = 'hidden';

        // If the iframe itself errors out (network/host-level failure),
        // swap in a direct open/download link so the user isn't stuck with
        // a blank box instead of a working preview.
        frame.onerror = function() {{
            pdfContainer.innerHTML = (
                '<div class="video-placeholder">' +
                '<i class="fas fa-exclamation-triangle"></i>' +
                '<p>Could not preview this PDF in-browser.</p>' +
                '<a href="' + url + '" target="_blank" class="btn btn-primary" style="margin-top:10px;display:inline-flex;"><i class="fas fa-external-link-alt"></i> Open / Download PDF</a>' +
                '</div>'
            );
        }};
    }}
    
    function closePdfViewer() {{
        document.getElementById('pdfFrame').src = '';
        document.getElementById('pdfModal').classList.remove('active');
        document.body.style.overflow = '';
        // Restore plain iframe markup in case the error fallback replaced it
        document.getElementById('pdfViewerContainer').innerHTML = '<iframe id="pdfFrame" src=""></iframe>';
    }}
    
    // ===== UTILITY =====
    function copyToClipboard(text) {{
        navigator.clipboard.writeText(text).then(function() {{
            showToast('URL copied to clipboard!');
        }}).catch(function() {{
            const ta = document.createElement('textarea');
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            showToast('URL copied to clipboard!');
        }});
    }}
    
    function showToast(msg) {{
        const toast = document.createElement('div');
        toast.textContent = msg;
        toast.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);background:var(--primary);color:white;padding:12px 24px;border-radius:10px;font-weight:600;z-index:20000;animation:fadeInUp 0.3s;box-shadow:0 5px 20px rgba(255,107,53,0.4);';
        document.body.appendChild(toast);
        setTimeout(function() {{
            toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s';
            setTimeout(function() {{ document.body.removeChild(toast); }}, 300);
        }}, 2000);
    }}
    
    function escapeHtml(text) {{
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }}
    
    // Close modals on escape key
    document.addEventListener('keydown', function(e) {{
        if (e.key === 'Escape') {{
            closeVideoPlayer();
            closePdfViewer();
        }}
    }});
    
    // Close modals on overlay click
    document.getElementById('videoModal').addEventListener('click', function(e) {{
        if (e.target === this) closeVideoPlayer();
    }});
    document.getElementById('pdfModal').addEventListener('click', function(e) {{
        if (e.target === this) closePdfViewer();
    }});
    
    // Smooth scroll for nav links
    document.querySelectorAll('.nav-links a').forEach(function(link) {{
        link.addEventListener('click', function(e) {{
            e.preventDefault();
            const href = this.getAttribute('href');
            if (href === '#home') {{
                window.scrollTo({{ top: 0, behavior: 'smooth' }});
            }} else if (href === '#subjects') {{
                showSection('subjects');
                window.scrollTo({{ top: document.querySelector('.section-tabs').offsetTop - 80, behavior: 'smooth' }});
            }} else if (href === '#videos') {{
                showSection('videos');
                window.scrollTo({{ top: document.querySelector('.section-tabs').offsetTop - 80, behavior: 'smooth' }});
            }} else if (href === '#pdfs') {{
                showSection('pdfs');
                window.scrollTo({{ top: document.querySelector('.section-tabs').offsetTop - 80, behavior: 'smooth' }});
            }}
        }});
    }});
    </script>
</body>
</html>"""
    return html


# ===============================================================
# PW API FETCH (with proper retry logic and error handling)
# ===============================================================
async def fetch_pwwp_data(session: aiohttp.ClientSession, url: str, headers: Dict = None, params: Dict = None, data: Dict = None, method: str = 'GET') -> Any:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with session.request(method, url, headers=headers, params=params, json=data) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logging.error(f"Attempt {attempt + 1} failed: aiohttp error fetching {url}: {e}")
        except Exception as e:
            logging.exception(f"Attempt {attempt + 1} failed: Unexpected error fetching {url}: {e}")

        if attempt < max_retries - 1:
            await asyncio.sleep(2 ** attempt)
        else:
            logging.error(f"Failed to fetch {url} after {max_retries} attempts.")
            return None


# ===============================================================
# CRITICAL FIX: Fetch data with batch ID fallback
# Tries primary_id first, then fallback_id for maximum compatibility
# ===============================================================
async def fetch_with_batch_id_fallback(
    session: aiohttp.ClientSession,
    url_template: str,
    headers: Dict,
    params: Dict = None,
    primary_id: str = None,
    fallback_id: str = None
) -> Any:
    """
    Fetch data trying primary batch ID first, then fallback ID.
    url_template should contain {batch_id} placeholder.
    Returns first successful response, or None if both fail.
    """
    ids_to_try = []
    if primary_id:
        ids_to_try.append(primary_id)
    if fallback_id and fallback_id != primary_id:
        ids_to_try.append(fallback_id)

    for bid in ids_to_try:
        try:
            url = url_template.format(batch_id=bid)
            data = await fetch_pwwp_data(session, url, headers=headers, params=params)
            if data and isinstance(data, dict):
                # Check for success - different APIs return different success indicators
                has_data = data.get("data") is not None
                is_success = data.get("success", False)
                status_code = data.get("statusCode", 200)

                if has_data and status_code != 404:
                    logging.info(f"SUCCESS with batch_id={bid}: {url_template}")
                    return data
        except Exception as e:
            logging.warning(f"Failed with batch_id={bid}: {e}")

    logging.error(f"All batch IDs failed for: {url_template}")
    return None


# ===============================================================
# ENHANCED: Fetch content using schedule-details (ALL BATCHES)
# with better video extraction for non-purchased batches
# ===============================================================
async def fetch_content_via_schedule_details(
    session: aiohttp.ClientSession,
    batch_id: str,
    subject_id: str,
    topic_id: str,
    headers: Dict,
    content_type: str = "videos"
) -> List[str]:
    """
    Fetch content list then get schedule-details for each to extract proper video URLs.
    Enhanced with fallback video extraction for non-purchased batches.
    """
    all_lines = []
    dedup = ContentDeduplicator()

    try:
        page = 1
        while page <= 20:  # safety limit
            url = f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/contents"
            params = {
                "tag": topic_id,
                "contentType": content_type,
                "page": page
            }

            data = await fetch_pwwp_data(session, url, headers=headers, params=params)
            if not data:
                break

            items = data.get("data", [])
            if not items:
                break

            for item in items:
                schedule_id = item.get("_id", "")
                # CRITICAL FIX: Use safe_topic to handle dict/None topic
                topic = safe_topic(item.get("topic"), "Unknown Topic")

                got_content = False

                # Try schedule-details for PROPER video URL extraction
                detail_url = f"https://api.penpencil.co/v3/batches/{batch_id}/subject/{subject_id}/schedule/{schedule_id}/schedule-details"
                detail_data = await fetch_pwwp_data(session, detail_url, headers=headers)

                if detail_data and detail_data.get("data"):
                    detail_item = detail_data["data"]

                    # --- VIDEO EXTRACTION (works for ALL batches) ---
                    if content_type in ('videos', 'DppVideos'):
                        # CRITICAL FIX: Always use comprehensive extractor with IDs
                        # to ensure parentId/childId/videoId are included in URL
                        video_details = detail_item.get('videoDetails', {})
                        parent_id, child_id, vid = extract_pw_ids(
                            video_details=video_details,
                            schedule_data=detail_item,
                            schedule_id=schedule_id,
                            batch_id=batch_id
                        )

                        # Method 1: Comprehensive extractor (PRIMARY - always with IDs)
                        vurl, drm = extract_comprehensive_video_url(video_details, parent_id, child_id, vid)
                        if vurl:
                            line = f"{topic}:{vurl}{drm}"
                            if dedup.add_and_check_unique(line):
                                all_lines.append(line)
                                got_content = True

                        # Method 2: Fallback using extract_video_data_from_schedule
                        if not got_content:
                            video_info = extract_video_data_from_schedule(detail_data)
                            # Pass IDs to format_video_line as well
                            video_lines = format_video_line(topic, video_info, parent_id, child_id, vid)
                            for vline in video_lines:
                                if dedup.add_and_check_unique(vline):
                                    all_lines.append(vline)
                                    got_content = True

                    # --- NOTES / PDF EXTRACTION ---
                    elif content_type == 'notes':
                        hw_ids = detail_item.get('homeworkIds', [])
                        for hw in hw_ids:
                            att_ids = hw.get('attachmentIds', [])
                            # CRITICAL FIX: Use safe_topic for homework topic too
                            hw_topic = safe_topic(hw.get('topic'), topic)
                            for att in att_ids:
                                base_url = att.get('baseUrl', '')
                                key = att.get('key', '')
                                # CRITICAL FIX: Use safe_topic for attachment name
                                name = safe_topic(att.get('name'), hw_topic)
                                if base_url and key:
                                    line = f"{name}:{base_url}{key}"
                                    if dedup.add_and_check_unique(line):
                                        all_lines.append(line)
                                        got_content = True

                    elif content_type == 'DppNotes':
                        # ONLY extract from dpp.homeworkIds (NOT regular homeworkIds)
                        dpp = detail_item.get('dpp')
                        if dpp:
                            dpp_homework_ids = dpp.get('homeworkIds', [])
                            for hw in dpp_homework_ids:
                                att_ids = hw.get('attachmentIds', [])
                                hw_topic = safe_topic(hw.get('topic'), topic)
                                for att in att_ids:
                                    base_url = att.get('baseUrl', '')
                                    key = att.get('key', '')
                                    name = safe_topic(att.get('name'), hw_topic)
                                    if base_url and key:
                                        line = f"{name}:{base_url}{key}"
                                        if dedup.add_and_check_unique(line):
                                            all_lines.append(line)
                                            got_content = True

                # --- FALLBACK: If schedule-details failed or no content found ---
                if not got_content:
                    if content_type in ('videos', 'DppVideos'):
                        # Fallback 1: Try to extract video from item directly
                        vurl, drm = extract_video_from_content_item(item)
                        if vurl:
                            # Append IDs to fallback URL too
                            parent_id_fb = batch_id
                            child_id_fb = schedule_id
                            vid_fb = item.get('videoId', '') or item.get('contentId', '')
                            vurl = append_video_params(vurl, parent_id_fb, child_id_fb, vid_fb)
                            line = f"{topic}:{vurl}{drm}"
                            if dedup.add_and_check_unique(line):
                                all_lines.append(line)
                                got_content = True

                        # Fallback 2: Check basic URL from item
                        if not got_content:
                            basic_url = item.get('url', '')
                            if basic_url and ('.mpd' in basic_url or '.m3u8' in basic_url or 'cloudfront' in basic_url):
                                parent_id_fb = batch_id
                                child_id_fb = schedule_id
                                vid_fb = item.get('videoId', '') or item.get('contentId', '')
                                final_url = append_video_params(basic_url, parent_id_fb, child_id_fb, vid_fb)
                                line = f"{topic}:{final_url}"
                                if dedup.add_and_check_unique(line):
                                    all_lines.append(line)
                                    got_content = True

                    elif content_type == 'notes':
                        for hw in item.get('homeworkIds', []):
                            for att in hw.get('attachmentIds', []):
                                name = safe_topic(att.get('name'), topic)
                                base_url = att.get('baseUrl', '')
                                key = att.get('key', '')
                                if key:
                                    line = f"{name}:{base_url}{key}"
                                    if dedup.add_and_check_unique(line):
                                        all_lines.append(line)
                                        got_content = True

                    elif content_type == 'DppNotes':
                        item_dpp = item.get('dpp')
                        if item_dpp:
                            for hw in item_dpp.get('homeworkIds', []):
                                for att in hw.get('attachmentIds', []):
                                    name = safe_topic(att.get('name'), topic)
                                    base_url = att.get('baseUrl', '')
                                    key = att.get('key', '')
                                    if key:
                                        line = f"{name}:{base_url}{key}"
                                        if dedup.add_and_check_unique(line):
                                            all_lines.append(line)
                                            got_content = True

            if not data.get("hasMore", True):
                break
            page += 1

    except Exception as e:
        logging.exception(f"Error in fetch_content_via_schedule_details: {e}")

    return all_lines


# ===============================================================
# PW: Process chapter content using ADVANCED schedule-details
# ===============================================================
async def process_pwwp_chapter_content_advanced(session, batch_id, subject_id, chapter_id, headers):
    """Process a chapter's content using schedule-details approach (works for ALL batches)."""
    combined_content = {
        'videos': [],
        'notes': [],
        'DppNotes': [],
        'DppVideos': []
    }

    for content_type in ['videos', 'notes', 'DppNotes', 'DppVideos']:
        lines = await fetch_content_via_schedule_details(
            session, batch_id, subject_id, chapter_id, headers, content_type
        )
        if lines:
            combined_content[content_type] = lines

    return combined_content


async def process_pwwp_subject(session, subject, batch_id, batch_name, zipf, json_data, all_subject_urls, headers):
    subject_name = safe_topic(subject.get("subject"), "Unknown Subject")
    subject_id = subject.get("_id")
    json_data[batch_name][subject_name] = {}
    zipf.writestr(f"{subject_name}/", "")

    # Get chapters
    chapters = []
    page = 1
    while page <= 20:
        url = f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/topics?page={page}"
        data = await fetch_pwwp_data(session, url, headers=headers)
        if data and data.get("data"):
            chapters.extend(data["data"])
            if len(data["data"]) < 20:
                break
            page += 1
        else:
            break

    chapter_tasks = []
    for chapter in chapters:
        chapter_name = safe_topic(chapter.get("name"), "Unknown Chapter")
        zipf.writestr(f"{subject_name}/{chapter_name}/", "")
        json_data[batch_name][subject_name][chapter_name] = {}
        chapter_tasks.append(process_pwwp_chapter_content_advanced(session, batch_id, subject_id, chapter["_id"], headers))

    chapter_results = await asyncio.gather(*chapter_tasks)

    # GLOBAL deduplication across ALL content types for this subject
    global_dedup = ContentDeduplicator()
    all_urls = []

    for chapter, chapter_content in zip(chapters, chapter_results):
        chapter_name = safe_topic(chapter.get("name"), "Unknown Chapter")

        for content_type in ['videos', 'notes', 'DppNotes', 'DppVideos']:
            if chapter_content.get(content_type):
                content = chapter_content[content_type]
                # Apply global deduplication
                unique_content = global_dedup.filter_unique(content)
                if unique_content:
                    unique_content.reverse()
                    content_string = "\n".join(unique_content)
                    zipf.writestr(f"{subject_name}/{chapter_name}/{content_type}.txt", content_string.encode('utf-8'))
                    json_data[batch_name][subject_name][chapter_name][content_type] = unique_content
                    all_urls.extend(unique_content)

    all_subject_urls[subject_name] = all_urls


def find_pw_old_batch(batch_search):
    try:
        response = requests.get(f"https://abhiguru143.github.io/AS-MULTIVERSE-PW/batch/batch.json")
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching data: {e}")
        return []
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON: {e}")
        return []

    matching_batches = []
    for batch in data:
        if batch_search.lower() in batch['batch_name'].lower():
            matching_batches.append(batch)

    return matching_batches


# ===============================================================
# ENHANCED: Fetch ALL batches using multiple endpoints
# Uses consistent API versions - v3 as primary, v2 as fallback
# ===============================================================
async def fetch_all_pw_batches(session, headers, search_query=""):
    """
    Fetch ALL available PW batches from multiple sources:
    1. my-batches (purchased + free)
    2. all-purchased-batches
    3. all-batches (explore)
    4. search API (by name)
    5. batches/list (alternative explore)

    With a valid token, gives access to ALL batches.
    Uses consistent v3 API as primary, v2 as fallback.
    """
    all_batches = []
    seen_ids = set()

    # 1. Get my-batches (v3 - primary)
    try:
        params = {
            'mode': '1',
            'filter': 'false',
            'exam': '',
            'amount': '',
            'organisationId': '5eb393ee95fab7468a79d189',
            'classes': '',
            'page': '1',
            'limit': '100',
            'programId': '',
            'ut': str(int(time.time() * 1000)),
        }
        url = "https://api.penpencil.co/v3/batches/my-batches"
        data = await fetch_pwwp_data(session, url, headers=headers, params=params)
        if data and data.get("data"):
            for batch in data["data"]:
                bid = batch.get("_id", "")
                if bid and bid not in seen_ids:
                    seen_ids.add(bid)
                    all_batches.append(batch)
    except Exception as e:
        logging.warning(f"my-batches endpoint failed: {e}")

    # 2. all-purchased-batches (v3)
    try:
        params = {'mode': '1', 'page': '1'}
        url = "https://api.penpencil.co/v3/batches/all-purchased-batches"
        data = await fetch_pwwp_data(session, url, headers=headers, params=params)
        if data and data.get("data"):
            for batch in data["data"]:
                bid = batch.get("_id", "")
                if bid and bid not in seen_ids:
                    seen_ids.add(bid)
                    all_batches.append(batch)
    except Exception as e:
        logging.warning(f"all-purchased-batches endpoint failed: {e}")

    # 3. all-batches (explore endpoint - v2 as primary since v3 gives 400/404)
    try:
        params = {'page': '1', 'limit': '100'}
        # v2 is more reliable for all-batches
        url = "https://api.penpencil.co/v2/batches/all-batches"
        data = await fetch_pwwp_data(session, url, headers=headers, params=params)
        if data and data.get("data"):
            explore_batches = data["data"]
            if isinstance(explore_batches, dict):
                explore_batches = explore_batches.get("data", [])
            if isinstance(explore_batches, list):
                for batch in explore_batches:
                    bid = batch.get("_id", "")
                    if bid and bid not in seen_ids:
                        seen_ids.add(bid)
                        all_batches.append(batch)
    except Exception as e:
        logging.warning(f"all-batches v2 endpoint failed: {e}")

    # 4. Search API (v3)
    if search_query:
        try:
            search_url = f"https://api.penpencil.co/v3/batches/search?name={search_query}"
            search_data = await fetch_pwwp_data(session, search_url, headers=headers)
            if search_data and search_data.get("data"):
                search_results = search_data["data"]
                if isinstance(search_results, dict):
                    search_results = search_results.get("data", [])
                if isinstance(search_results, list):
                    for batch in search_results:
                        bid = batch.get("_id", "")
                        if bid and bid not in seen_ids:
                            seen_ids.add(bid)
                            all_batches.append(batch)
        except Exception as e:
            logging.warning(f"Search API failed: {e}")

    # 5. Try batches/list endpoint (v1 - alternative explore)
    try:
        params = {'organisationId': '5eb393ee95fab7468a79d189', 'page': '1', 'limit': '100'}
        url = "https://api.penpencil.co/v1/batches/list"
        data = await fetch_pwwp_data(session, url, headers=headers, params=params)
        if data and data.get("data"):
            list_batches = data["data"]
            if isinstance(list_batches, dict):
                list_batches = list_batches.get("data", [])
            if isinstance(list_batches, list):
                for batch in list_batches:
                    bid = batch.get("_id", "")
                    if bid and bid not in seen_ids:
                        seen_ids.add(bid)
                        all_batches.append(batch)
    except Exception as e:
        logging.warning(f"batches/list endpoint failed: {e}")

    return all_batches


# ===============================================================
# PAGINATION: Build inline keyboard for batch selection
# ===============================================================
def build_batch_pagination_keyboard(user_id: int, page: int) -> InlineKeyboardMarkup:
    """Build pagination keyboard for batch selection."""
    data = user_batch_pages.get(user_id)
    if not data:
        return InlineKeyboardMarkup([])

    all_batches = data["batches"]
    total_pages = (len(all_batches) + 9) // 10  # 10 per page, round up

    buttons = []

    # Navigation row (Previous | Page X/Y | Next)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀ Previous", callback_data=f"batch_prev|{user_id}"))
    nav_buttons.append(InlineKeyboardButton(f"Page {page + 1}/{total_pages}", callback_data="batch_page_info"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ▶", callback_data=f"batch_next|{user_id}"))

    if nav_buttons:
        buttons.append(nav_buttons)

    return InlineKeyboardMarkup(buttons)


def get_batches_for_page(user_id: int, page: int) -> Tuple[List[dict], str]:
    """Get the batch list text for a specific page (10 per page)."""
    data = user_batch_pages.get(user_id)
    if not data:
        return [], ""

    all_batches = data["batches"]
    total = len(all_batches)
    total_pages = (total + 9) // 10

    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    start = page * 10
    end = min(start + 10, total)
    page_batches = all_batches[start:end]

    text = ''
    for cnt, course in enumerate(page_batches):
        global_index = start + cnt + 1
        name = course.get('name', 'Unknown')
        text += f"{global_index}. ```\n{name}```\n"

    return page_batches, text


# ===============================================================
# DATE PARSING: User date input (DD/MM/YYYY) -> Date Range (IST based)
# ===============================================================
def parse_user_date_to_range(date_str: str):
    """
    User sends a date in DD/MM/YYYY format (e.g. 16/06/2026).
    Convert to:
    - start_epoch: 12:00 AM IST of that day (in UTC ms)
    - end_epoch: 11:59:59.999 PM IST of that day (in UTC ms)
    Returns: (start_epoch_ms, end_epoch_ms, date_str_yyyy_mm_dd, display_date_str)
    Returns (None, None, None, None) if the input is invalid.
    """
    if not date_str:
        return None, None, None, None

    cleaned = date_str.strip()
    # Accept both / and - as separators, e.g. 16/06/2026 or 16-06-2026
    cleaned = cleaned.replace("-", "/")

    parsed_dt = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            parsed_dt = datetime.strptime(cleaned, fmt)
            break
        except ValueError:
            continue

    if parsed_dt is None:
        return None, None, None, None

    # Treat the parsed date as a calendar day in IST
    start_dt_ist = parsed_dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=IST)
    end_dt_ist = start_dt_ist + timedelta(days=1) - timedelta(milliseconds=1)

    start_dt_utc = start_dt_ist.astimezone(timezone.utc)
    end_dt_utc = end_dt_ist.astimezone(timezone.utc)

    start_epoch = int(start_dt_utc.timestamp() * 1000)
    end_epoch = int(end_dt_utc.timestamp() * 1000)

    date_str_iso = start_dt_ist.strftime("%Y-%m-%d")
    display_date = start_dt_ist.strftime("%d-%m-%Y")

    return start_epoch, end_epoch, date_str_iso, display_date


# ===============================================================
# CRITICAL FIX: Fetch schedule for SPECIFIC DATE RANGE
# Now properly handles purchased batches (single batch _id)
# ===============================================================
# ===============================================================
# CONFIRMED WORKING: weekly-schedules endpoint (captured from PW Web)
# GET /v2/batches/{batchId}/weekly-schedules
# params: batchId, batchSubjectId (empty = ALL subjects), startDate, endDate (YYYY-MM-DD), page
# This single endpoint returns BOTH lecture and notes items already
# scoped to the exact date range - no more guessing across v1/v2/v3.
# ===============================================================
async def fetch_weekly_schedules(session, batch_id, date_str_iso, headers):
    """
    Fetch the schedule for ONE specific day (YYYY-MM-DD, IST calendar date)
    using the confirmed-working weekly-schedules endpoint.
    Returns a flat list of normalized schedule items:
        {
            "type": "LECTURE" | "NOTES",
            "schedule_id": str,
            "subject_id": str,        # batchSubjectId
            "topic": str,
            "start_time": str,        # ISO datetime string from API
            "raw": dict                # full raw item, for reference
        }
    """
    normalized = []
    page = 1

    while page <= 20:  # safety limit
        url = f"https://api.penpencil.co/v2/batches/{batch_id}/weekly-schedules"
        params = {
            "batchId": batch_id,
            "batchSubjectId": "",   # empty = all subjects in one call
            "startDate": date_str_iso,
            "endDate": date_str_iso,
            "page": page
        }

        data = await fetch_pwwp_data(session, url, headers=headers, params=params)
        if not data or not data.get("success") or not data.get("data"):
            break

        items = data["data"]
        if not items:
            break

        for item in items:
            item_type = item.get("type", "")
            schedule_id = item.get("_id", "")

            if item_type == "LECTURE":
                details = item.get("videoDetails", {}) or {}
            elif item_type == "NOTES":
                details = item.get("notesDetails", {}) or {}
            else:
                # Unknown type - try both common containers, skip if neither exists
                details = item.get("videoDetails") or item.get("notesDetails") or {}

            subject_id = details.get("batchSubjectId", "")
            topic = safe_topic(details.get("topic"), "Unknown Topic")
            start_time = details.get("startTime", "")

            normalized.append({
                "type": item_type,
                "schedule_id": schedule_id,
                "subject_id": subject_id,
                "topic": topic,
                "start_time": start_time,
                "raw": item
            })

        # weekly-schedules doesn't reliably send hasMore - stop once a page
        # returns fewer than a typical full page, or just rely on empty next page
        if len(items) < 20:
            break
        page += 1

    return normalized


# ===============================================================
# For each matched schedule item on the target date, fetch the FULL
# schedule-details (v1 - confirmed working, same as Full Batch /
# Today's Class) to get the actual playable video URL (master.mpd),
# DRM keys, and parentId/childId/videoId - plus PDF attachments.
# ===============================================================
async def fetch_date_schedule_details(session, batch_id, subject_id, schedule_id, topic, headers, dedup):
    """
    Fetch schedule-details for ONE schedule item and extract video + notes lines.
    Mirrors the proven logic used in Full Batch / Today's Class (v1 endpoint).
    Returns (video_lines, notes_lines)
    """
    video_lines = []
    notes_lines = []

    if not subject_id or not schedule_id or str(subject_id).lower() == "none":
        return video_lines, notes_lines

    url = f"https://api.penpencil.co/v1/batches/{batch_id}/subject/{subject_id}/schedule/{schedule_id}/schedule-details"
    data = await fetch_pwwp_data(session, url, headers=headers)

    if not data or not data.get("success") or not data.get("data"):
        return video_lines, notes_lines

    detail_item = data["data"]

    # --- VIDEO EXTRACTION (master.mpd + parentId/childId/videoId + DRM) ---
    video_details = detail_item.get("videoDetails", {})
    if video_details:
        parent_id, child_id, vid = extract_pw_ids(
            video_details=video_details,
            schedule_data=detail_item,
            schedule_id=schedule_id,
            batch_id=batch_id
        )
        vurl, drm = extract_comprehensive_video_url(video_details, parent_id, child_id, vid)
        if vurl:
            line = f"{topic}:{vurl}{drm}"
            if dedup.add_and_check_unique(line):
                video_lines.append(line)

    # --- NOTES / PDF EXTRACTION ---
    homework_ids = detail_item.get("homeworkIds", [])
    for hw in homework_ids:
        hw_topic = safe_topic(hw.get("topic"), topic)
        for att in hw.get("attachmentIds", []):
            base_url = att.get("baseUrl", "")
            key = att.get("key", "")
            name = safe_topic(att.get("name"), hw_topic)
            if base_url and key:
                line = f"{name}:{base_url}{key}"
                if dedup.add_and_check_unique(line):
                    notes_lines.append(line)

    # --- DPP NOTES (only from dpp.homeworkIds) ---
    dpp = detail_item.get("dpp")
    if dpp:
        for hw in dpp.get("homeworkIds", []):
            hw_topic = safe_topic(hw.get("topic"), topic)
            for att in hw.get("attachmentIds", []):
                base_url = att.get("baseUrl", "")
                key = att.get("key", "")
                name = safe_topic(att.get("name"), hw_topic)
                if base_url and key:
                    line = f"{name}:{base_url}{key}"
                    if dedup.add_and_check_unique(line):
                        notes_lines.append(line)

    return video_lines, notes_lines


# ===============================================================
# CRITICAL FIX: Enhanced topic extraction from schedule items
# Handles all PW API response formats for topic/subject names
# Now checks many more fields to avoid "Unknown Topic"
# ===============================================================
def extract_topic_from_schedule_item(schedule_item: dict) -> str:
    """
    Enhanced topic extraction from a schedule item.
    Tries multiple fields to find the best topic name.
    CRITICAL FIX: Now checks 20+ fields to avoid "Unknown Topic"
    """
    if not schedule_item:
        return "Unknown Topic"

    # Priority 1: Direct topic fields (most common)
    for key in ["topic", "name", "title", "displayName", "classTitle", "lessonName", "lessonTitle"]:
        val = schedule_item.get(key)
        if val:
            topic = safe_topic(val)
            if topic and topic != "Unknown Topic":
                return topic

    # Priority 2: Nested topic dict (multilingual support)
    topic_dict = schedule_item.get("topic", {})
    if isinstance(topic_dict, dict):
        for key in ["en", "hi", "name", "title", "text", "value", "display"]:
            if key in topic_dict and topic_dict[key]:
                topic = safe_topic(topic_dict[key])
                if topic and topic != "Unknown Topic":
                    return topic

    # Priority 3: Content/topic info
    for key in ["contentTitle", "videoTitle", "chapterName", "topicName", "description", "contentDescription"]:
        val = schedule_item.get(key)
        if val:
            topic = safe_topic(val)
            if topic and topic != "Unknown Topic":
                return topic

    # Priority 4: Subject info for topic
    subject_info = schedule_item.get("subject", {})
    if isinstance(subject_info, dict):
        for key in ["subject", "name", "title", "displayName"]:
            if key in subject_info and subject_info[key]:
                topic = safe_topic(subject_info[key])
                if topic and topic != "Unknown Topic":
                    return topic

    # Priority 5: Instructor/teacher name as fallback
    for key in ["instructor", "teacher", "mentor", "faculty"]:
        val = schedule_item.get(key)
        if val:
            if isinstance(val, dict):
                name = val.get("name", val.get("fullName", ""))
                if name:
                    return safe_topic(name)
            elif isinstance(val, str):
                return safe_topic(val)

    # Priority 6: Check in schedule data
    schedule_data = schedule_item.get("schedule", {})
    if isinstance(schedule_data, dict):
        for key in ["topic", "name", "title", "lessonName"]:
            if key in schedule_data and schedule_data[key]:
                topic = safe_topic(schedule_data[key])
                if topic and topic != "Unknown Topic":
                    return topic

    # Priority 7: Use content type as last resort
    content_type = schedule_item.get("contentType", schedule_item.get("type", ""))
    if content_type:
        return safe_topic(f"{content_type} Class")

    return "Unknown Topic"


# ===============================================================
# CRITICAL FIX: Enhanced subject_id extraction from schedule items
# Prevents dict-string in URLs causing 404 errors
# ===============================================================
def extract_subject_id_from_schedule_item(schedule_item: dict) -> str:
    """
    Safely extract subject_id from a schedule item.
    Handles all PW API response formats.
    """
    if not schedule_item:
        return ""

    # Try direct fields first
    for key in ["batchSubjectId", "subjectId", "batchSubject", "subject"]:
        val = schedule_item.get(key)
        if val:
            if isinstance(val, str):
                return val
            elif isinstance(val, dict):
                return val.get("_id", val.get("id", ""))
            elif isinstance(val, list) and val:
                first = val[0]
                if isinstance(first, dict):
                    return first.get("_id", first.get("id", ""))
                elif isinstance(first, str):
                    return first

    return ""


# ===============================================================
# ENHANCED: Process content for a specific date (DD/MM/YYYY input)
# Uses both schedule endpoint + content-based fallback
# Better topic extraction and subject_id validation to prevent 404s
# ===============================================================
async def process_date_content(session, batch_id, batch_name, start_epoch, end_epoch, target_date, headers, user_id):
    """
    Process content extraction for a specific date (DD/MM/YYYY input from user).
    target_date is the IST calendar date in YYYY-MM-DD format.

    Flow (confirmed-working endpoints):
      1. weekly-schedules  -> discover every LECTURE/NOTES item on that exact date
      2. schedule-details  -> (v1, same as Full Batch / Today's Class) fetch the
                               actual master.mpd video URL + parentId/childId/videoId
                               + DRM keys, and PDF attachments for each item found

    Returns: (txt_path, zip_path, total_schedules, error_msg)
    """
    # Get batch details for subjects (used to build a subject_id -> name lookup)
    subjects = []
    try:
        detail_url = f"https://api.penpencil.co/v3/batches/{batch_id}/details"
        batch_resp = await fetch_pwwp_data(session, detail_url, headers=headers)
        if batch_resp and batch_resp.get("success"):
            subjects = batch_resp.get("data", {}).get("subjects", [])
            if subjects:
                logging.info(f"Fetched batch details: {len(subjects)} subjects")
    except Exception as e:
        logging.warning(f"Failed to fetch batch details: {e}")

    if not subjects:
        return None, None, 0, "Failed to fetch batch details"

    # Build subject lookup: batchSubjectId (the "_id" of the subject-batch mapping) -> name
    subject_map = {}
    for subj in subjects:
        sid = subj.get("_id")
        sname = safe_topic(subj.get("subject"), "Unknown Subject")
        subject_map[sid] = sname

    # Step 1: Discover all schedule items for this exact date
    schedule_items = await fetch_weekly_schedules(session, batch_id, target_date, headers)

    if not schedule_items:
        return None, None, 0, f"No classes scheduled for {target_date}"

    # Step 2: For each item, fetch full schedule-details (video URL + PDFs)
    all_urls = []
    structured_data = {}
    dedup = ContentDeduplicator()

    clean_batch_name = batch_name.replace('/', '_').replace(':', '_').replace('|', '_').replace('?', '_')

    detail_tasks = []
    for item in schedule_items:
        detail_tasks.append(
            fetch_date_schedule_details(
                session, batch_id, item["subject_id"], item["schedule_id"], item["topic"], headers, dedup
            )
        )

    detail_results = await asyncio.gather(*detail_tasks)

    for item, (video_lines, notes_lines) in zip(schedule_items, detail_results):
        # Primary: lookup via batch/details subjects list
        subject_name = subject_map.get(item["subject_id"], "")

        # Fallback: weekly-schedules itself embeds a subjectId.name field
        # on each raw item - use it if the primary lookup missed
        if not subject_name:
            raw = item.get("raw", {})
            raw_details = raw.get("videoDetails") or raw.get("notesDetails") or {}
            raw_subject = raw_details.get("subjectId", {})
            if isinstance(raw_subject, dict) and raw_subject.get("name"):
                subject_name = safe_topic(raw_subject.get("name"), "")

        if not subject_name:
            subject_name = "Unknown Subject"

        if subject_name not in structured_data:
            structured_data[subject_name] = []

        all_urls.extend(video_lines)
        all_urls.extend(notes_lines)

        structured_data[subject_name].append({
            "topic": item["topic"],
            "start_time": item["start_time"],
            "end_time": item["start_time"],
            "videos": video_lines,
            "notes": notes_lines
        })

    # === CREATE OUTPUT FILES ===
    file_path_base = f"date_{target_date}_{clean_batch_name}"

    # 1. TXT file
    txt_path = f"{file_path_base}.txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        for subject_name, items in structured_data.items():
            for item in items:
                if item["videos"]:
                    f.write("\n".join(item["videos"]) + "\n")
                if item["notes"]:
                    f.write("\n".join(item["notes"]) + "\n")

    # 2. ZIP file
    zip_path = f"{file_path_base}.zip"
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for subject_name, items in structured_data.items():
            zipf.writestr(f"{subject_name}/", "")
            for item in items:
                topic = item["topic"]
                time_slot = item["start_time"]
                folder_name = f"{subject_name}/{topic}_{time_slot}"

                if item["videos"]:
                    content_text = "\n".join(item["videos"])
                    zipf.writestr(f"{folder_name}/videos.txt", content_text.encode('utf-8'))
                if item["notes"]:
                    content_text = "\n".join(item["notes"])
                    zipf.writestr(f"{folder_name}/notes.txt", content_text.encode('utf-8'))

    # 3. JSON file
    json_path = f"{file_path_base}.json"
    json_data = {batch_name: {}}
    for subject_name, items in structured_data.items():
        json_data[batch_name][subject_name] = {}
        for item in items:
            topic_key = f"{item['topic']} ({item['start_time']})"
            json_data[batch_name][subject_name][topic_key] = {
                "videos": item["videos"],
                "notes": item["notes"]
            }
    json_data[batch_name]["date"] = target_date
    json_data[batch_name]["total_schedules"] = len(schedule_items)
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=4)

    return txt_path, zip_path, len(schedule_items), None


# ===============================================================
# TODAY'S CLASS: Using working v1 todays-schedule endpoint
# EXACTLY as in workingmain.py - DO NOT MODIFY
# CRITICAL FIX: Now accepts batch_pw_id for non-purchased batches
# CRITICAL FIX: Skips items with None subject_id to prevent 404
# ===============================================================
async def get_pwwp_todays_schedule_content_details(session: aiohttp.ClientSession, selected_batch_id, subject_id, schedule_id, headers: Dict) -> List[str]:
    """Fetch content details for a single today's schedule item.
    Uses v1 schedule-details endpoint (confirmed working)."""
    content = []

    # CRITICAL FIX: Skip if subject_id is None or invalid
    if not subject_id or str(subject_id).lower() == "none":
        logging.warning(f"Skipping schedule {schedule_id}: subject_id is None/invalid")
        return content

    url = f"https://api.penpencil.co/v1/batches/{selected_batch_id}/subject/{subject_id}/schedule/{schedule_id}/schedule-details"
    data = await fetch_pwwp_data(session, url, headers)

    if data and data.get("success") and data.get("data"):
        data_item = data["data"]

        # --- VIDEO EXTRACTION ---
        video_details = data_item.get('videoDetails', {})
        if video_details:
            # CRITICAL FIX: Use safe_topic for name extraction
            name = safe_topic(data_item.get('topic'), "Unknown Topic")

            parent_id, child_id, video_id = extract_pw_ids(
                video_details=video_details,
                schedule_data=data_item,
                schedule_id=schedule_id,
                batch_id=selected_batch_id
            )

            video_url, drm_info = extract_comprehensive_video_url(video_details, parent_id, child_id, video_id)

            if video_url:
                line = f"{name}:{video_url}{drm_info}\n"
                content.append(line)

        # --- HOMEWORK / NOTES (PDFs) ---
        homework_ids = data_item.get('homeworkIds', [])
        for homework in homework_ids:
            attachment_ids = homework.get('attachmentIds', [])
            # CRITICAL FIX: Use safe_topic
            name = safe_topic(homework.get('topic'), "Notes")
            for attachment in attachment_ids:
                url = attachment.get('baseUrl', '') + attachment.get('key', '')
                if url:
                    line = f"{name}:{url}\n"
                    content.append(line)

        # --- DPP HOMEWORK ---
        dpp = data_item.get('dpp')
        if dpp:
            dpp_homework_ids = dpp.get('homeworkIds', [])
            for homework in dpp_homework_ids:
                attachment_ids = homework.get('attachmentIds', [])
                name = safe_topic(homework.get('topic'), "DPP")
                for attachment in attachment_ids:
                    url = attachment.get('baseUrl', '') + attachment.get('key', '')
                    if url:
                        line = f"{name}:{url}\n"
                        content.append(line)
    else:
        logging.warning(f"No Data Found For Id - {schedule_id}")
    return content


# ===============================================================
# Today's schedule using the CONFIRMED WORKING v1 endpoint
# (matches the proven pww.py implementation)
# ===============================================================
async def get_pwwp_all_todays_schedule_content(session: aiohttp.ClientSession, selected_batch_id: str, batch_pw_id: str, headers: Dict) -> List[str]:
    """Fetch all of today's schedule content using the working v1 endpoint."""
    all_content = []

    url = f"https://api.penpencil.co/v1/batches/{selected_batch_id}/todays-schedule"
    todays_schedule_details = await fetch_pwwp_data(session, url, headers)

    if todays_schedule_details and todays_schedule_details.get("success") and todays_schedule_details.get("data"):
        tasks = []
        valid_items = []

        for item in todays_schedule_details['data']:
            schedule_id = item.get('_id')
            subject_id = item.get('batchSubjectId')

            # CRITICAL FIX: Skip items with None/invalid subject_id
            if not subject_id or str(subject_id).lower() == "none":
                logging.warning(f"Skipping item {schedule_id}: subject_id is None/invalid")
                continue

            task = asyncio.create_task(get_pwwp_todays_schedule_content_details(session, selected_batch_id, subject_id, schedule_id, headers))
            tasks.append(task)
            valid_items.append(item)

        if tasks:
            results = await asyncio.gather(*tasks)
            for result in results:
                all_content.extend(result)

        if not valid_items:
            logging.warning("No valid items found (all had None subject_id)")
    else:
        logging.warning("No today's schedule data found.")

    # Remove duplicate (title+url) entries — fixes DPP notes appearing twice
    # (once under homeworkIds, once under dpp.homeworkIds)
    all_content = deduplicate_by_url_and_title(all_content)

    return all_content


# ===============================================================
# BOT: Start Command
# ===============================================================
@bot.on_message(filters.command(["start"]))
async def start(bot, message):
    broadcast_users.add(message.chat.id)
    _save_broadcast_users(broadcast_users)
    random_image_url = random.choice(image_list)
    keyboard = [
        [InlineKeyboardButton("🕺PHYSICS WALLAH🕺", callback_data="pwwp")],
        [InlineKeyboardButton("😋Join Channel", url="https://t.me/teamcinderella")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_photo(
        photo=random_image_url,
        caption="HABIBI PLEASE PRESS HERE🤓",
        quote=True,
        reply_markup=reply_markup
    )


# ══════════════════════════════════════════════════════════════════════════════
# ── AUTH SYSTEM (Owner only — JSON-backed, survives restarts) ────────────────
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["addauth"]))
async def addauth_handler(client: Client, m: Message):
    if m.from_user.id != OWNER:
        return await m.reply_text("❌ Only owner can use this command.")
    parts = m.text.split()
    if len(parts) < 2:
        return await m.reply_text("Usage: /addauth <user_id>")
    try:
        uid = int(parts[1])
    except ValueError:
        return await m.reply_text("❌ Invalid user id.")
    auth_users.add(uid)
    _save_auth_users(auth_users)
    await m.reply_text(f"✅ User `{uid}` added to authorized list.")

@bot.on_message(filters.command(["rmauth"]))
async def rmauth_handler(client: Client, m: Message):
    if m.from_user.id != OWNER:
        return await m.reply_text("❌ Only owner can use this command.")
    parts = m.text.split()
    if len(parts) < 2:
        return await m.reply_text("Usage: /rmauth <user_id>")
    try:
        uid = int(parts[1])
    except ValueError:
        return await m.reply_text("❌ Invalid user id.")
    auth_users.discard(uid)
    _save_auth_users(auth_users)
    await m.reply_text(f"✅ User `{uid}` removed from authorized list.")

@bot.on_message(filters.command(["users"]))
async def allusers_handler(client: Client, m: Message):
    if m.from_user.id != OWNER:
        return await m.reply_text("❌ Only owner can use this command.")
    if not auth_users:
        return await m.reply_text("📋 No authorized users yet.")
    user_list = "\n".join([f"• `{uid}`" for uid in auth_users])
    await m.reply_text(f"👥 **Authorized Users ({len(auth_users)}):**\n\n{user_list}")

# ══════════════════════════════════════════════════════════════════════════════
# ── BROADCAST SYSTEM (Owner only — JSON-backed) ───────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["broadcast"]))
async def broadcast_handler(client: Client, m: Message):
    if m.from_user.id != OWNER:
        return await m.reply_text("❌ Only owner can use this command.")
    if not m.reply_to_message:
        return await m.reply_text("📢 Reply to a message to broadcast it.")

    total = len(broadcast_users)
    if total == 0:
        return await m.reply_text("No users to broadcast to yet.")

    status = await m.reply_text(f"📢 Broadcasting to {total} users...")
    success, failed = 0, 0
    for uid in list(broadcast_users):
        try:
            await m.reply_to_message.copy(uid)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await status.edit_text(
        f"📢 **Broadcast Complete!**\n\n"
        f"✅ Success: {success}\n"
        f"❌ Failed: {failed}\n"
        f"👥 Total: {total}"
    )

@bot.on_message(filters.command(["broadusers"]))
async def broadusers_handler(client: Client, m: Message):
    if m.from_user.id != OWNER:
        return await m.reply_text("❌ Only owner can use this command.")
    total = len(broadcast_users)
    if total == 0:
        return await m.reply_text("📋 No broadcast users registered yet.")
    uid_list = "\n".join([f"• `{uid}`" for uid in list(broadcast_users)[:50]])
    suffix = f"\n\n...and {total - 50} more." if total > 50 else ""
    await m.reply_text(f"👥 **Broadcast Users ({total}):**\n\n{uid_list}{suffix}")

# ══════════════════════════════════════════════════════════════════════════════


# ===============================================================
# BOT: PW Callback Handler
# ===============================================================
def _run_async_in_thread(coro):
    """Helper to run async coroutine in ThreadPool"""
    asyncio.run(coro)

@bot.on_callback_query(filters.regex("^pwwp$"))
async def pwwp_callback(bot, callback_query):
    user_id = callback_query.from_user.id
    await callback_query.answer()

    if user_id not in auth_users:
        await bot.send_message(callback_query.message.chat.id, "**You Are Not Subscribed To This Bot\nSo DM me for access\nID: @JapaneseFury**")
        return

    THREADPOOL.submit(_run_async_in_thread, process_pwwp(bot, callback_query.message, user_id))


# ===============================================================
# PAGINATION CALLBACK HANDLERS
# ===============================================================
@bot.on_callback_query(filters.regex(r"^batch_next\|"))
async def batch_next_callback(bot, callback_query):
    """Handle Next page button click"""
    user_id = callback_query.from_user.id
    await callback_query.answer()

    data = user_batch_pages.get(user_id)
    if not data:
        return

    current_page = data.get("page", 0)
    total_pages = (len(data["batches"]) + 9) // 10

    if current_page < total_pages - 1:
        new_page = current_page + 1
        data["page"] = new_page
        user_batch_pages[user_id] = data

        _, text = get_batches_for_page(user_id, new_page)
        keyboard = build_batch_pagination_keyboard(user_id, new_page)

        header = f"**Send index number of the course to download.\n\n{text}\n\nIf Your Batch Not Listed Above Enter contact: @JapaneseFury**"
        try:
            await callback_query.message.edit_text(header, reply_markup=keyboard)
        except Exception as e:
            logging.warning(f"Failed to edit message: {e}")


@bot.on_callback_query(filters.regex(r"^batch_prev\|"))
async def batch_prev_callback(bot, callback_query):
    """Handle Previous page button click"""
    user_id = callback_query.from_user.id
    await callback_query.answer()

    data = user_batch_pages.get(user_id)
    if not data:
        return

    current_page = data.get("page", 0)
    if current_page > 0:
        new_page = current_page - 1
        data["page"] = new_page
        user_batch_pages[user_id] = data

        _, text = get_batches_for_page(user_id, new_page)
        keyboard = build_batch_pagination_keyboard(user_id, new_page)

        header = f"**Send index number of the course to download.\n\n{text}\n\nIf Your Batch Not Listed Above Contact: @JapaneseFury**"
        try:
            await callback_query.message.edit_text(header, reply_markup=keyboard)
        except Exception as e:
            logging.warning(f"Failed to edit message: {e}")


@bot.on_callback_query(filters.regex(r"^batch_page_info$"))
async def batch_page_info_callback(bot, callback_query):
    """Handle page info button click (does nothing, just info)"""
    await callback_query.answer("Use Previous/Next to navigate pages")


# ===============================================================
# PW: Main Processing Function (with ALL fixes integrated)
# ===============================================================
async def process_pwwp(bot, m, user_id):
    editable = await m.reply_text(
        "**Enter Working Access Token\n\nOR\n\nEnter Phone Number(without +91)\n\n"
        "NOTE: Jis Batch Ka Token hai uska Sara Content aayega with Videos & PDFs "
        "But Agar Us Batch ka Token nahi to Sirf Notes & DPP PDFs hi Milegi😒.**"
    )

    try:
        input1 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
        raw_text1 = input1.text
        await input1.delete(True)
    except:
        await editable.edit("**Timeout! You took too long to respond😢\n\nPlease response under 60 seconds🙂.**")
        return

    headers = get_pw_mobile_headers("")
    loop = asyncio.get_event_loop()
    CONNECTOR = aiohttp.TCPConnector(limit=1000, loop=loop)
    direct_token_login = False

    async with aiohttp.ClientSession(connector=CONNECTOR, loop=loop) as session:
        try:
            if raw_text1.isdigit() and len(raw_text1) == 10:
                phone = raw_text1
                data = {
                    "username": phone,
                    "countryCode": "+91",
                    "organizationId": "5eb393ee95fab7468a79d189"
                }
                try:
                    await session.post(
                        f"https://api.penpencil.co/v1/users/get-otp?smsType=0",
                        json=data, headers=get_pw_login_headers()
                    )
                except Exception as e:
                    await editable.edit(f"**Error : {e}**")
                    return

                editable = await editable.edit("**ENTER OTP YOU RECEIVED\n\nOnly Enter 6 digit OTP.**")
                try:
                    input2 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                    otp = input2.text
                    await input2.delete(True)
                except:
                    await editable.edit("**Timeout! You took too long to respond\n\nPlease response under 60 seconds🙂.**")
                    return

                payload = {
                    "username": phone,
                    "otp": otp,
                    "client_id": "system-admin",
                    "client_secret": "KjPXuAVfC5xbmgreETNMaL7z",
                    "grant_type": "password",
                    "organizationId": "5eb393ee95fab7468a79d189",
                    "latitude": 0,
                    "longitude": 0
                }

                try:
                    async with session.post(
                        f"https://api.penpencil.co/v3/oauth/token",
                        json=payload, headers=get_pw_login_headers()
                    ) as response:
                        access_token = (await response.json())["data"]["access_token"]
                        await editable.edit(
                            f"<b>✅Please Renew Token From: @pwextract_bot(New Duplicate token). </b>\n\n"
                            f"<pre language='Save this Login Token for future usage'>{access_token}</pre>\n\n"
                        )
                        editable = await m.reply_text("**Getting ALL Batches...\n\nPlease Wait...🤭**")
                except Exception as e:
                    await editable.edit(f"**Error : {e}**")
                    return

            else:
                access_token = raw_text1
                direct_token_login = True

            headers = get_pw_mobile_headers(access_token)

            # Validate token
            try:
                params = {'mode': '1', 'page': '1'}
                async with session.get(
                    f"https://api.penpencil.co/v3/batches/my-batches",
                    headers=headers, params=params
                ) as response:
                    response.raise_for_status()
                    test_data = await response.json()
                    if not test_data.get("data"):
                        raise Exception("Invalid token")
            except Exception as e:
                await editable.edit(
                    "**```\nLogin Failed TOKEN IS EXPIRED```\n"
                    "Please Enter Working Token\n"
                    "                       OR\n"
                    "Login With Phone Number**"
                )
                return

            if direct_token_login:
                await editable.edit(
                    f"<b>✅Please Renew Token From: @pwextract_bot(New Duplicate token).</b>\n\n"
                    f"<pre language='Save this Login Token for future usage'>{access_token}</pre>\n\n"
                )
                editable = await m.reply_text("**Getting ALL Batches...\n\nPlease Wait...🤭**")

            await editable.edit(
                "**Enter Your Batch Name\n\n"
                "REMEMBER Only Purchased Batch ke Videos hi aayenge Otherwise Sirf PDFs😐.**"
            )
            try:
                input3 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                batch_search = input3.text
                await input3.delete(True)
            except:
                await editable.edit("**Timeout! You took too long to respond😢\n\nPlease response under 60 seconds🙂..**")
                return

            # ==========================================================
            # Fetch ALL purchased batches matching the search term
            # ==========================================================
            all_batches = await fetch_all_pw_batches(session, headers, batch_search)

            if not all_batches:
                raise Exception("No batches found for the given search name.")

            # ==========================================================
            # Pagination system for batch selection
            # Store batches, show 10 per page with Next/Prev buttons
            # ==========================================================
            user_batch_pages[user_id] = {
                "batches": all_batches,
                "page": 0,
                "message_id": None
            }

            # Show first page (10 batches)
            _, text = get_batches_for_page(user_id, 0)
            keyboard = build_batch_pagination_keyboard(user_id, 0)

            total_batches = len(all_batches)
            total_pages = (total_batches + 9) // 10

            header = (
                f"**Send index number of the course to download.\n\n"
                f"{text}\n\n"
                f"Showing page 1 of {total_pages} ({total_batches} total batches)\n"
                f"If Your Batch Not Listed Above Enter Contact: @JapaneseFury**"
            )

            # Edit the message with pagination keyboard
            await editable.edit(header, reply_markup=keyboard)

            try:
                input4 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                raw_text4 = input4.text
                await input4.delete(True)
            except:
                await editable.edit("**Timeout! You took too long to respond😢\n\nPlease response under 60 seconds🙂.**")
                # Clean up pagination state
                user_batch_pages.pop(user_id, None)
                return

            # Clean up pagination state
            page_data = user_batch_pages.pop(user_id, None)

            if input4.text.isdigit():
                selected_index = int(input4.text.strip())
                if 1 <= selected_index <= len(all_batches):
                    course = all_batches[selected_index - 1]
                    selected_batch_id = course['_id']
                    selected_batch_name = course['name']
                    clean_batch_name = selected_batch_name.replace("/", "-").replace("|", "-")
                    clean_file_name = f"{user_id}_{clean_batch_name}"
                else:
                    raise Exception(f"Invalid index. Please enter a number between 1 and {len(all_batches)}")

            elif "No" in input4.text:
                courses = find_pw_old_batch(batch_search)
                if courses:
                    text = ''
                    for cnt, course in enumerate(courses):
                        name = course['batch_name']
                        text += f"{cnt + 1}. ```\n{name}```\n"

                    await editable.edit(f"**Send index number of the course to download.\n\n{text}**")

                    try:
                        input5 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                        raw_text5 = input5.text
                        await input5.delete(True)
                    except:
                        await editable.edit("**Timeout! You took too long to respond😢\n\nPlease response under 60 seconds🙂.**")
                        return

                    if input5.text.isdigit() and 1 <= int(input5.text) <= len(courses):
                        selected_course_index = int(input5.text.strip())
                        course = courses[selected_course_index - 1]
                        selected_batch_id = course['batch_id']
                        selected_batch_name = course['batch_name']
                        clean_batch_name = selected_batch_name.replace("/", "-").replace("|", "-")
                        clean_file_name = f"{user_id}_{clean_batch_name}"
                    else:
                        raise Exception("Invalid batch index.")
                else:
                    raise Exception("No batches found for the given search name.")
            else:
                raise Exception("Invalid input. Please enter a valid index number or 'No'.")

            # selected_batch_id is always the purchased batch's v2 API _id.
            # No separate PW-internal batchId is needed for purchased batches.
            selected_batch_pw_id = None

            # ==========================================================
            # MENU: 1.Full Batch | 2.Today Class | 3.Khazana | 4.Select Date
            # ==========================================================
            await editable.edit(
                f"You Choosed Batch\n**{selected_batch_name}**\n\n"
                "1.```\nFull Batch```\n"
                "2.```\nToday's Class```\n"
                "3.```\nKhazana```\n"
                "4.```\n📅 Select Date```"
            )

            try:
                input6 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                raw_text6 = input6.text
                await input6.delete(True)
            except ListenerTimeout:
                await editable.edit("**Timeout! You took too long to respond😢\n\nPlease response under 60 seconds🙂.**")
                return
            except Exception as e:
                logging.exception("Error during option listening:")
                try:
                    await editable.edit(f"**Error: {e}**")
                except:
                    logging.error(f"Failed to send error message to user: {e}")
                return

            await editable.edit(f"**Extracting course : {selected_batch_name} ...**")
            start_time = time.time()

            # ==========================================================
            # OPTION 1: FULL BATCH
            # CRITICAL FIX: For hardcoded batches, try both _id and batchId
            # ==========================================================
            if input6.text == '1':
                batch_details = None
                for bid, label in [(selected_batch_id, "_id"), (selected_batch_pw_id, "batchId")]:
                    if not bid:
                        continue
                    try:
                        url = f"https://api.penpencil.co/v3/batches/{bid}/details"
                        batch_details = await fetch_pwwp_data(session, url, headers=headers)
                        if batch_details and batch_details.get("success"):
                            logging.info(f"Batch details fetched using {label}={bid}")
                            break
                    except Exception as e:
                        logging.warning(f"Batch details failed with {label}={bid}: {e}")

                if batch_details and batch_details.get("success"):
                    subjects = batch_details.get("data", {}).get("subjects", [])

                    json_data = {selected_batch_name: {}}
                    all_subject_urls = {}

                    with zipfile.ZipFile(f"{clean_file_name}.zip", 'w') as zipf:
                        subject_tasks = [
                            process_pwwp_subject(session, subject, selected_batch_id, selected_batch_name, zipf, json_data, all_subject_urls, headers)
                            for subject in subjects
                        ]
                        await asyncio.gather(*subject_tasks)

                    with open(f"{clean_file_name}.json", 'w') as f:
                        json.dump(json_data, f, indent=4)

                    with open(f"{clean_file_name}.txt", 'w', encoding='utf-8') as f:
                        for subject in subjects:
                            subject_name = safe_topic(subject.get("subject"), "Unknown Subject")
                            if subject_name in all_subject_urls:
                                f.write('\n'.join(all_subject_urls[subject_name]) + '\n')
                else:
                    raise Exception(f"Error fetching batch details: Both IDs failed")

            # ==========================================================
            # OPTION 2: TODAY'S CLASS (using working v1 endpoint)
            # CRITICAL FIX: Now passes batch_pw_id for non-purchased batches
            # and filters out items with None subject_id
            # ==========================================================
            elif input6.text == '2':
                selected_batch_name = "Today's Class"
                today_schedule = await get_pwwp_all_todays_schedule_content(session, selected_batch_id, selected_batch_pw_id, headers)
                if today_schedule:
                    clean_file_name = f"{user_id}_today_class"
                    with open(f"{clean_file_name}.txt", "w", encoding="utf-8") as f:
                        f.writelines(today_schedule)
                else:
                    raise Exception("No classes found for today.\n\nSo Revise today and prepare for tomorrow😊.")

            # ==========================================================
            # OPTION 4: SELECT DATE (DD/MM/YYYY INPUT)
            # User picks a specific past date (or multiple dates with & separator)
            # and we extract every class scheduled on those days (videos + PDFs).
            # Single date:   DD/MM/YYYY          → txt + html (as before)
            # Multiple dates: DD/MM/YYYY&DD/MM/YYYY&...  → only txt files (max 10)
            # ==========================================================
            elif input6.text == '4':
                await editable.edit(
                    "**📅 Select Date\n\n"
                    "Single Date Format:\n"
                    "`16/06/2026`\n\n"
                    "Multiple Dates Format (max 10):\n"
                    "`15/06/2026&16/06/2026&21/09/2025`\n\n"
                    "Connect dates with `&` — no spaces needed.\n\n"
                    "This will extract all classes scheduled on those dates (IST), videos & PDFs both.**"
                )

                try:
                    input7 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                    date_input = input7.text.strip()
                    await input7.delete(True)
                except:
                    await editable.edit("**Timeout! You took too long to respond😢\n\nPlease response under 60 seconds🙂.**")
                    return

                # ── Split on '&' to detect single vs multiple dates ────────────
                raw_date_parts = [d.strip() for d in date_input.split('&') if d.strip()]

                # Limit to maximum 10 dates
                if len(raw_date_parts) > 10:
                    await editable.edit(
                        "**❌ Too Many Dates!\n\n"
                        "Maximum 10 dates allowed at once.\n"
                        "Example (3 dates):\n"
                        "`15/06/2026&16/06/2026&21/09/2025`**"
                    )
                    return

                # Validate all dates first
                parsed_dates = []
                for raw_d in raw_date_parts:
                    s_ep, e_ep, t_date, disp_date = parse_user_date_to_range(raw_d)
                    if s_ep is None:
                        await editable.edit(
                            f"**❌ Invalid Date Format: `{raw_d}`\n\n"
                            "Please send valid dates in DD/MM/YYYY format.\n"
                            "Single:   `16/06/2026`\n"
                            "Multiple: `15/06/2026&16/06/2026&21/09/2025`**"
                        )
                        return
                    parsed_dates.append((s_ep, e_ep, t_date, disp_date))

                total_dates = len(parsed_dates)
                is_multi = total_dates > 1

                # ── MULTIPLE DATE MODE ─────────────────────────────────────────
                if is_multi:
                    date_labels = " | ".join(d[3] for d in parsed_dates)
                    await editable.edit(
                        f"**📅 Fetching classes for {total_dates} dates...\n\n"
                        f"Dates: {date_labels}\n\nPlease Wait...🤭**"
                    )

                    all_sent_message_ids = []

                    for idx, (s_ep, e_ep, t_date, disp_date) in enumerate(parsed_dates, start=1):
                        index_label = f"{idx:02d} of {total_dates:02d}"

                        await editable.edit(
                            f"**📅 Extracting date {index_label}...\n"
                            f"Date: {disp_date}\n\nPlease Wait...🤭**"
                        )

                        txt_path, zip_path, total_schedules, error = await process_date_content(
                            session, selected_batch_id, selected_batch_name,
                            s_ep, e_ep, t_date, headers, user_id
                        )

                        if error or not txt_path or not os.path.exists(txt_path):
                            logging.warning(f"[MultiDate] No content for {disp_date}: {error}")
                            # Inform user and continue to next date
                            err_msg = await m.reply_text(
                                f"**⚠️ {index_label} | {disp_date} — No content found, skipping.**"
                            )
                            all_sent_message_ids.append(err_msg.id)
                            # Clean up any partial files
                            for ext in ['txt', 'zip', 'json']:
                                fp = f"date_{t_date}_{selected_batch_name.replace('/', '_').replace(':', '_').replace('|', '_').replace('?', '_')}.{ext}"
                                if os.path.exists(fp):
                                    try:
                                        os.remove(fp)
                                    except:
                                        pass
                            await asyncio.sleep(2)
                            continue

                        # Rename txt file
                        clean_file_name_multi = f"{user_id}_multidate_{idx}_{disp_date}"
                        clean_batch_name_multi = selected_batch_name.replace('/', '-').replace('|', '-').replace(':', '-')

                        dst_txt = f"{clean_file_name_multi}.txt"
                        try:
                            os.rename(txt_path, dst_txt)
                        except Exception as rename_err:
                            logging.error(f"Rename error for {txt_path}: {rename_err}")
                            dst_txt = txt_path

                        # Clean up zip and json (not needed for multi-date)
                        for ext in ['zip', 'json']:
                            fp = f"date_{t_date}_{selected_batch_name.replace('/', '_').replace(':', '_').replace('|', '_').replace('?', '_')}.{ext}"
                            if os.path.exists(fp):
                                try:
                                    os.remove(fp)
                                except:
                                    pass
                            # Also try the zip_path directly
                            if zip_path and os.path.exists(zip_path):
                                try:
                                    os.remove(zip_path)
                                except:
                                    pass

                        end_time_multi = time.time()
                        resp_time_multi = end_time_multi - start_time
                        mins_m = int(resp_time_multi // 60)
                        secs_m = int(resp_time_multi % 60)
                        fmt_time_multi = f"{mins_m}m {secs_m}s" if mins_m > 0 else f"{secs_m}s"

                        caption_multi = (
                            f"**Index: {index_label}\n"
                            f"Batch Name : ```\n{selected_batch_name}```\n"
                            f"📅 Date: {disp_date}\n"
                            f"📊 Total Classes: {total_schedules}\n"
                            f"Time Taken : {fmt_time_multi}```"
                            f"Extracted By: @JapaneseFury**"
                        )

                        # Send only txt file for multi-date mode (no html)
                        if os.path.exists(dst_txt):
                            _thumb_multi = await get_thumbnail_async()
                            try:
                                with open(dst_txt, 'rb') as f:
                                    sent_msg = await m.reply_document(
                                        document=f,
                                        caption=caption_multi,
                                        file_name=f"{selected_batch_name.replace('/', '-').replace('|', '-').replace(':', '-')}.txt",
                                        thumb=_thumb_multi
                                    )
                                    all_sent_message_ids.append(sent_msg.id)
                                logging.info(f"[MultiDate] Sent txt for {disp_date} ({index_label})")
                            except Exception as send_err:
                                logging.error(f"[MultiDate] Error sending {disp_date}: {send_err}", exc_info=True)
                            finally:
                                try:
                                    os.remove(dst_txt)
                                except:
                                    pass

                            done_msg = await m.reply_text(
                                f"**DONE ✅ {index_label} | {disp_date} Extracted!**"
                            )
                            all_sent_message_ids.append(done_msg.id)

                        # 2-3 second delay between files (Telegram limit safety)
                        await asyncio.sleep(2)

                    await editable.delete(True)

                    # Log all extractions to log channel
                    try:
                        user_info = await bot.get_chat(user_id)
                        await log_extraction_to_channel(
                            bot, user_id, user_info.first_name, user_info.username,
                            selected_batch_name, access_token[:20] + "...",
                            ['txt'], all_sent_message_ids, m.chat.id
                        )
                    except Exception as log_err:
                        logging.warning(f"[MultiDate] Could not log extraction: {log_err}")
                    return

                # ── SINGLE DATE MODE (original behaviour, unchanged) ───────────
                else:
                    s_ep, e_ep, t_date, disp_date = parsed_dates[0]

                    await editable.edit(f"**📅 Fetching classes for {disp_date}...\n\nPlease Wait...🤭**")

                    txt_path, zip_path, total_schedules, error = await process_date_content(
                        session, selected_batch_id, selected_batch_name, s_ep, e_ep, t_date, headers, user_id
                    )

                    if error:
                        await editable.edit(f"**⚠️ {error}**")
                        return

                    if txt_path and os.path.exists(txt_path):
                        clean_file_name = f"{user_id}_date_{disp_date}"
                        # Rename files
                        for ext in ['txt', 'zip', 'json']:
                            src = f"date_{t_date}_{selected_batch_name.replace('/', '_').replace(':', '_').replace('|', '_').replace('?', '_')}.{ext}"
                            if ext == 'txt':
                                src = txt_path
                            elif ext == 'zip':
                                src = zip_path
                            if os.path.exists(src):
                                dst = f"{clean_file_name}.{ext}"
                                os.rename(src, dst)

                        end_time = time.time()
                        response_time = end_time - start_time
                        minutes = int(response_time // 60)
                        seconds = int(response_time % 60)
                        formatted_time = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"

                        await editable.delete(True)

                        caption = (
                            f"**Batch Name : ```\n{selected_batch_name}```\n"
                            f"📅 Date: {disp_date}\n"
                            f"📊 Total Classes: {total_schedules}\n"
                            f"Time Taken : {formatted_time}```"
                            f"Extracted By: @JapaneseFury**"
                        )

                        # Send files for single date: txt + html (original behaviour)
                        files_to_send = ['txt', 'html']

                        # Generate HTML from txt content for calendar
                        try:
                            with open(f"{clean_file_name}.txt", 'r', encoding='utf-8') as f:
                                txt_content = f.read()

                            items = []
                            for line in txt_content.strip().split('\n'):
                                if ':' in line:
                                    parts = line.split(':', 1)
                                    items.append({
                                        "title": parts[0].strip(),
                                        "url": parts[1].strip(),
                                        "type": "video" if any(ext in parts[1].lower() for ext in ['.mpd', '.m3u8']) else "file"
                                    })

                            html_data = {selected_batch_name: {"Schedule": {"Items": items}}}
                            logging.info(f"Generating HTML for calendar {t_date} ({len(items)} items)")
                            html_content = generate_html_from_json(selected_batch_name, html_data, access_token)
                            with open(f"{clean_file_name}.html", 'w', encoding='utf-8') as f:
                                f.write(html_content)
                            logging.info(f"HTML file created for calendar: {clean_file_name}.html")
                        except Exception as e:
                            logging.error(f"Could not generate HTML for calendar: {e}", exc_info=True)

                        # Send the files and capture message IDs for forwarding
                        sent_message_ids = []
                        for ext in files_to_send:
                            fp = f"{clean_file_name}.{ext}"
                            if os.path.exists(fp):
                                _thumb_single = await get_thumbnail_async()
                                try:
                                    with open(fp, 'rb') as f:
                                        sent_msg = await m.reply_document(
                                            document=f,
                                            caption=caption if ext == 'txt' else f"{selected_batch_name} - Study Page",
                                            file_name=f"{selected_batch_name.replace('/', '-').replace('|', '-')}.{ext}",
                                            thumb=_thumb_single
                                        )
                                        sent_message_ids.append(sent_msg.id)
                                    logging.info(f"Sent {ext} file to user")
                                except Exception as e:
                                    logging.error(f"Error sending {ext} file: {e}", exc_info=True)
                                finally:
                                    try:
                                        os.remove(fp)
                                    except:
                                        pass

                                if ext == 'txt':
                                    done_msg = await m.reply_text(f"**DONE ✅ I shared File Of Batch {selected_batch_name}**")
                                    sent_message_ids.append(done_msg.id)

                        # Log extraction + forward files to log channel
                        user_info = await bot.get_chat(user_id)
                        await log_extraction_to_channel(bot, user_id, user_info.first_name, user_info.username, selected_batch_name, access_token[:20] + "...", files_to_send, sent_message_ids, m.chat.id)
                        return
                    else:
                        await editable.edit(f"**⚠️ No content found for {disp_date}**")
                        return

            else:
                raise Exception("Invalid index.")

            # ==========================================================
            # Send output files (for options 1, 2)
            # Determine which files to send based on what was generated
            # ==========================================================
            
            # Generate HTML from JSON if it exists
            html_generated = False
            try:
                if os.path.exists(f"{clean_file_name}.json"):
                    try:
                        with open(f"{clean_file_name}.json", 'r', encoding='utf-8') as f:
                            json_data = json.load(f)
                        logging.info(f"Generating HTML from JSON for {selected_batch_name}")
                        html_content = generate_html_from_json(selected_batch_name, json_data, access_token)
                        with open(f"{clean_file_name}.html", 'w', encoding='utf-8') as f:
                            f.write(html_content)
                        logging.info(f"HTML file created: {clean_file_name}.html")
                        html_generated = True
                    except Exception as e:
                        logging.error(f"Error generating HTML from JSON: {e}", exc_info=True)
                elif os.path.exists(f"{clean_file_name}.txt"):
                    # For today's class, generate simple HTML from txt
                    try:
                        with open(f"{clean_file_name}.txt", 'r', encoding='utf-8') as f:
                            txt_content = f.read()
                        
                        # Parse txt content into structured data
                        items = []
                        for line in txt_content.strip().split('\n'):
                            if ':' in line:
                                parts = line.split(':', 1)
                                items.append({
                                    "title": parts[0].strip(),
                                    "url": parts[1].strip(),
                                    "type": "video" if any(ext in parts[1].lower() for ext in ['.mpd', '.m3u8']) else "file"
                                })
                        
                        html_data = {selected_batch_name: {"Content": {"Items": items}}}
                        logging.info(f"Generating HTML from txt for {selected_batch_name} ({len(items)} items)")
                        html_content = generate_html_from_json(selected_batch_name, html_data, access_token)
                        with open(f"{clean_file_name}.html", 'w', encoding='utf-8') as f:
                            f.write(html_content)
                        logging.info(f"HTML file created from txt: {clean_file_name}.html")
                        html_generated = True
                    except Exception as e:
                        logging.error(f"Error generating HTML from txt: {e}", exc_info=True)
            except Exception as e:
                logging.error(f"Unexpected error in HTML generation: {e}", exc_info=True)
            
            # Determine which files to send
            if os.path.exists(f"{clean_file_name}.zip"):
                # Option 1: Full Batch -> send txt, zip, json, html
                files_to_send = ["txt", "zip", "json", "html"]
            else:
                # Option 2: Today's Class -> send txt, html only
                files_to_send = ["txt", "html"]
            
            end_time = time.time()
            response_time = end_time - start_time
            minutes = int(response_time // 60)
            seconds = int(response_time % 60)

            if minutes == 0:
                if seconds < 1:
                    formatted_time = f"{response_time:.2f} seconds"
                else:
                    formatted_time = f"{seconds} seconds"
            else:
                formatted_time = f"{minutes} minutes {seconds} seconds"

            await editable.delete(True)

            caption = f"**Batch Name : ```\n{selected_batch_name}``````\nTime Taken : {formatted_time}```\n\nExtracted By: @JapaneseFury**"

            # Send files and capture message IDs for log channel forwarding
            sent_message_ids = []
            for ext in files_to_send:
                file = f"{clean_file_name}.{ext}"
                if os.path.exists(file):
                    try:
                        with open(file, 'rb') as f:
                            doc = await m.reply_document(
                                document=f,
                                caption=caption if ext == 'txt' else f"{selected_batch_name} - {ext.upper() if ext != 'html' else 'Study Page'}",
                                file_name=f"{clean_batch_name}.{ext}",
                                thumb=await get_thumbnail_async()
                            )
                            sent_message_ids.append(doc.id)
                        if ext == 'txt':
                            done_msg = await m.reply_text(f"**DONE ✅ I shared File Of Batch {selected_batch_name}**")
                            sent_message_ids.append(done_msg.id)
                    except FileNotFoundError:
                        logging.error(f"File not found: {file}")
                    except Exception as e:
                        logging.exception(f"Error sending document {file}:")
                    finally:
                        try:
                            os.remove(file)
                            logging.info(f"Removed File After Sending : {file}")
                        except OSError as e:
                            logging.error(f"Error deleting {file}: {e}")
            
            # Log extraction + forward files to log channel
            try:
                user_info = await bot.get_chat(user_id)
                await log_extraction_to_channel(bot, user_id, user_info.first_name, user_info.username, selected_batch_name, access_token[:20] + "...", files_to_send, sent_message_ids, m.chat.id)
            except Exception as e:
                logging.warning(f"Could not log extraction: {e}")

        except Exception as e:
            logging.exception(f"An unexpected error occurred: {e}")
            try:
                await editable.edit(f"**Error : {e}**")
            except Exception as ee:
                logging.error(f"Failed to send error message to user in callback: {ee}")
        finally:
            # Clean up pagination state
            user_batch_pages.pop(user_id, None)
            user_batch_selecting.discard(user_id)
            if session:
                await session.close()
            await CONNECTOR.close()


# ===============================================================
# START: Flask + Bot
# ===============================================================
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run()
