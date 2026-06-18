import requests
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
from pyrogram.enums import ChatMemberStatus
from pyrogram.raw.functions.channels import GetParticipants
from config import api_id, api_hash, bot_token, auth_users
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

# Flask app for Render
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app_flask.run(host="0.0.0.0", port=8000)

image_list = [
    "https://graph.org/file/d24b9bd4d0592a07ad746-de047531c5efafafce.jpg",
    "https://graph.org/file/06d5077e2fe5442e1dbb4-77cb51eecc0aab0608.jpg",
    "https://graph.org/file/8ea482ae6278601bae5c5-b1475ac9b0622a6cd7.jpg",
    "https://graph.org/file/5312e32455e56860c75cb-b56bedb77b7cf93227.jpg",
    "https://graph.org/file/977afb0f88089d227a19d-443ba34add7d83a182.jpg",
]
print(4321)


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
        for key in ["topic", "name", "title", "subject", "text", "value", "label", "display"]:
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
            line += f" | Keys:{ '|'.join(str(k) for k in video_info['drm_keys'])}"
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
    """Append parentId, childId, videoId to video URL."""
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

    separator = '&' if '?' in video_url else '?'
    param_string = separator + '&'.join(params)
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
            'limit': '100',
            'page': '1',
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
# TIMESTAMP CONVERSION: User timestamp -> Date Range (IST based)
# ===============================================================
def parse_user_timestamp_to_date_range(timestamp_str: str):
    """
    User sends a timestamp in milliseconds (assumed to be in IST).
    Convert to:
    - start_epoch: 12:00 AM IST of that day (in UTC ms)
    - end_epoch: user's timestamp (in UTC ms)
    Returns: (start_epoch_ms, end_epoch_ms, date_str_yyyy_mm_dd, display_date_str)
    """
    try:
        user_timestamp = int(timestamp_str)
    except ValueError:
        return None, None, None, None

    # The timestamp is in ms since epoch (UTC-based)
    # Convert to datetime in IST
    ts_sec = user_timestamp / 1000
    dt_utc = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    dt_ist = dt_utc.astimezone(IST)

    # Start of day in IST: same date, 00:00:00 IST
    start_dt_ist = dt_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    # Convert back to UTC for API calls
    start_dt_utc = start_dt_ist.astimezone(timezone.utc)
    start_epoch = int(start_dt_utc.timestamp() * 1000)

    # End is user's timestamp
    end_epoch = user_timestamp

    date_str = dt_ist.strftime("%Y-%m-%d")
    display_date = dt_ist.strftime("%d-%m-%Y")

    return start_epoch, end_epoch, date_str, display_date


# ===============================================================
# ENHANCED: Fetch schedule for SPECIFIC DATE RANGE
# Uses multiple endpoint strategies for maximum compatibility
# ===============================================================
async def fetch_schedule_by_date_range(session, batch_id, start_epoch, end_epoch, headers):
    """
    Fetch scheduled classes for a date range from PW API.
    Uses multiple endpoint strategies for maximum compatibility.
    Uses consistent API versions: v1 as primary, v2/v3 as fallback.
    """
    all_schedules = []

    # Strategy 1: Try v1 schedule endpoint (most reliable)
    try:
        url = f"https://api.penpencil.co/v1/batches/{batch_id}/schedule"
        params = {
            "startDate": start_epoch,
            "endDate": end_epoch,
            "page": 1
        }
        data = await fetch_pwwp_data(session, url, headers=headers, params=params)
        if data and data.get("data"):
            items = data["data"]
            if isinstance(items, list):
                for item in items:
                    item["_source"] = "v1-schedule"
                all_schedules.extend(items)
            elif isinstance(items, dict) and items.get("data"):
                for item in items["data"]:
                    item["_source"] = "v1-schedule"
                all_schedules.extend(items["data"])
    except Exception as e:
        logging.warning(f"v1/schedule endpoint failed: {e}")

    # Strategy 2: Try v2 schedule endpoint
    if not all_schedules:
        try:
            url = f"https://api.penpencil.co/v2/batches/{batch_id}/schedule"
            params = {
                "startDate": start_epoch,
                "endDate": end_epoch,
                "page": 1
            }
            data = await fetch_pwwp_data(session, url, headers=headers, params=params)
            if data and data.get("data"):
                items = data["data"]
                if isinstance(items, list):
                    for item in items:
                        item["_source"] = "v2-schedule"
                    all_schedules.extend(items)
                elif isinstance(items, dict) and items.get("data"):
                    for item in items["data"]:
                        item["_source"] = "v2-schedule"
                    all_schedules.extend(items["data"])
        except Exception as e:
            logging.warning(f"v2/schedule endpoint failed: {e}")

    # Strategy 3: Try v3 schedule endpoint
    if not all_schedules:
        try:
            url = f"https://api.penpencil.co/v3/batches/{batch_id}/schedule"
            params = {
                "startDate": start_epoch,
                "endDate": end_epoch,
                "page": 1
            }
            data = await fetch_pwwp_data(session, url, headers=headers, params=params)
            if data and data.get("data"):
                items = data["data"]
                if isinstance(items, list):
                    for item in items:
                        item["_source"] = "v3-schedule"
                    all_schedules.extend(items)
                elif isinstance(items, dict) and items.get("data"):
                    for item in items["data"]:
                        item["_source"] = "v3-schedule"
                    all_schedules.extend(items["data"])
        except Exception as e:
            logging.warning(f"v3/schedule endpoint failed: {e}")

    # Strategy 4: Try todays-schedule and filter (if date range includes today)
    if not all_schedules:
        try:
            url = f"https://api.penpencil.co/v1/batches/{batch_id}/todays-schedule"
            data = await fetch_pwwp_data(session, url, headers=headers)
            if data and data.get("data"):
                items = data["data"]
                if isinstance(items, list):
                    for item in items:
                        # Check if item falls within date range
                        item_start = item.get("startTime", item.get("startDate", 0))
                        try:
                            if item_start and start_epoch <= int(item_start) <= end_epoch:
                                item["_source"] = "todays-schedule"
                                all_schedules.append(item)
                        except (ValueError, TypeError):
                            # If can't compare, include anyway
                            item["_source"] = "todays-schedule"
                            all_schedules.append(item)
        except Exception as e:
            logging.warning(f"todays-schedule endpoint failed: {e}")

    return all_schedules


# ===============================================================
# CONTENT-BASED fallback: Fetch schedule via subject contents
# Works when schedule endpoints fail (non-purchased batches)
# ===============================================================
async def fetch_schedule_via_contents(session, batch_id, start_epoch, end_epoch, headers):
    """
    Fallback: Get batch subjects -> fetch all contents -> filter by date.
    This works for ANY batch including non-purchased.
    """
    all_items = []

    # Get batch details for subjects
    detail_url = f"https://api.penpencil.co/v3/batches/{batch_id}/details"
    batch_resp = await fetch_pwwp_data(session, detail_url, headers=headers)

    if not batch_resp or not batch_resp.get("success"):
        return []

    subjects = batch_resp.get("data", {}).get("subjects", [])
    if not subjects:
        return []

    for subject in subjects:
        subject_id = subject.get("_id")
        if not subject_id:
            continue

        # Get chapters for this subject
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

        # For each chapter, fetch contents and filter by date
        for chapter in chapters:
            chapter_id = chapter.get("_id", "")
            if not chapter_id:
                continue

            for content_type in ['videos', 'notes', 'DppNotes', 'DppVideos']:
                page = 1
                while page <= 20:
                    url = f"https://api.penpencil.co/v2/batches/{batch_id}/subject/{subject_id}/contents"
                    params = {
                        "tag": chapter_id,
                        "contentType": content_type,
                        "page": page
                    }
                    data = await fetch_pwwp_data(session, url, headers=headers, params=params)
                    if not data or not data.get("data"):
                        break

                    items = data["data"]
                    for item in items:
                        # Check if item's startTime falls within range
                        item_start = item.get("startTime", item.get("startDate", 0))
                        if item_start:
                            try:
                                item_start_ms = int(item_start)
                                if start_epoch <= item_start_ms <= end_epoch:
                                    item["_contentType"] = content_type
                                    item["_subjectId"] = subject_id
                                    item["_batchId"] = batch_id
                                    item["_source"] = "content-filter"
                                    all_items.append(item)
                            except (ValueError, TypeError):
                                pass

                    if not data.get("hasMore", True) or len(items) < 20:
                        break
                    page += 1

    return all_items


# ===============================================================
# ENHANCED: Process content for a specific date using user timestamp
# Uses both schedule endpoint + content-based fallback
# Now properly uses IST and user's timestamp as endDate
# ===============================================================
async def process_date_content(session, batch_id, batch_name, start_epoch, end_epoch, target_date, headers, user_id):
    """
    Process content extraction for a specific date range.
    start_epoch = 12:00 AM IST of the target day
    end_epoch = user's provided timestamp
    Returns: (txt_path, zip_path, total_schedules, error_msg)
    """
    # Get batch details for subjects
    detail_url = f"https://api.penpencil.co/v3/batches/{batch_id}/details"
    batch_resp = await fetch_pwwp_data(session, detail_url, headers=headers)

    if not batch_resp or not batch_resp.get("success"):
        return None, None, 0, "Failed to fetch batch details"

    subjects = batch_resp.get("data", {}).get("subjects", [])
    if not subjects:
        return None, None, 0, "No subjects found in batch"

    # Fetch schedule for the date range (try multiple strategies)
    schedules = await fetch_schedule_by_date_range(session, batch_id, start_epoch, end_epoch, headers)

    # If no schedules found, try content-based fallback
    if not schedules:
        logging.info("No schedules from endpoint, trying content-based fallback")
        schedules = await fetch_schedule_via_contents(session, batch_id, start_epoch, end_epoch, headers)

    if not schedules:
        return None, None, 0, f"No classes scheduled for {target_date}"

    # Build subject lookup
    subject_map = {}
    for subj in subjects:
        sid = subj.get("_id")
        sname = safe_topic(subj.get("subject"), "Unknown")
        subject_map[sid] = sname

    # Process each scheduled item
    all_urls = []
    structured_data = {}
    dedup = ContentDeduplicator()

    clean_batch_name = batch_name.replace('/', '_').replace(':', '_').replace('|', '_').replace('?', '_')

    for schedule_item in schedules:
        # Extract subject_id
        raw_subject = schedule_item.get("subject", "")
        subject_id = ""
        if isinstance(raw_subject, list) and raw_subject:
            first = raw_subject[0]
            subject_id = first.get("_id", "") if isinstance(first, dict) else str(first)
        elif isinstance(raw_subject, dict):
            subject_id = raw_subject.get("_id", "")
        else:
            subject_id = str(raw_subject) if raw_subject else ""

        if not subject_id:
            subject_id = schedule_item.get("subjectId", "") or schedule_item.get("batchSubjectId", "")

        schedule_id = schedule_item.get("_id", "")

        # CRITICAL FIX: Use safe_topic to handle dict/None topic properly
        topic = safe_topic(
            schedule_item.get("topic") or schedule_item.get("name"),
            "Unknown Topic"
        )

        start_time = schedule_item.get("startTime", schedule_item.get("startDate", ""))
        end_time_val = schedule_item.get("endTime", schedule_item.get("endDate", ""))

        # CRITICAL FIX: Use safe_topic for subject_name too
        subject_name = subject_map.get(str(subject_id), "")
        if not subject_name:
            if isinstance(raw_subject, dict):
                subject_name = safe_topic(raw_subject.get("subject") or raw_subject.get("name"), "")
            if not subject_name:
                subject_name = safe_topic(schedule_item.get("subjectName"), "Unknown Subject")

        if subject_name not in structured_data:
            structured_data[subject_name] = []

        # Fetch schedule-details for video URL
        video_lines = []
        notes_lines = []

        if subject_id and schedule_id:
            try:
                detail_url = f"https://api.penpencil.co/v3/batches/{batch_id}/subject/{subject_id}/schedule/{schedule_id}/schedule-details"
                detail_data = await fetch_pwwp_data(session, detail_url, headers=headers)

                if detail_data and detail_data.get("data"):
                    detail_item = detail_data["data"]

                    # CRITICAL FIX: Always use comprehensive extractor with IDs first
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
                        video_lines.append(f"{topic}:{vurl}{drm}")

                    # Method 2: Fallback using extract_video_data_from_schedule with IDs
                    if not video_lines:
                        video_info = extract_video_data_from_schedule(detail_data)
                        fallback_lines = format_video_line(topic, video_info, parent_id, child_id, vid)
                        video_lines.extend(fallback_lines)

                    # Extract notes with deduplication
                    for hw in detail_item.get('homeworkIds', []):
                        for att in hw.get('attachmentIds', []):
                            # CRITICAL FIX: Use safe_topic for note names
                            name = safe_topic(att.get('name'), topic)
                            base_url = att.get('baseUrl', '')
                            key = att.get('key', '')
                            if base_url and key:
                                nline = f"{name}:{base_url}{key}"
                                if dedup.add_and_check_unique(nline):
                                    notes_lines.append(nline)

                    # Extract DPP notes (only from dpp.homeworkIds)
                    dpp = detail_item.get('dpp')
                    if dpp:
                        for hw in dpp.get('homeworkIds', []):
                            for att in hw.get('attachmentIds', []):
                                name = safe_topic(att.get('name'), topic)
                                base_url = att.get('baseUrl', '')
                                key = att.get('key', '')
                                if base_url and key:
                                    nline = f"{name}:{base_url}{key}"
                                    if dedup.add_and_check_unique(nline):
                                        notes_lines.append(nline)
            except Exception as e:
                logging.warning(f"Error fetching schedule-details: {e}")

        # Fallback: check if schedule item itself has URL
        if not video_lines:
            item_url = schedule_item.get('url', '')
            if item_url:
                # Append IDs to item URL too
                final_url = append_video_params(item_url, batch_id, schedule_id, '')
                video_lines.append(f"{topic}:{final_url}")

        # Also try fallback video extraction from item
        if not video_lines:
            vurl, drm = extract_video_from_content_item(schedule_item)
            if vurl:
                # Ensure IDs are in fallback URL
                parent_id_fb = batch_id
                child_id_fb = schedule_id
                vid_fb = schedule_item.get('videoId', '') or schedule_item.get('contentId', '')
                vurl = append_video_params(vurl, parent_id_fb, child_id_fb, vid_fb)
                video_lines.append(f"{topic}:{vurl}{drm}")

        # Apply deduplication to videos too
        for vline in video_lines:
            if dedup.add_and_check_unique(vline):
                all_urls.append(vline)
        for nline in notes_lines:
            if dedup.add_and_check_unique(nline):
                all_urls.append(nline)

        structured_data[subject_name].append({
            "topic": topic,
            "start_time": start_time,
            "end_time": end_time_val,
            "videos": video_lines,
            "notes": notes_lines
        })

    # === CREATE OUTPUT FILES ===
    file_path_base = f"date_{target_date}_{clean_batch_name}"

    # 1. TXT file
    txt_path = f"{file_path_base}.txt"
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(f"=== {batch_name} - Classes for {target_date} ===\n\n")
        for subject_name, items in structured_data.items():
            f.write(f"\n--- {subject_name} ---\n")
            for item in items:
                f.write(f"\nTopic: {item['topic']}\n")
                f.write(f"Time: {item['start_time']} - {item['end_time']}\n")
                if item["videos"]:
                    f.write("\n[Videos]\n")
                    f.write("\n".join(item["videos"]) + "\n")
                if item["notes"]:
                    f.write("\n[Notes]\n")
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
            json_data[batch_name][topic_key] = {
                "videos": item["videos"],
                "notes": item["notes"]
            }
    json_data[batch_name]["date"] = target_date
    json_data[batch_name]["total_schedules"] = len(schedules)
    with open(json_path, 'w') as f:
        json.dump(json_data, f, indent=4)

    return txt_path, zip_path, len(schedules), None


# ===============================================================
# TODAY'S CLASS: Using working v1 todays-schedule endpoint
# EXACTLY as in workingmain.py - DO NOT MODIFY
# ===============================================================
async def get_pwwp_todays_schedule_content_details(session: aiohttp.ClientSession, selected_batch_id, subject_id, schedule_id, headers: Dict) -> List[str]:
    """Fetch content details for a single today's schedule item."""
    url = f"https://api.penpencil.co/v1/batches/{selected_batch_id}/subject/{subject_id}/schedule/{schedule_id}/schedule-details"
    data = await fetch_pwwp_data(session, url, headers)
    content = []

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


async def get_pwwp_all_todays_schedule_content(session: aiohttp.ClientSession, selected_batch_id: str, headers: Dict) -> List[str]:
    """Fetch all of today's schedule content using the working v1 endpoint."""
    url = f"https://api.penpencil.co/v1/batches/{selected_batch_id}/todays-schedule"
    todays_schedule_details = await fetch_pwwp_data(session, url, headers)
    all_content = []

    if todays_schedule_details and todays_schedule_details.get("success") and todays_schedule_details.get("data"):
        tasks = []

        for item in todays_schedule_details['data']:
            schedule_id = item.get('_id')
            subject_id = item.get('batchSubjectId')

            task = asyncio.create_task(get_pwwp_todays_schedule_content_details(session, selected_batch_id, subject_id, schedule_id, headers))
            tasks.append(task)

        results = await asyncio.gather(*tasks)

        for result in results:
            all_content.extend(result)

    else:
        logging.warning("No today's schedule data found.")

    return all_content


# ===============================================================
# BOT: Start Command
# ===============================================================
@bot.on_message(filters.command(["start"]))
async def start(bot, message):
    random_image_url = random.choice(image_list)
    keyboard = [
        [InlineKeyboardButton("🚀 PHYSICS WALLAH 🚀 ", callback_data="pwwp")],
        [InlineKeyboardButton("🚀 CLASSPLUS APPS 🚀 ", callback_data="cpwp")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await message.reply_photo(
        photo=random_image_url,
        caption="PLEASE PRESS HERE",
        quote=True,
        reply_markup=reply_markup
    )


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
        await bot.send_message(callback_query.message.chat.id, "**You Are Not Subscribed To This Bot\nSo DM me for access\nID: @SmartBoy_ApnaMS**")
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

        header = f"**Send index number of the course to download.\n\n{text}\n\nIf Your Batch Not Listed Above Enter - No**"
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

        header = f"**Send index number of the course to download.\n\n{text}\n\nIf Your Batch Not Listed Above Enter - No**"
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
        "**Enter Working Access Token\n\nOR\n\nEnter Phone Number\n\n"
        "Ab koi bhi valid PW token daalo, ALL batches ka content niklega "
        "with Videos, PDFs & Video IDs!**"
    )

    try:
        input1 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
        raw_text1 = input1.text
        await input1.delete(True)
    except:
        await editable.edit("**Timeout! You took too long to respond😢.**")
        return

    headers = get_pw_mobile_headers("")
    loop = asyncio.get_event_loop()
    CONNECTOR = aiohttp.TCPConnector(limit=1000, loop=loop)

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

                editable = await editable.edit("**ENTER OTP YOU RECEIVED**")
                try:
                    input2 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                    otp = input2.text
                    await input2.delete(True)
                except:
                    await editable.edit("**Timeout! You took too long to respond**")
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
                            f"<b>Physics Wallah Login Successful </b>\n\n"
                            f"<pre language='Save this Login Token for future usage'>{access_token}</pre>\n\n"
                        )
                        editable = await m.reply_text("**Getting ALL Batches...**")
                except Exception as e:
                    await editable.edit(f"**Error : {e}**")
                    return

            else:
                access_token = raw_text1

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

            await editable.edit(
                "**Enter Your Batch Name\n\n"
                "Ab ALL batches milenge - purchased ho ya nahi! "
                "Videos + PDFs dono!**"
            )
            try:
                input3 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                batch_search = input3.text
                await input3.delete(True)
            except:
                await editable.edit("**Timeout! You took too long to respond😢.**")
                return

            # Fetch ALL batches from multiple sources
            all_batches = await fetch_all_pw_batches(session, headers, batch_search)

            if all_batches:
                # ==========================================================
                # CRITICAL FIX: Pagination system for batch selection
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
                    f"If Your Batch Not Listed Above Enter - No**"
                )

                # Edit the message with pagination keyboard
                await editable.edit(header, reply_markup=keyboard)

                try:
                    input4 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                    raw_text4 = input4.text
                    await input4.delete(True)
                except:
                    await editable.edit("**Timeout! You took too long to respond😢.**")
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
                            await editable.edit("**Timeout! You took too long to respond😢.**")
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

                # ==========================================================
                # MENU: 1.Full Batch | 2.Today Class | 3.Khazana | 4.Select Date
                # ==========================================================
                await editable.edit(
                    "1.```\nFull Batch```\n"
                    "2.```\nToday's Class```\n"
                    "3.```\nKhazana```\n"
                    "4.```\n📅 Select Date (Send Timestamp)```"
                )

                try:
                    input6 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                    raw_text6 = input6.text
                    await input6.delete(True)
                except ListenerTimeout:
                    await editable.edit("**Timeout! You took too long to respond😢.**")
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
                # ==========================================================
                if input6.text == '1':
                    url = f"https://api.penpencil.co/v3/batches/{selected_batch_id}/details"
                    batch_details = await fetch_pwwp_data(session, url, headers=headers)

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
                        raise Exception(f"Error fetching batch details: {batch_details.get('message')}")

                # ==========================================================
                # OPTION 2: TODAY'S CLASS (using working v1 endpoint)
                # EXACTLY as in workingmain.py - DO NOT MODIFY
                # ==========================================================
                elif input6.text == '2':
                    selected_batch_name = "Today's Class"
                    today_schedule = await get_pwwp_all_todays_schedule_content(session, selected_batch_id, headers)
                    if today_schedule:
                        clean_file_name = f"{user_id}_today_class"
                        with open(f"{clean_file_name}.txt", "w", encoding="utf-8") as f:
                            f.writelines(today_schedule)
                    else:
                        raise Exception("No Classes Found Today")

                # ==========================================================
                # OPTION 3: KHAZANA
                # ==========================================================
                elif input6.text == '3':
                    raise Exception("Working In Progress")

                # ==========================================================
                # OPTION 4: SELECT DATE (TIMESTAMP INPUT)
                # FIXED: Now uses IST timezone and user's timestamp as endDate
                # ==========================================================
                elif input6.text == '4':
                    await editable.edit(
                        "**📅 Select Date\n\n"
                        "Send Date Timestamp (in milliseconds):\n\n"
                        "Example:\n"
                        "```1781717400000```\n\n"
                        "This will extract all classes from 12:00 AM IST of that date "
                        "upto the time you specified.**"
                    )

                    try:
                        input7 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                        timestamp_input = input7.text.strip()
                        await input7.delete(True)
                    except:
                        await editable.edit("**Timeout! You took too long to respond😢.**")
                        return

                    # Parse timestamp to date range (IST based)
                    start_epoch, end_epoch, target_date, display_date = parse_user_timestamp_to_date_range(timestamp_input)

                    if start_epoch is None:
                        await editable.edit(
                            "**❌ Invalid Timestamp!\n\n"
                            "Please send a valid numeric timestamp in milliseconds.\n"
                            "Example: ```1781717400000```**"
                        )
                        return

                    await editable.edit(f"**📅 Fetching classes for {display_date} (timestamp: {timestamp_input})...**")

                    txt_path, zip_path, total_schedules, error = await process_date_content(
                        session, selected_batch_id, selected_batch_name, start_epoch, end_epoch, target_date, headers, user_id
                    )

                    if error:
                        await editable.edit(f"**⚠️ {error}**")
                        return

                    if txt_path and os.path.exists(txt_path):
                        clean_file_name = f"{user_id}_date_{display_date}"
                        # Rename files
                        for ext in ['txt', 'zip', 'json']:
                            src = f"date_{target_date}_{selected_batch_name.replace('/', '_').replace(':', '_').replace('|', '_').replace('?', '_')}.{ext}"
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
                            f"📅 Date: {display_date}\n"
                            f"📊 Total Classes: {total_schedules}\n"
                            f"Time Taken : {formatted_time}```**"
                        )

                        # Send files
                        for ext in ['txt', 'zip', 'json']:
                            fp = f"{clean_file_name}.{ext}"
                            if os.path.exists(fp):
                                with open(fp, 'rb') as f:
                                    await m.reply_document(
                                        document=f,
                                        caption=caption if ext == 'txt' else f"{selected_batch_name} - {ext.upper()}",
                                        file_name=f"{selected_batch_name.replace('/', '-').replace('|', '-')}_{display_date}.{ext}"
                                    )
                                os.remove(fp)
                        return  # Already sent files
                    else:
                        await editable.edit(f"**⚠️ No content found for {display_date}**")
                        return

                else:
                    raise Exception("Invalid index.")

                # ==========================================================
                # Send output files (for options 1, 2)
                # ==========================================================
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

                caption = f"**Batch Name : ```\n{selected_batch_name}``````\nTime Taken : {formatted_time}```**"

                files = [f"{clean_file_name}.{ext}" for ext in ["txt", "zip", "json"]]
                for file in files:
                    file_ext = os.path.splitext(file)[1][1:]
                    try:
                        with open(file, 'rb') as f:
                            doc = await m.reply_document(document=f, caption=caption, file_name=f"{clean_batch_name}.{file_ext}")
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
            else:
                raise Exception("No batches found for the given search name.")

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
# CP (Classplus) Functions - UNCHANGED
# ===============================================================
async def fetch_cpwp_signed_url(url_val, name, session, headers):
    MAX_RETRIES = 3
    for attempt in range(MAX_RETRIES):
        params = {"url": url_val}
        try:
            async with session.get("https://api.classplusapp.com/cams/uploader/video/jw-signed-url", params=params, headers=headers) as response:
                response.raise_for_status()
                response_json = await response.json()
                signed_url = response_json.get("url") or response_json.get('drmUrls', {}).get('manifestUrl')
                return signed_url
        except Exception as e:
            pass
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(2 ** attempt)
    logging.error(f"Failed to fetch signed URL for {name} after {MAX_RETRIES} attempts.")
    return None

async def process_cpwp_url(url_val, name, session, headers):
    try:
        signed_url = await fetch_cpwp_signed_url(url_val, name, session, headers)
        if not signed_url:
            logging.warning(f"Failed to obtain signed URL for {name}: {url_val}")
            return None
        if "testbook.com" in url_val or "classplusapp.com/drm" in url_val or "media-cdn.classplusapp.com/drm" in url_val:
            return f"{name}:{url_val}\n"
        async with session.get(signed_url) as response:
            response.raise_for_status()
            return f"{name}:{url_val}\n"
    except Exception as e:
        pass
    return None


async def get_cpwp_course_content(session, headers, Batch_Token, folder_id=0, limit=9999999999, retry_count=0):
    MAX_RETRIES = 3
    fetched_urls = set()
    results = []
    video_count = 0
    pdf_count = 0
    image_count = 0
    content_tasks = []
    folder_tasks = []

    try:
        content_api = f'https://api.classplusapp.com/v2/course/preview/content/list/{Batch_Token}'
        params = {'folderId': folder_id, 'limit': limit}

        async with session.get(content_api, params=params, headers=headers) as res:
            res.raise_for_status()
            res_json = await res.json()
            contents = res_json['data']

            for content in contents:
                if content['contentType'] == 1:
                    folder_task = asyncio.create_task(get_cpwp_course_content(session, headers, Batch_Token, content['id'], retry_count=0))
                    folder_tasks.append((content['id'], folder_task))
                else:
                    name = content['name']
                    url_val = content.get('url') or content.get('thumbnailUrl')

                    if not url_val:
                        logging.warning(f"No URL found for content: {name}")
                        continue

                    if "media-cdn.classplusapp.com/tencent/" in url_val:
                        url_val = url_val.rsplit('/', 1)[0] + "/master.m3u8"
                    elif "media-cdn.classplusapp.com" in url_val and url_val.endswith('.jpg'):
                        identifier = url_val.split('/')[-3]
                        url_val = f'https://media-cdn.classplusapp.com/alisg-cdn-a.classplusapp.com/{identifier}/master.m3u8'
                    elif "tencdn.classplusapp.com" in url_val and url_val.endswith('.jpg'):
                        identifier = url_val.split('/')[-2]
                        url_val = f'https://media-cdn.classplusapp.com/tencent/{identifier}/master.m3u8'
                    elif "4b06bf8d61c41f8310af9b2624459378203740932b456b07fcf817b737fbae27" in url_val and url_val.endswith('.jpeg'):
                        url_val = f'https://media-cdn.classplusapp.com/alisg-cdn-a.classplusapp.com/b08bad9ff8d969639b2e43d5769342cc62b510c4345d2f7f153bec53be84fe35/{url_val.split("/")[-1].split(".")[0]}/master.m3u8'
                    elif "cpvideocdn.testbook.com" in url_val and url_val.endswith('.png'):
                        match = re.search(r'/streams/([a-f0-9]{24})/', url_val)
                        video_id = match.group(1) if match else url_val.split('/')[-2]
                        url_val = f'https://cpvod.testbook.com/{video_id}/playlist.m3u8'
                    elif "media-cdn.classplusapp.com/drm/" in url_val and url_val.endswith('.png'):
                        video_id = url_val.split('/')[-3]
                        url_val = f'https://media-cdn.classplusapp.com/drm/{video_id}/playlist.m3u8'
                    elif "https://media-cdn.classplusapp.com" in url_val and ("cc/" in url_val or "lc/" in url_val or "uc/" in url_val or "dy/" in url_val) and url_val.endswith('.png'):
                        url_val = url_val.replace('thumbnail.png', 'master.m3u8')
                    elif "https://tb-video.classplusapp.com" in url_val and url_val.endswith('.jpg'):
                        video_id = url_val.split('/')[-1].split('.')[0]
                        url_val = f'https://tb-video.classplusapp.com/{video_id}/master.m3u8'

                    if url_val.endswith(("master.m3u8", "playlist.m3u8")) and url_val not in fetched_urls:
                        fetched_urls.add(url_val)
                        headers2 = {'x-access-token': 'eyJjb3Vyc2VJZCI6IjQ1NjY4NyIsInR1dG9ySWQiOm51bGwsIm9yZ0lkIjo0ODA2MTksImNhdGVnb3J5SWQiOm51bGx9'}
                        task = asyncio.create_task(process_cpwp_url(url_val, name, session, headers2))
                        content_tasks.append((content['id'], task))
                    else:
                        name = content['name']
                        url_val = content.get('url')
                        if url_val:
                            fetched_urls.add(url_val)
                            results.append(f"{name}:{url_val}\n")
                            if url_val.endswith('.pdf'):
                                pdf_count += 1
                            else:
                                image_count += 1

    except Exception as e:
        logging.exception(f"An unexpected error occurred: {e}")
        if retry_count < MAX_RETRIES:
            logging.info(f"Retrying folder {folder_id} (Attempt {retry_count + 1}/{MAX_RETRIES})")
            await asyncio.sleep(2 ** retry_count)
            return await get_cpwp_course_content(session, headers, Batch_Token, folder_id, limit, retry_count + 1)
        else:
            logging.error(f"Failed to retrieve folder {folder_id} after {MAX_RETRIES} retries.")
            return [], 0, 0, 0

    content_results = await asyncio.gather(*(task for _, task in content_tasks), return_exceptions=True)
    folder_results = await asyncio.gather(*(task for _, task in folder_tasks), return_exceptions=True)

    for (folder_id, result) in zip(content_tasks, content_results):
        if isinstance(result, Exception):
            logging.error(f"Task failed with exception: {result}")
        elif result:
            results.append(result)
            video_count += 1

    for folder_id, folder_result in folder_tasks:
        try:
            nested_results, nested_video_count, nested_pdf_count, nested_image_count = await folder_result
            if nested_results:
                results.extend(nested_results)
            else:
                pass
            video_count += nested_video_count
            pdf_count += nested_pdf_count
            image_count += nested_image_count
        except Exception as e:
            logging.error(f"Error processing folder {folder_id}: {e}")

    return results, video_count, pdf_count, image_count


@bot.on_callback_query(filters.regex("^cpwp$"))
async def cpwp_callback(bot, callback_query):
    user_id = callback_query.from_user.id
    await callback_query.answer()

    if user_id not in auth_users:
        await bot.send_message(callback_query.message.chat.id, "**You Are Not Subscribed To This Bot**")
        return

    THREADPOOL.submit(_run_async_in_thread, process_cpwp(bot, callback_query.message, user_id))


async def process_cpwp(bot, m, user_id):

    headers = {
        'accept-encoding': 'gzip',
        'accept-language': 'EN',
        'api-version'    : '35',
        'app-version'    : '1.4.73.2',
        'build-number'   : '35',
        'connection'     : 'Keep-Alive',
        'content-type'   : 'application/json',
        'device-details' : 'Xiaomi_Redmi 7_SDK-32',
        'device-id'      : 'c28d3cb16bbdac01',
        'host'           : 'api.classplusapp.com',
        'region'         : 'IN',
        'user-agent'     : 'Mobile-Android',
        'webengage-luid' : '00000187-6fe4-5d41-a530-26186858be4c'
    }

    loop = asyncio.get_event_loop()
    CONNECTOR = aiohttp.TCPConnector(limit=1000, loop=loop)
    async with aiohttp.ClientSession(connector=CONNECTOR, loop=loop) as session:
        try:
            editable = await m.reply_text("**Enter ORG Code Of Your Classplus App**")

            try:
                input1 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                org_code = input1.text.lower()
                await input1.delete(True)
            except ListenerTimeout:
                await editable.edit("**Timeout! You took too long to respond**")
                return
            except Exception as e:
                logging.exception("Error during input1 listening:")
                try:
                    await editable.edit(f"**Error: {e}**")
                except:
                    logging.error(f"Failed to send error message to user: {e}")
                return

            hash_headers = {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Encoding': 'gzip, deflate, br, zstd',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://qsvfn.courses.store/?mainCategory=0&subCatList=[130504,62442]',
                'Sec-CH-UA': '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
                'Sec-CH-UA-Mobile': '?0',
                'Sec-CH-UA-Platform': '"Windows"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
            }

            async with session.get(f"https://{org_code}.courses.store", headers=hash_headers) as response:
                html_text = await response.text()
                hash_match = re.search(r'"hash":"(.*?)"', html_text)

                if hash_match:
                    token = hash_match.group(1)

                    async with session.get(f"https://api.classplusapp.com/v2/course/preview/similar/{token}?limit=20", headers=headers) as response:
                        if response.status == 200:
                            res_json = await response.json()
                            courses = res_json.get('data', {}).get('coursesData', [])

                            if courses:
                                text = ''
                                for cnt, course in enumerate(courses):
                                    name = course['name']
                                    price = course['finalPrice']
                                    text += f'{cnt + 1}. ```\n{name} Rs.{price}```\n'

                                await editable.edit(f"**Send index number of the Category Name\n\n{text}\nIf Your Batch Not Listed Then Enter Your Batch Name**")

                                try:
                                    input2 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                                    raw_text2 = input2.text
                                    await input2.delete(True)
                                except ListenerTimeout:
                                    await editable.edit("**Timeout! You took too long to respond**")
                                    return
                                except Exception as e:
                                    logging.exception("Error during input1 listening:")
                                    try:
                                        await editable.edit(f"**Error : {e}**")
                                    except:
                                        logging.error(f"Failed to send error message to user : {e}")
                                    return

                                if input2.text.isdigit() and len(input2.text) <= len(courses):
                                    selected_course_index = int(input2.text.strip())
                                    course = courses[selected_course_index - 1]
                                    selected_batch_id = course['id']
                                    selected_batch_name = course['name']
                                    price = course['finalPrice']
                                    clean_batch_name = selected_batch_name.replace("/", "-").replace("|", "-")
                                    clean_file_name = f"{user_id}_{clean_batch_name}"

                                else:
                                    search_url = f"https://api.classplusapp.com/v2/course/preview/similar/{token}?search={raw_text2}"
                                    async with session.get(search_url, headers=headers) as response:
                                        if response.status == 200:
                                            res_json = await response.json()
                                            courses = res_json.get("data", {}).get("coursesData", [])

                                            if courses:
                                                text = ''
                                                for cnt, course in enumerate(courses):
                                                    name = course['name']
                                                    price = course['finalPrice']
                                                    text += f'{cnt + 1}. ```\n{name} Rs.{price}```\n'
                                                await editable.edit(f"**Send index number of the Batch to download.\n\n{text}**")

                                                try:
                                                    input3 = await bot.listen(chat_id=m.chat.id, filters=filters.user(user_id), timeout=120)
                                                    raw_text3 = input3.text
                                                    await input3.delete(True)
                                                except ListenerTimeout:
                                                    await editable.edit("**Timeout! You took too long to respond**")
                                                    return
                                                except Exception as e:
                                                    logging.exception("Error during input1 listening:")
                                                    try:
                                                        await editable.edit(f"**Error : {e}**")
                                                    except:
                                                        logging.error(f"Failed to send error message to user : {e}")
                                                    return

                                                if input3.text.isdigit() and len(input3.text) <= len(courses):
                                                    selected_course_index = int(input3.text.strip())
                                                    course = courses[selected_course_index - 1]
                                                    selected_batch_id = course['id']
                                                    selected_batch_name = course['name']
                                                    price = course['finalPrice']
                                                    clean_batch_name = selected_batch_name.replace("/", "-").replace("|", "-")
                                                    clean_file_name = f"{user_id}_{clean_batch_name}"
                                                else:
                                                    raise Exception("Wrong Index Number")
                                            else:
                                                raise Exception("Didn't Find Any Course Matching The Search Term")
                                        else:
                                            raise Exception(f"{response.text}")

                                download_price = int(price * 0.10)
                                batch_headers = {
                                    'Accept': 'application/json, text/plain, */*',
                                    'region': 'IN',
                                    'accept-language': 'EN',
                                    'Api-Version': '22',
                                    'tutorWebsiteDomain': f'https://{org_code}.courses.store'
                                }

                                params = {
                                    'courseId': f'{selected_batch_id}',
                                }

                                async with session.get(f"https://api.classplusapp.com/v2/course/preview/org/info", params=params, headers=batch_headers) as response:
                                    if response.status == 200:
                                        res_json = await response.json()
                                        Batch_Token = res_json['data']['hash']
                                        App_Name = res_json['data']['name']

                                        await editable.edit(f"**Extracting course : {selected_batch_name} ...**")

                                        start_time = time.time()
                                        course_content, video_count, pdf_count, image_count = await get_cpwp_course_content(session, headers, Batch_Token)

                                        if course_content:
                                            file = f"{clean_file_name}.txt"

                                            with open(file, 'w') as f:
                                                f.write(''.join(course_content))

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

                                            caption = f"**App Name : ```\n{App_Name}({org_code})```\nBatch Name : ```\n{selected_batch_name}``````\n : {video_count} |  : {pdf_count} |   : {image_count}``````\nTime Taken : {formatted_time}```**"

                                            with open(file, 'rb') as f:
                                                doc = await m.reply_document(document=f, caption=caption, file_name=f"{clean_batch_name}.txt")

                                            os.remove(file)

                                        else:
                                            raise Exception("Didn't Find Any Content In The Course")
                                    else:
                                        raise Exception(f"{response.text}")
                            else:
                                raise Exception("Didn't Find Any Course")
                        else:
                            raise Exception(f"{response.text}")
                else:
                    raise Exception('No App Found In Org Code')

        except Exception as e:
            await editable.edit(f"**Error : {e}**")

        finally:
            await session.close()
            await CONNECTOR.close()


# ===============================================================
# START: Flask + Bot
# ===============================================================
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run()
