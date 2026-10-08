"""
slashcommands.py — ไฟล์กลางสำหรับ slash commands
ถูก import โดยทั้ง bot.py และ register.py
"""

import discord
from discord import app_commands
import yt_dlp
import asyncio
import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import json
import requests
import html
import unicodedata
import random
import uuid


def _html_unescape(text: str) -> str:
    return html.unescape(text) if text else text


logging.getLogger("discord.player").setLevel(logging.ERROR)
logging.getLogger("discord.voice_state").setLevel(logging.WARNING)

# ─────────────────────────────────────────────
#  Multi-guild file logging
#  ทุก guild มีไฟล์ log แยกตามชื่อ server และวันที่ (logs/guilds/<server>_<YYYY-MM-DD>.log)
#  console เห็นทุก guild ปนกัน จึงต้องเติม [ชื่อ guild] นำหน้าให้แยกออก
# ─────────────────────────────────────────────

_guild_loggers: dict[tuple[int, str], logging.Logger] = {}

def _safe_log_filename_part(name: str, guild_id: int) -> str:
    """แปลงชื่อ server ให้ใช้เป็นชื่อไฟล์ Windows ได้อย่างปลอดภัย."""
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name or "")
    safe_name = re.sub(r'\s+', " ", safe_name).strip(". ")
    return safe_name[:80] or f"guild-{guild_id}"

def get_guild_logger(guild_id: int, guild_name: str = "") -> logging.Logger:
    """คืน logger เฉพาะของ guild และวันที่ — เขียน logs/guilds/<server>_<YYYY-MM-DD>.log
    ไฟล์หมุนอัตโนมัติเมื่อขนาดเกิน 5MB เก็บสำรองย้อนหลัง 3 ไฟล์ กันไฟล์บวมไม่จำกัด
    """
    log_date = datetime.datetime.now().strftime("%Y-%m-%d")
    cache_key = (guild_id, log_date)
    if cache_key in _guild_loggers:
        return _guild_loggers[cache_key]

    os.makedirs("logs/guilds", exist_ok=True)
    logger = logging.getLogger(f"guild.{guild_id}.{log_date}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # กัน log ซ้ำขึ้น root logger/console

    safe_name = _safe_log_filename_part(guild_name, guild_id)
    handler = RotatingFileHandler(
        f"logs/guilds/{safe_name}_{log_date}.log",
        maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    _guild_loggers[cache_key] = logger
    return logger


def glog(guild_id: int, guild_name: str, message: str, level: str = "info", console: bool = True):
    """เขียน log ของ guild นี้ — บันทึกรายละเอียดเต็มเข้าไฟล์เสมอ และ print ขึ้น console ถ้า console=True
    (console มี [ชื่อ guild] นำหน้า กันสับสนตอนหลาย guild ทำงานพร้อมกัน)
    ใช้ level="info"/"warning"/"error" ให้ตรงความรุนแรง เพื่อกรองในไฟล์ log ได้ภายหลัง
    """
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger = get_guild_logger(guild_id, guild_name)
    getattr(logger, level, logger.info)(f"[{ts}] {message}")
    if console:
        print(f"[{guild_name or guild_id}] {message}")

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

DEFAULT_VOLUME = 0.10  # 10%

full_queues: dict[int, list] = {}
now_playing_idx: dict[int, int] = {}
# queue_seq_offset = เลขลำดับสะสมของเพลงแรก (index 0) ใน full_queues ตอนนี้
# ใช้แสดงผลเลขลำดับเพลงแบบนับต่อเนื่อง ไม่รีเซ็ตเมื่อตัดเพลงเก่าออก
# เช่น ถ้าตัดเพลง #1-13 ออก เพลงที่เหลือ index 0 จะมี seq_offset = 13
# แสดงผลเป็น #14 (= index 0 + 1 + offset 13)
queue_seq_offset: dict[int, int] = {}

# guild_total_added = จำนวนเพลงสะสมทั้งหมดที่เพิ่มเข้า queue (ไม่รีเซ็ตเมื่อตัดเพลงเก่า)
# ใช้แสดง "กำลังเล่น #X จาก Y เพลง" ให้ Y = จำนวนจริงเสมอ
guild_total_added: dict[int, int] = {}

active_views: dict[int, "PlayerView"] = {}
queue_done_msgs: dict[int, object] = {}
queue_add_msgs: dict[int, dict[int, object]] = {}
queue_view_msgs: dict[tuple[int, int], tuple[object, "QueueView"]] = {}
search_result_msgs: dict[int, list] = {}

# เก็บชื่อแบบสั้นสำหรับแสดงใน Queue โดยผูกกับ stream URL
# ไม่แก้ title ต้นฉบับ เพื่อให้หน้าผลการค้นหายังแสดงชื่อวิดีโอเต็มเหมือนเดิม
queue_display_titles: dict[str, str] = {}

# guild_volumes = ระดับเสียงที่ผู้ใช้ตั้งไว้ต่อ server (guild)
# จำไว้ตราบใดที่บอทยังอยู่ใน Voice Channel (ไม่ว่าเพลงจะเปลี่ยนกี่รอบ)
# จะถูกล้างกลับเป็นค่า default ทุกครั้งที่บอท disconnect ออกจาก VC (ดู clear_guild)
guild_volumes: dict[int, float] = {}

# guild_stopped  = หยุดจงใจ (⏹ stop / /stop) → play_next ต้องหยุด
# Playback generation per guild. Delayed callbacks from older sources are ignored.
guild_stopped: set[int] = set()
playback_generation: dict[int, int] = {}

# Stable ID for the currently active Player session in each guild.
# Playback callbacks carry this ID so callbacks from an old Player cannot mutate a new Player.
player_session_ids: dict[int, str] = {}

def _new_player_session(guild_id: int) -> str:
    session_id = f"{guild_id}:{uuid.uuid4().hex}"
    player_session_ids[guild_id] = session_id
    return session_id

def _get_player_session(guild_id: int) -> str | None:
    return player_session_ids.get(guild_id)

# Serialize user-driven Previous/Next transitions per guild.
navigation_locks: dict[int, asyncio.Lock] = {}

def get_navigation_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in navigation_locks:
        navigation_locks[guild_id] = asyncio.Lock()
    return navigation_locks[guild_id]

def _next_playback_generation(guild_id: int) -> int:
    token = playback_generation.get(guild_id, 0) + 1
    playback_generation[guild_id] = token
    return token

# Player UI state
loop_modes: dict[int, str] = {}
shuffle_enabled: set[int] = set()
QUEUE_PAGE_SIZE = 20

HISTORY_LIMIT = 10  # เก็บเพลงที่เล่นไปแล้วล่าสุดเพื่อ Previous
MAX_PLAYLIST_FETCH = 50  # ดึงเพลงจาก playlist สูงสุด 50 อัน
PLAYLIST_FETCH_CONCURRENCY = 4  # จำกัดจำนวน request พร้อมกันไปหา YouTube กันโดน rate-limit (HTTP 429)


def get_full_queue(guild_id: int) -> list:
    if guild_id not in full_queues:
        full_queues[guild_id] = []
    return full_queues[guild_id]

def get_now_idx(guild_id: int) -> int:
    return now_playing_idx.get(guild_id, 0)

def set_now_idx(guild_id: int, idx: int):
    now_playing_idx[guild_id] = idx

def get_guild_volume(guild_id: int) -> float:
    return guild_volumes.get(guild_id, DEFAULT_VOLUME)

def set_guild_volume(guild_id: int, vol: float):
    guild_volumes[guild_id] = vol

def get_seq_offset(guild_id: int) -> int:
    return queue_seq_offset.get(guild_id, 0)

def display_no(guild_id: int, idx: int) -> int:
    """แปลง list-index เป็นเลขลำดับสะสมที่จะแสดงให้ผู้ใช้เห็น (ไม่รีเซ็ตเมื่อตัดเพลงเก่า)"""
    return idx + 1 + get_seq_offset(guild_id)

def _trim_queue(guild_id: int):
    """ตัดเฉพาะ Playback History ที่เกิน HISTORY_LIMIT; Upcoming ไม่ถูกจำกัด.

    Queue แบ่งเป็น:
      history = q[:now_idx]
      current = q[now_idx]
      upcoming = q[now_idx + 1:]

    การ trim จะลบจากด้านหน้าเฉพาะเมื่อจำนวนเพลงก่อน Current เกิน 10 เพลง
    และเพิ่ม queue_seq_offset เพื่อรักษาเลข Queue เดิมของเพลงที่เหลือ.
    """
    q = get_full_queue(guild_id)
    if not q:
        set_now_idx(guild_id, 0)
        return

    now_idx = max(0, min(get_now_idx(guild_id), len(q) - 1))
    history_count = now_idx
    trim_count = max(0, history_count - HISTORY_LIMIT)

    if trim_count <= 0:
        return

    del q[:trim_count]
    set_now_idx(guild_id, now_idx - trim_count)
    queue_seq_offset[guild_id] = get_seq_offset(guild_id) + trim_count

    old_msgs = queue_add_msgs.get(guild_id, {})
    if old_msgs:
        shifted = {}
        for old_key, msg in old_msgs.items():
            new_key = old_key - trim_count
            if new_key >= 0:
                shifted[new_key] = msg
        queue_add_msgs[guild_id] = shifted

def get_total_added(guild_id: int) -> int:
    return guild_total_added.get(guild_id, 0)

def increment_total_added(guild_id: int, count: int = 1):
    guild_total_added[guild_id] = guild_total_added.get(guild_id, 0) + count

def add_to_queue(guild_id: int, track) -> int:
    q = get_full_queue(guild_id)
    q.append(track)
    increment_total_added(guild_id)
    _trim_queue(guild_id)
    return len(q) - 1

def clear_guild(guild_id: int):
    # Invalidate callbacks belonging to the old Player session.
    player_session_ids.pop(guild_id, None)
    for track in full_queues.get(guild_id, []):
        if track:
            queue_display_titles.pop(track[0], None)
    full_queues[guild_id] = []
    now_playing_idx[guild_id] = 0
    queue_seq_offset[guild_id] = 0
    guild_total_added[guild_id] = 0
    guild_volumes.pop(guild_id, None)
    loop_modes.pop(guild_id, None)
    shuffle_enabled.discard(guild_id)
    active_views.pop(guild_id, None)
    playback_generation.pop(guild_id, None)
    for key in [key for key in queue_view_msgs if key[0] == guild_id]:
        queue_view_msgs.pop(key, None)
    search_result_msgs.pop(guild_id, None)

def _queue_pos_str(guild_id: int, idx: int) -> str:
    return f"กำลังเล่น #{display_no(guild_id, idx)} จาก {get_total_added(guild_id)} เพลง"

def get_ydl_options(include_playlist: bool = False) -> dict:
    """Use yt-dlp's current YouTube client defaults and shared retry settings."""
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
        "remote_components": ["ejs:github"],
        "socket_timeout": 60,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 3,
        "extractor_retries": 3,
        "skip_unavailable_fragments": True,
    }
    opts["noplaylist"] = not include_playlist
    return opts

def _remember_queue_display_title(info: dict, stream_url: str):
    """เก็บ artist — track สำหรับ Queue เมื่อ extractor มี metadata ที่เชื่อถือได้.

    title ต้นฉบับยังถูกส่งกลับเหมือนเดิม จึงไม่กระทบหน้าผลการค้นหา.
    """
    artist = info.get("artist")
    track = info.get("track")
    if artist and track:
        queue_display_titles[stream_url] = f"{artist} — {track}"
    else:
        queue_display_titles.pop(stream_url, None)

# ─────────────────────────────────────────────
#  Spotify — ดึงข้อมูลจากหน้า embed สาธารณะ ไม่ใช้ Web API
#  (ไม่ต้องมี Client ID/Secret และไม่ต้องมี Spotify Premium)
# ─────────────────────────────────────────────

_SPOTIFY_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

def extract_spotify_id(url: str, kind: str) -> str:
    """ดึง ID จาก Spotify URL เช่น .../track/<id> หรือ .../playlist/<id>?si=..."""
    match = re.search(rf"spotify\.com/{kind}/([a-zA-Z0-9]+)", url)
    return match.group(1) if match else None

def extract_spotify_track_id(url: str) -> str:
    return extract_spotify_id(url, "track")

def extract_spotify_playlist_id(url: str) -> str:
    """รองรับทั้ง playlist และ album"""
    return extract_spotify_id(url, "playlist") or extract_spotify_id(url, "album")

def extract_spotify_artist_id(url: str) -> str:
    return extract_spotify_id(url, "artist")

def _fetch_spotify_entity(spotify_id: str, kind: str) -> dict:
    """ดึงข้อมูล track/playlist/album จากหน้า embed ของ Spotify (เพจสาธารณะ ไม่ต้อง login/credentials)"""
    url = f"https://open.spotify.com/embed/{kind}/{spotify_id}"
    try:
        resp = requests.get(url, headers=_SPOTIFY_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"Spotify embed fetch error: {str(e)}")
        return None

    match = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        return data["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None

def get_spotify_track_info(track_id: str) -> dict:
    """ดึงข้อมูล track เดี่ยวจากหน้า Spotify (ไม่ใช้ API)
    ใช้ <title> tag เป็นหลัก เพราะ Spotify render รูปแบบนี้เสมอ:
    "<ชื่อเพลง> - song and lyrics by <ศิลปิน> | Spotify"
    """
    url = f"https://open.spotify.com/track/{track_id}"
    try:
        resp = requests.get(url, headers=_SPOTIFY_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"Spotify track fetch error: {str(e)}")
        html = None

    if html:
        match = re.search(r"<title>(.*?)\s*-\s*song(?:s)? and lyrics by\s*(.*?)\s*\|\s*Spotify</title>",
                          html, re.IGNORECASE)
        if match:
            title = _html_unescape(match.group(1))
            artist = _html_unescape(match.group(2))
            if title and artist:
                return {"title": title, "artist": artist, "duration": 0}

    # Fallback: ลองดึงจาก __NEXT_DATA__ JSON
    entity = _fetch_spotify_entity(track_id, "track")
    if not entity:
        return None

    title = entity.get("name") or entity.get("title")
    if not title:
        return None

    artists = entity.get("artists") or []
    artist = ", ".join(a.get("name", "") for a in artists) if artists else entity.get("subtitle", "")

    return {
        "title": title,
        "artist": artist,
        "duration": (entity.get("duration") or 0) // 1000,
    }

def _fetch_spotify_track_from_search(search_query: str):
    """Helper: ค้นหา Spotify track จาก YouTube ด้วย search query
    ใช้ใน asyncio.to_thread เพื่อให้ thread-safe
    Returns: (url, title, duration, thumbnail)
    """
    opts = get_ydl_options(include_playlist=False)
    opts["socket_timeout"] = 30
    opts["retries"] = 5
    opts["fragment_retries"] = 5
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(search_query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        duration = info.get("duration", 0)
        minutes, seconds = divmod(int(duration), 60)
        url = info["url"]
        title = info.get("title", "Unknown")
        _remember_queue_display_title(info, url)
        duration = f"{minutes}:{seconds:02d}"
        thumbnail = info.get("thumbnail")
        
        
    
    return url, title, duration, thumbnail


def _scrape_spotify_playlist_html(playlist_id: str, kind: str, max_tracks: int = MAX_PLAYLIST_FETCH) -> list:
    """Fallback: ดึง track+artist จากหน้า playlist/album ปกติด้วย regex
    เผื่อโครงสร้าง __NEXT_DATA__ เปลี่ยนไป
    """
    url = f"https://open.spotify.com/{kind}/{playlist_id}"
    try:
        resp = requests.get(url, headers=_SPOTIFY_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"Spotify {kind} HTML fetch error: {str(e)}")
        return None

    track_pattern = re.compile(r'<a[^>]+href="/track/([a-zA-Z0-9]+)"[^>]*>([^<]+)</a>')
    artist_pattern = re.compile(r'<a[^>]+href="/artist/[a-zA-Z0-9]+"[^>]*>([^<]+)</a>')

    matches = list(track_pattern.finditer(html))
    if not matches:
        return None

    tracks = []
    for i, m in enumerate(matches[:max_tracks]):
        title = _html_unescape(m.group(2))
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        segment = html[start:end]
        artists = [_html_unescape(a) for a in artist_pattern.findall(segment)]
        tracks.append({"title": title, "artist": ", ".join(artists)})

    return tracks

def get_spotify_playlist_tracks(playlist_id: str, max_tracks: int = MAX_PLAYLIST_FETCH) -> list:
    """ดึง tracks จาก Spotify playlist/album ผ่านหน้าเว็บสาธารณะ (ไม่ใช้ API)
    Returns: list of dicts with keys: title, artist
    """
    for kind in ("playlist", "album"):
        entity = _fetch_spotify_entity(playlist_id, kind)
        if entity:
            track_list = entity.get("trackList") or []
            if track_list:
                tracks = []
                for item in track_list[:max_tracks]:
                    tracks.append({
                        "title": item.get("title", "Unknown"),
                        "artist": item.get("subtitle", ""),
                    })
                return tracks

    # Fallback: parse จากหน้าเว็บปกติด้วย regex
    for kind in ("playlist", "album"):
        tracks = _scrape_spotify_playlist_html(playlist_id, kind, max_tracks)
        if tracks:
            return tracks

    return None

def get_spotify_artist_top_tracks(artist_id: str, max_tracks: int = 10) -> list:
    """ดึงเพลงนิยมสูงสุด (Top Tracks) ของศิลปินจากหน้า embed ของ Spotify (ไม่ใช้ API)
    หน้า embed ของศิลปินใช้โครงสร้าง trackList เดียวกับ playlist/album
    Returns: list of dicts with keys: title, artist
    """
    entity = _fetch_spotify_entity(artist_id, "artist")
    if entity:
        track_list = entity.get("trackList") or []
        if track_list:
            tracks = []
            for item in track_list[:max_tracks]:
                tracks.append({
                    "title": item.get("title", "Unknown"),
                    "artist": item.get("subtitle", ""),
                })
            return tracks
    return None

def is_youtube_music_album_url(query: str) -> bool:
    """ตรวจสอบ YouTube Music album/playlist URL ที่ใช้ list=OLAK..."""
    if "youtube.com" not in query.lower() and "youtu.be" not in query.lower():
        return False
    list_match = re.search(r"[?&]list=([^&#]+)", query, re.IGNORECASE)
    return bool(list_match and list_match.group(1).upper().startswith("OLAK"))


def is_youtube_radio_url(query: str) -> bool:
    """ตรวจสอบว่าเป็น YouTube Mix/Radio URL (list=RD...)."""
    if "youtube.com" not in query.lower() and "youtu.be" not in query.lower():
        return False
    list_match = re.search(r"[?&]list=([^&#]+)", query, re.IGNORECASE)
    return bool(list_match and list_match.group(1).upper().startswith("RD"))

def _remove_youtube_list_param(query: str) -> str:
    """ลบเฉพาะพารามิเตอร์ list ออกจาก YouTube URL ก่อนเล่นเพลงเดี่ยว."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    parts = urlsplit(query)
    params = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "list"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))

def is_playlist_url(query: str) -> bool:
    """ตรวจสอบว่า URL มีหลายเพลง (playlist/album/artist) หรือไม่
    ข้อยกเว้น: YouTube Mix/Radio (list=RDxxxx) ไม่นับเป็น playlist —
    เป็น auto-generated playlist ที่ YouTube สร้างสดๆ ไม่มีจำนวนเพลงตายตัว/ขยายได้ไม่จำกัด
    จึงให้เล่นเป็นเพลงเดี่ยวเหมือนลิงก์ปกติแทน (ตัดพารามิเตอร์ list= ทิ้งตอนดึงเพลง)
    """
    query_lower = query.lower()
    # YouTube Playlist
    if "youtube.com" in query_lower or "youtu.be" in query_lower:
        list_match = re.search(r"[?&]list=([^&]+)", query)
        if list_match and list_match.group(1).upper().startswith("RD"):
            return False  # YouTube Mix/Radio — เล่นเป็นเพลงเดี่ยว ไม่ใช่ playlist
        return "list=" in query or "playlist" in query_lower
    # Spotify Playlist/Album/Artist (หน้าศิลปินมี Top Tracks หลายเพลง ใช้ flow เดียวกับ playlist)
    if "spotify.com" in query_lower:
        return "playlist" in query_lower or "album" in query_lower or "artist" in query_lower
    return False

def fetch_playlist_tracks(query: str, max_tracks: int = MAX_PLAYLIST_FETCH) -> list:
    """ดึง tracks จาก playlist (YouTube/Spotify) - สูงสุด 20 เพลงต่อ playlist
    Returns: list of dicts with keys: id, title, duration, url (ถ้าเป็น YouTube)
             หรือ title, artist (ถ้าเป็น Spotify)
    """
    # ตรวจสอบ Spotify Playlist/Album URL — ดึงรายชื่อเพลงจากหน้า embed (ไม่ใช้ API)
    if "spotify.com/playlist/" in query or "spotify.com/album/" in query:
        playlist_id = extract_spotify_playlist_id(query)
        if not playlist_id:
            raise ValueError("SPOTIFY_SCRAPE_ERROR")

        tracks = get_spotify_playlist_tracks(playlist_id, max_tracks)
        if not tracks:
            raise ValueError("SPOTIFY_SCRAPE_ERROR")

        return tracks

    # ตรวจสอบ Spotify Artist URL — ดึงเพลงนิยมสูงสุด (Top Tracks) จากหน้า embed
    if "spotify.com/artist/" in query:
        artist_id = extract_spotify_artist_id(query)
        if not artist_id:
            raise ValueError("SPOTIFY_SCRAPE_ERROR")

        tracks = get_spotify_artist_top_tracks(artist_id, max_tracks)
        if not tracks:
            raise ValueError("SPOTIFY_SCRAPE_ERROR")

        return tracks
    
    # Spotify URL รูปแบบอื่นที่ไม่รองรับ
    if "spotify.com" in query.lower():
        raise ValueError("SPOTIFY_DRM_ERROR")
    
    opts = get_ydl_options(include_playlist=True)
    opts["socket_timeout"] = 30
    opts["retries"] = 5
    opts["fragment_retries"] = 5
    opts["playlistend"] = max_tracks
    opts["extract_flat"] = "in_playlist"
    
    tracks = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            entries = info.get("entries", [])
            
            for entry in entries[:max_tracks]:
                if not entry or not entry.get("id"):
                    continue
                
                track_info = {
                    "id": entry.get("id"),
                    "title": entry.get("title", "Unknown"),
                    "duration": entry.get("duration"),
                }
                
                # ถ้าเป็น YouTube URL ให้เพิ่ม URL ด้วย
                if "youtube" in query.lower():
                    track_info["url"] = f"https://www.youtube.com/watch?v={entry.get('id')}"
                
                tracks.append(track_info)
    except Exception as e:
        print(f"Fetch playlist error: {str(e)}")
        raise
    
    return tracks

def fetch_track(query: str):
    """ดึงข้อมูล single track
    รองรับ: YouTube URLs, Spotify Track URLs, Search queries
    """
    # ตรวจสอบ Spotify Track URL
    if "spotify.com/track/" in query:
        track_id = extract_spotify_track_id(query)
        if track_id:
            track_info = get_spotify_track_info(track_id)
            if track_info:
                # ค้นหา track จาก YouTube ด้วย title + artist
                search_query = f"{track_info['title']} {track_info['artist']}"
                print(f"  {'🎵 Spotify→YT':<13}: {track_info['title']} — {track_info['artist']}")
                
                opts = get_ydl_options(include_playlist=False)
                opts["socket_timeout"] = 30
                opts["retries"] = 5
                opts["fragment_retries"] = 5
                
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = ydl.extract_info(search_query, download=False)
                        if "entries" in info:
                            info = info["entries"][0]
                        duration = info.get("duration", 0)
                        minutes, seconds = divmod(int(duration), 60)
                        
                        
                        url = info["url"]
                        title = info.get("title", "Unknown")
                        _remember_queue_display_title(info, url)
                        return url, title, f"{minutes}:{seconds:02d}", info.get("thumbnail")
                except Exception as e:
                    print(f"YouTube search error: {str(e)}")
                    raise ValueError("SPOTIFY_NO_YOUTUBE_MATCH")
            else:
                raise ValueError("SPOTIFY_SCRAPE_ERROR")
    
    # ตรวจสอบว่าเป็น playlist หรือไม่ (YouTube)
    if is_playlist_url(query):
        raise ValueError("PLAYLIST_DETECTED")

    # Spotify URL ประเภทอื่นที่ไม่รองรับ (episode/show/user/concert ฯลฯ)
    # ป้องกันไม่ให้หลุดไปเรียก yt-dlp ตรงๆ ซึ่งจะชน DRM error ที่ไม่ได้ดักไว้
    if "spotify.com" in query.lower():
        raise ValueError("SPOTIFY_UNSUPPORTED_LINK")
    
    opts = get_ydl_options(include_playlist=False)
    opts["socket_timeout"] = 30
    opts["retries"] = 5
    opts["fragment_retries"] = 5
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            duration = info.get("duration", 0)
            minutes, seconds = divmod(int(duration), 60)
            
            
            url = info["url"]
            title = info.get("title", "Unknown")
            _remember_queue_display_title(info, url)
            return url, title, f"{minutes}:{seconds:02d}", info.get("thumbnail")
    except ValueError as e:
        if str(e) in ("PLAYLIST_DETECTED", "SPOTIFY_SCRAPE_ERROR", "SPOTIFY_NO_YOUTUBE_MATCH", "SPOTIFY_UNSUPPORTED_LINK"):
            raise
        raise
    except Exception as e:
        raise

def search_tracks(query: str, limit: int = 5):
    opts = get_ydl_options(include_playlist=False)
    opts["extract_flat"] = "in_playlist"
    opts["default_search"] = "ytsearch5"
    opts["socket_timeout"] = 30
    opts["retries"] = 5
    opts["fragment_retries"] = 5
    results = []
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(query, download=False)
            entries = info.get("entries", [info]) if "entries" in info else [info]
            for entry in entries[:limit]:
                if entry.get("id") and entry.get("title"):  # Skip incomplete entries
                    duration = entry.get("duration")
                    if duration is not None:
                        m, s = divmod(int(duration), 60)
                        duration = f"{m}:{s:02d}"
                    results.append({
                        "id": entry.get("id"),
                        "title": entry.get("title", "Unknown"),
                        "duration": duration,
                    })
    except Exception as e:
        print(f"Search error: {str(e)}")
    
    return results

async def send_search_results(results, guild, channel, loop, loop_getter, requester,
                              done_msg_ref=None):
    lines = []
    for i, r in enumerate(results):
        line = f"`{i+1}.` {_trunc(r['title'], 55)}"
        duration = r.get("duration")
        if duration:
            line += f" | `{duration}`"
        lines.append(line)
    embed = discord.Embed(title="🔍 ผลการค้นหา", description="\n".join(lines), color=0x1a1a2e)
    embed.set_footer(text=f"กำลังรอ {requester.display_name} เลือกเพลง • หมดเวลาใน 30 วินาที")
    search_view = SearchResultView(results, guild, channel, loop, loop_getter,
                                   requester=requester, done_msg_ref=done_msg_ref)
    pub_msg = await channel.send(
        content=f"🎵 {requester.mention} กำลังเลือกเพลง", embed=embed, view=search_view)
    search_view.message = pub_msg
    search_result_msgs.setdefault(guild.id, []).append(pub_msg)
    return pub_msg


def make_now_playing_embed(title, duration, requester=None, thumbnail=None, queue_pos=None):
    """Render Player จาก Queue state เดียวกับ /queue.

    แสดง History 10 เพลงล่าสุด + Current + Upcoming 5 เพลง
    โดยใช้ logical queue number จาก display_no() ทุกบรรทัด.
    """
    requester_str = requester.mention if requester else "ไม่ทราบชื่อ"

    guild_id = None
    if requester is not None and hasattr(requester, "guild"):
        guild_id = requester.guild.id

    current_idx = get_now_idx(guild_id) if guild_id is not None else 0
    q = get_full_queue(guild_id) if guild_id is not None else []
    if q:
        current_idx = max(0, min(current_idx, len(q) - 1))

    history_start = max(0, current_idx - HISTORY_LIMIT)
    history = q[history_start:current_idx] if q else []
    upcoming_start = current_idx + 1
    upcoming = q[upcoming_start:upcoming_start + 5] if q else []

    artist = "YouTube"
    if q and 0 <= current_idx < len(q):
        current_url = q[current_idx][0]
        display_meta = queue_display_titles.get(current_url)
        if display_meta and " — " in display_meta:
            artist = display_meta.split(" — ", 1)[0]

    display_title = _clean_player_title(title)

    embed = discord.Embed(color=0x5865F2)
    embed.set_author(name="🎵  NOW PLAYING")
    embed.description = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"**{display_title}**\n"
        f"{_truncate_display_width(artist, 44)} • YouTube\n"
        f"👤 {requester_str} • {duration}"
    )

    if guild_id is not None:
        active_modes = []
        if guild_id in shuffle_enabled:
            active_modes.append("🔀")
        mode = loop_modes.get(guild_id, "off")
        if mode == "track":
            active_modes.append("🔂")
        elif mode == "queue":
            active_modes.append("🔁")

        volume_pct = round(get_guild_volume(guild_id) * 100)
        mode_prefix = " ".join(active_modes)
        status_line = f"{mode_prefix}  🔊 {volume_pct}%" if mode_prefix else f"🔊 {volume_pct}%"
        embed.description += f"\n{status_line}"
        embed.description += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

        music_icons = ("🎧", "🎵", "🎶", "🎼")
        queue_lines = []

        if history:
            queue_lines.append("📚 **HISTORY • LAST 10**")
            for offset, track in enumerate(history, start=history_start):
                url, track_title, track_duration, _requester, *_rest = track
                queue_title = _truncate_display_width(queue_display_titles.get(url, track_title), 31)
                queue_lines.append(
                    f"**{display_no(guild_id, offset):02d}** {music_icons[(offset - history_start) % len(music_icons)]} "
                    f"{_pad_queue_title(queue_title, 31)} `{track_duration}`"
                )

        if q and 0 <= current_idx < len(q):
            url, current_title, current_duration, _requester, *_rest = q[current_idx]
            current_queue_title = _truncate_display_width(queue_display_titles.get(url, current_title), 31)
            queue_lines.append("▶️ **CURRENT**")
            queue_lines.append(
                f"**{display_no(guild_id, current_idx):02d}** ▶️ "
                f"{_pad_queue_title(current_queue_title, 31)} `{current_duration}`"
            )

        if upcoming:
            queue_lines.append("📋 **NEXT • 5**")
            for offset, track in enumerate(upcoming, start=upcoming_start):
                url, track_title, track_duration, _requester, *_rest = track
                queue_title = _truncate_display_width(queue_display_titles.get(url, track_title), 31)
                queue_lines.append(
                    f"**{display_no(guild_id, offset):02d}** {music_icons[(offset - upcoming_start) % len(music_icons)]} "
                    f"{_pad_queue_title(queue_title, 31)} `{track_duration}`"
                )

        if queue_lines:
            embed.description += "\n" + "\n".join(queue_lines)

    embed.set_footer(text=queue_pos or (
        f"กำลังเล่น #{display_no(guild_id, current_idx)} จาก {get_total_added(guild_id)} เพลง"
        if guild_id is not None and q else "ไม่มีเพลง"
    ))

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    return embed
def _pad_queue_title(text: str, width: int) -> str:
    """Pad queue text by estimated display width for a stable Discord layout."""
    current_width = sum(_char_display_width(char) for char in text)
    if current_width >= width:
        return text
    return text + " " * (width - current_width)
def make_done_embed():
    return discord.Embed(
        description="⏹ หยุดเพลงและออกจาก Voice Channel แล้ว",
        color=discord.Color.green()
    )

_QUEUE_TITLE_NORMAL  = 60
_QUEUE_TITLE_PLAYING = 48

def _char_display_width(char: str) -> int:
    """ประมาณความกว้างของอักขระใน Discord: CJK กว้างกว่า Latin ราว 2 เท่า."""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1

def _clean_player_title(text: str) -> str:
    """Remove common YouTube video metadata while preserving the actual song title."""
    if not text:
        return text

    cleaned = html.unescape(text).strip()

    # Remove common leading metadata tags such as [MV], [Official Video], [Lyrics].
    leading_patterns = (
        r"^\s*[\[(]\s*(?:mv|music\s+video|official(?:\s+music)?\s+video|official\s+audio|"
        r"official\s+lyric(?:s)?\s+video|lyrics?|lyric\s+video|audio|visualizer|"
        r"performance|live|4k|hd|uhd)\s*[\])]\s*",
    )
    for pattern in leading_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    # Remove parenthesized/bracketed metadata anywhere in the title.
    metadata_group = (
        r"official(?:\s+music)?\s+video|official\s+audio|official\s+lyric(?:s)?\s+video|"
        r"music\s+video|lyric(?:s)?(?:\s+video)?|audio|visualizer|performance|live|"
        r"mv|amv|fmv|4k|hd|uhd|remaster(?:ed)?"
    )
    cleaned = re.sub(
        rf"\s*[\[(]\s*(?:{metadata_group})\s*[\])]\s*",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )

    # If an AMV/FMV label is followed by extra video context, drop that suffix.
    cleaned = re.sub(
        r"\s+(?:AMV|FMV)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Remove trailing separators followed only by common video metadata.
    cleaned = re.sub(
        rf"\s*(?:\||•|[-–—])\s*(?:{metadata_group})(?:\s+.*)?$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Collapse whitespace left behind by removed tags.
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—|•")
    return cleaned


def _truncate_display_width(text: str, max_width: int) -> str:
    """ตัดข้อความตามความกว้างที่มองเห็น แทนการนับจำนวนตัวอักษรล้วน ๆ."""
    if sum(_char_display_width(char) for char in text) <= max_width:
        return text

    chars = []
    width = 0
    # สงวนความกว้าง 1 ช่องให้เครื่องหมาย …
    for char in text:
        char_width = _char_display_width(char)
        if width + char_width > max_width - 1:
            break
        chars.append(char)
        width += char_width
    return "".join(chars) + "…"

def make_queue_embed(guild_id: int, current_idx: int = None, page: int = 0):
    """Render Queue ทั้งหมด: History + Current + Upcoming โดยใช้เลข Queue เดียวกับ Player."""
    q = get_full_queue(guild_id)
    idx = current_idx if current_idx is not None else get_now_idx(guild_id)

    if not q:
        embed = discord.Embed(
            title="📋  QUEUE",
            description="ไม่มีเพลงใน Queue",
            color=0x5865F2,
        )
        embed.set_footer(text="Queue ว่าง")
        return embed

    idx = max(0, min(idx, len(q) - 1))
    total_pages = max(1, (len(q) + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * QUEUE_PAGE_SIZE
    page_items = q[start:start + QUEUE_PAGE_SIZE]

    music_icons = ("🎧", "🎵", "🎶", "🎼")
    lines = []
    for actual_idx, track in enumerate(page_items, start=start):
        url, title, duration, requester, *_rest = track
        display_title = _truncate_display_width(
            queue_display_titles.get(url, title), 31
        )
        line_no = display_no(guild_id, actual_idx)
        icon = "▶️" if actual_idx == idx else music_icons[(actual_idx - start) % len(music_icons)]
        lines.append(
            f"**{line_no:02d}**  {icon} {_pad_queue_title(display_title, 31)}  " + "`" + f"{duration}" + "`"
        )

    embed = discord.Embed(
        title="📋  QUEUE",
        description="\n".join(lines),
        color=0x5865F2,
    )
    embed.set_footer(
        text=f"Page {page + 1} / {total_pages}  •  {len(q)} songs  •  กำลังเล่น #{display_no(guild_id, idx)}"
    )
    return embed
MAX_TITLE_LOG = 40

def _trunc(text: str, n: int = MAX_TITLE_LOG) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"

def log(action: str, interaction: discord.Interaction, extra: str = ""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"",
        f"[{ts}] {action}",
        f"  {'Guild':<9}: {_trunc(interaction.guild.name, 30)} ({interaction.guild.id})",
        f"  {'Channel':<9}: #{_trunc(interaction.channel.name, 30)}",
        f"  {'User':<9}: {_trunc(interaction.user.display_name, 30)} ({interaction.user.id})",
    ]
    if extra:
        key, _, val = extra.partition(": ")
        lines.append(f"  {key:<9}: {val}")
    full_block = "\n".join(lines)
    print(full_block)
    # เก็บรายละเอียดเต็มเข้าไฟล์ log ของ guild นี้ด้วย (ตรวจย้อนหลังได้แม้บอทรีสตาร์ท)
    get_guild_logger(interaction.guild.id, interaction.guild.name).info(full_block)

VOICE_CONNECT_RETRIES = 3       # ลองเชื่อมต่อ VC สูงสุดกี่ครั้งถ้าโดน 1006/หลุดกลาง handshake
VOICE_CONNECT_RETRY_DELAY = 1.5  # วินาที รอก่อนลองใหม่แต่ละรอบ (กันยิงรัวตอนเน็ตแกว่ง)

async def _connect_with_retry(voice_channel: discord.VoiceChannel):
    """เชื่อมต่อ Voice Channel พร้อม retry — กัน ConnectionClosed (1006) ตอนเน็ตบอทไม่เสถียร
    ถ้า handshake หลุดกลางทาง discord.py จะโยน ConnectionClosed/asyncio.TimeoutError ออกมาทันที
    โดยไม่ retry เอง จึงต้องดักแล้วลองใหม่ที่นี่แทน ไม่งั้น /play จะพังทันทีตั้งแต่ครั้งแรกที่เน็ตสะดุด
    คืนค่า VoiceClient ถ้าสำเร็จ, โยน exception เดิมกลับไปถ้าลองครบทุกครั้งแล้วยังไม่ผ่าน
    """
    last_error = None
    for attempt in range(1, VOICE_CONNECT_RETRIES + 1):
        try:
            vc = await voice_channel.connect()
            return vc
        except (discord.errors.ConnectionClosed, asyncio.TimeoutError) as e:
            last_error = e
            print(f"⚠ Voice connect ล้มเหลว (ครั้งที่ {attempt}/{VOICE_CONNECT_RETRIES}): {e}")
            # เช็คว่ามี voice_client ค้างอยู่จาก attempt ก่อนหน้าไหม (บาง state discord.py
            # จะสร้าง VoiceClient ไว้ก่อนแล้วค่อยพังตอน handshake) ต้อง disconnect ทิ้งก่อนลองใหม่
            stale_vc = voice_channel.guild.voice_client
            if stale_vc:
                try: await stale_vc.disconnect(force=True)
                except Exception: pass
            if attempt < VOICE_CONNECT_RETRIES:
                await asyncio.sleep(VOICE_CONNECT_RETRY_DELAY)
    raise last_error

async def check_in_voice(interaction: discord.Interaction) -> bool:
    vc = interaction.guild.voice_client
    if not vc:
        await safe_respond(interaction, embed=discord.Embed(
            description="❌ บอทไม่ได้อยู่ใน Voice Channel", color=discord.Color.red()), ephemeral=True)
        return False
    if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
        await safe_respond(interaction, embed=discord.Embed(
            description=f"❌ คุณต้องอยู่ใน **{vc.channel.name}** ถึงจะใช้งานได้",
            color=discord.Color.red()), ephemeral=True)
        return False
    return True

async def safe_respond(interaction: discord.Interaction, content=None, embed=None,
                       view=None, ephemeral=False):
    kwargs = {"ephemeral": ephemeral}
    if content: kwargs["content"] = content
    if embed:   kwargs["embed"]   = embed
    if view:    kwargs["view"]    = view
    try:
        if interaction.response.is_done():
            return await interaction.followup.send(**kwargs, wait=True)
        else:
            await interaction.response.send_message(**kwargs)
    except Exception:
        try:
            await interaction.followup.send(**kwargs)
        except Exception:
            pass

async def _refresh_queue_msg(guild_id: int):
    """Refresh every open ephemeral Queue for this guild and keep each user page."""
    targets = [(key, value) for key, value in queue_view_msgs.items() if key[0] == guild_id]
    if not targets:
        return
    for key, (wmsg, view) in targets:
        try:
            view._sync_buttons()
            embed = make_queue_embed(guild_id, current_idx=get_now_idx(guild_id), page=view.page)
            await wmsg.edit(embed=embed, view=view)
        except Exception:
            queue_view_msgs.pop(key, None)

async def _delete_queue_view_msg(guild_id: int):
    """Delete all tracked ephemeral Queue views for this guild."""
    targets = [(key, value) for key, value in queue_view_msgs.items() if key[0] == guild_id]
    for key, (wmsg, _view) in targets:
        queue_view_msgs.pop(key, None)
        try:
            await wmsg.delete()
        except Exception:
            pass
async def _is_current_player(view: "PlayerView") -> bool:
    """Return True only when this button belongs to the active Player session."""
    return (
        _get_player_session(view.guild.id) is not None
        and view.player_id == _get_player_session(view.guild.id)
        and active_views.get(view.guild.id) is view
    )

async def _refresh_player(guild_id: int):
    """Refresh the existing Player message with the latest guild state."""
    view = active_views.get(guild_id)
    if not view or not view.current_track:
        return False

    try:
        vc = view.guild.voice_client
        view.volume_level = get_guild_volume(guild_id)
        view._sync_state_buttons()

        # Keep Play/Pause icon synchronized with the actual voice state.
        for item in view.children:
            if item.custom_id == "player_pause_resume":
                if vc and vc.is_paused():
                    item.emoji = "▶️"
                else:
                    item.emoji = "⏸️"

        _url, title, duration, requester, thumbnail, *_rest = view.current_track
        embed = make_now_playing_embed(
            title, duration, requester, thumbnail,
            _queue_pos_str(guild_id, get_now_idx(guild_id)),
        )

        if view.now_playing_msg:
            try:
                await view.now_playing_msg.edit(embed=embed, view=view)
                return True
            except Exception:
                view.now_playing_msg = None

        view.now_playing_msg = await view.channel.send(embed=embed, view=view)
        return True
    except Exception:
        return False
async def _delete_search_result_msgs(guild_id: int):
    msgs = search_result_msgs.pop(guild_id, [])
    if not msgs:
        return
    async def _safe_delete(m):
        try: await m.delete()
        except Exception: pass
    await asyncio.gather(*(_safe_delete(m) for m in msgs))

async def _delete_queue_add_msgs(guild_id: int):
    msgs = list(queue_add_msgs.pop(guild_id, {}).values())
    if not msgs:
        return
    async def _safe_delete(m):
        try: await m.delete()
        except Exception: pass
    await asyncio.gather(*(_safe_delete(m) for m in msgs))


async def cleanup_old_messages(bot=None):
    """ลบ reference เก่าจาก memory ตอน startup (ไม่ scan channel history)
    การลบข้อความจริงใน Discord จะเกิดตอนมีการใช้ /play หรือ /stop ในช่องนั้น
    """
    async def _safe_delete(m):
        try:
            if m and hasattr(m, 'delete'):
                await m.delete()
        except Exception:
            pass

    all_msgs = []
    for guild_msgs in queue_add_msgs.values():
        all_msgs.extend(guild_msgs.values())
    queue_add_msgs.clear()
    all_msgs.extend(queue_done_msgs.values())
    queue_done_msgs.clear()
    all_msgs.extend(value[0] for value in queue_view_msgs.values())
    queue_view_msgs.clear()

    if all_msgs:
        print(f"🧹 ลบ reference เก่า {len(all_msgs)} รายการจาก memory")
        await asyncio.gather(*(_safe_delete(m) for m in all_msgs), return_exceptions=True)


async def _cleanup_channel(channel: discord.TextChannel):
    """ลบข้อความเก่าของบอทใน channel นี้ — เรียกตอนมีการใช้ /play หรือ /stop
    ลบเฉพาะ: "เพิ่มใน Queue", "เล่นเพลงครบ Queue", "หยุดเพลง"
    ไม่ลบ: now playing embed (▶ Now Playing) และ queue list
    """
    _DELETE_KEYWORDS = ["เพิ่มใน queue", "เล่นเพลงครบ queue", "หยุดเพลงและออกจาก"]
    _KEEP_KEYWORDS   = ["now playing", "▶", "queue เพลง"]
    try:
        async for msg in channel.history(limit=50):
            if msg.author != channel.guild.me:
                continue
            if not msg.embeds:
                continue
            embed = msg.embeds[0]
            desc   = str(embed.description or "").lower()
            author = str(embed.author.name or "").lower() if embed.author else ""
            text   = desc + " " + author
            # ข้ามถ้าเป็น now playing หรือ queue list
            if any(k in text for k in _KEEP_KEYWORDS):
                continue
            if any(k in text for k in _DELETE_KEYWORDS):
                try: await msg.delete()
                except Exception: pass
    except Exception:
        pass


# ─────────────────────────────────────────────
#  Queue Done View
# ─────────────────────────────────────────────

class QueueDoneView(discord.ui.View):
    def __init__(self, guild, channel, loop_getter, done_msg_ref: list = None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop_getter = loop_getter
        self.done_msg_ref = done_msg_ref

    @discord.ui.button(emoji="🔍", label="ค้นหาเพลง", style=discord.ButtonStyle.primary,
                       custom_id="queue_done_search")
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.voice:
            return await safe_respond(
                interaction,
                embed=discord.Embed(
                    description="❌ กรุณาเข้า Voice Channel ก่อน",
                    color=discord.Color.red()),
                ephemeral=True)

        # ส่ง modal ทันที ไม่มี await ใดๆ คั่นกลาง — กัน interaction token หมดอายุ (3 วิ)
        # เรื่องเชื่อมต่อ/ย้ายห้องเสียง ให้ SearchModal.on_submit() จัดการเองตอน submit (เหมือน /play)
        loop = self.loop_getter()
        try:
            modal = SearchModal(self.guild, self.channel, loop, self.loop_getter,
                                done_msg_ref=self.done_msg_ref)
            await interaction.response.send_modal(modal)
        except Exception as e:
            log("🔍 SEARCH BTN ERROR", interaction, str(e))
            try:
                await safe_respond(
                    interaction,
                    embed=discord.Embed(description="❌ เกิดข้อผิดพลาดในการเปิดหน้าต่างค้นหา", color=discord.Color.red()),
                    ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(emoji="⏹", label="หยุดและออก", style=discord.ButtonStyle.danger,
                       custom_id="queue_done_stop")
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        log("⏹ STOP", interaction, "Queue done stop")

        # ตอบ interaction ทันที กัน Discord ฟ้องว่าปุ่มไม่ตอบสนอง
        try: await interaction.response.defer()
        except Exception: pass

        vc = self.guild.voice_client
        if vc:
            await asyncio.gather(
                _delete_search_result_msgs(self.guild.id),
                _delete_queue_view_msg(self.guild.id),
                _delete_queue_add_msgs(self.guild.id),
            )
            async with get_navigation_lock(self.guild.id):
                guild_stopped.add(self.guild.id)
                clear_guild(self.guild.id)
                vc.stop()
                await vc.disconnect()
        done_msg = (self.done_msg_ref[0] if self.done_msg_ref else None) \
                   or queue_done_msgs.pop(self.guild.id, None)
        queue_done_msgs.pop(self.guild.id, None)
        if done_msg:
            try: await done_msg.edit(embed=make_done_embed(), view=None)
            except Exception: pass
        self.stop()


# ─────────────────────────────────────────────
#  Volume Modal
# ─────────────────────────────────────────────

class VolumeModal(discord.ui.Modal, title="🔊 ปรับระดับเสียง"):
    def __init__(self, vc, player_view):
        super().__init__(custom_id="volume_modal")
        self.vc = vc
        self.player_view = player_view
        # แสดงค่าปัจจุบันล่าสุดของ guild นี้ (ไม่ใช่ placeholder ตายตัว)
        # ใช้ default= ให้ผู้ใช้เห็นค่าปัจจุบันเติมอยู่ในช่องกรอกเลย ไม่ใช่แค่ตัวอย่างจางๆ
        current_pct = round(get_guild_volume(player_view.guild.id) * 100)
        self.vol_input = discord.ui.TextInput(
            label="ระดับเสียง (0-100)",
            placeholder=f"ค่าปัจจุบัน: {current_pct}",
            default=str(current_pct),
            min_length=1, max_length=3,
            custom_id="volume_modal_input")
        self.add_item(self.vol_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        if not await _is_current_player(self.player_view):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        try:
            vol = int(str(self.vol_input))
            if not 0 <= vol <= 100: raise ValueError
        except ValueError:
            return await interaction.response.send_message("❌ กรอกตัวเลข 0-100", ephemeral=True)
        vol_level = vol / 100
        self.player_view.volume_level = vol_level
        set_guild_volume(self.player_view.guild.id, vol_level)
        if self.vc.source:
            self.vc.source.volume = vol_level
        await _refresh_player(self.player_view.guild.id)
        log("🔊 VOLUME", interaction, f"Volume: {vol}%")


# ─────────────────────────────────────────────
#  YouTube Radio / Mix Choice View
# ─────────────────────────────────────────────

class PlaylistCountView(discord.ui.View):
    """ให้ผู้ใช้เลือกจำนวนเพลงจาก playlist ที่ตรวจพบจริง (สูงสุด MAX_PLAYLIST_FETCH)."""

    def __init__(self, playlist_tracks, guild, channel, loop_getter, requester,
                 vc, parent_view=None, source_label="เพลย์ลิสต์"):
        super().__init__(timeout=30)
        self.playlist_tracks = playlist_tracks
        self.guild = guild
        self.channel = channel
        self.loop_getter = loop_getter
        self.requester = requester
        self.vc = vc
        self.parent_view = parent_view
        self.source_label = source_label
        self.message = None
        self._busy = False
        self._build_buttons()

    def _build_buttons(self):
        count = len(self.playlist_tracks)
        choices = [n for n in (5, 10, 20, 30, 50) if n <= count]
        if count < 5:
            choices = [count]
        elif count not in choices and count < 50:
            choices.append(count)

        for index, amount in enumerate(choices):
            button = discord.ui.Button(
                label=str(amount),
                style=discord.ButtonStyle.primary if index == 0 else discord.ButtonStyle.secondary,
                custom_id=f"playlist_count_{amount}",
            )
            button.callback = self._make_callback(amount)
            self.add_item(button)

    def _make_callback(self, amount):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.requester.id:
                return await interaction.response.send_message(
                    "❌ เฉพาะผู้ที่ส่งลิงก์เท่านั้นที่เลือกได้", ephemeral=True)
            if self._busy:
                return await interaction.response.defer()

            self._busy = True
            try:
                await interaction.response.defer()
                selected = self.playlist_tracks[:amount]
                await _add_playlist_to_queue(
                    self.vc,
                    self.guild,
                    self.channel,
                    self.loop_getter,
                    selected,
                    interaction.user,
                )
                await self._close()
                if self.parent_view:
                    await self.parent_view._close()
            except Exception as e:
                log("📋 PLAYLIST COUNT ERROR", interaction, str(e))
                try:
                    await interaction.followup.send(
                        f"❌ ไม่สามารถโหลด {amount} เพลงจาก{self.source_label}ได้",
                        ephemeral=True,
                    )
                except Exception:
                    pass
            finally:
                self._busy = False

        return callback

    async def _close(self):
        self.stop()
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                try:
                    await self.message.edit(view=None)
                except Exception:
                    pass
            self.message = None

    async def on_timeout(self):
        await self._close()


class RadioChoiceView(discord.ui.View):
    """ตัวเลือกแรกของ YouTube Radio/Mix — แสดงทันทีโดยยังไม่เชื่อมต่อ VC/โหลด playlist."""

    def __init__(self, query, guild, channel, loop_getter, requester, loop):
        super().__init__(timeout=30)
        self.query = query
        self.guild = guild
        self.channel = channel
        self.loop_getter = loop_getter
        self.requester = requester
        self.loop = loop
        self.message = None
        self._busy = False

        # ใช้ dynamic buttons เพื่อให้แน่ใจว่า Discord ส่ง components ไปพร้อม View
        single = discord.ui.Button(
            emoji="▶️",
            label="เล่นเพลงนี้เท่านั้น",
            style=discord.ButtonStyle.primary,
            custom_id="youtube_radio_single",
        )
        single.callback = self.single_btn
        self.add_item(single)

        playlist = discord.ui.Button(
            emoji="📋",
            label="โหลดเพลงจาก Radio",
            style=discord.ButtonStyle.secondary,
            custom_id="youtube_radio_playlist",
        )
        playlist.callback = self.radio_btn
        self.add_item(playlist)

    async def _check_requester(self, interaction):
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "❌ เฉพาะผู้ที่ส่งลิงก์เท่านั้นที่เลือกได้", ephemeral=True)
            return False
        return True

    async def _connect_voice(self, interaction):
        vc = self.guild.voice_client
        if not vc:
            if not interaction.user.voice:
                await interaction.followup.send("❌ กรุณาเข้า Voice Channel ก่อน", ephemeral=True)
                return None
            try:
                vc = await _connect_with_retry(interaction.user.voice.channel)
            except Exception:
                await interaction.followup.send("❌ เชื่อมต่อ Voice Channel ไม่สำเร็จ", ephemeral=True)
                return None
        elif interaction.user.voice and interaction.user.voice.channel != vc.channel:
            try:
                await vc.move_to(interaction.user.voice.channel)
            except Exception:
                await interaction.followup.send("❌ ไม่สามารถย้ายบอทไป Voice Channel ของคุณได้", ephemeral=True)
                return None
        return vc

    async def _close(self):
        self.stop()
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                try:
                    await self.message.edit(view=None)
                except Exception:
                    pass
            self.message = None

    async def single_btn(self, interaction: discord.Interaction):
        if not await self._check_requester(interaction):
            return
        if self._busy:
            return await interaction.response.defer()

        self._busy = True
        try:
            await interaction.response.defer()
            vc = await self._connect_voice(interaction)
            if not vc:
                return

            single_url = _remove_youtube_list_param(self.query)
            url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, single_url)
            track = (url, title, duration, interaction.user, thumbnail)
            await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track)
            await self._close()
        except Exception as e:
            log("📻 RADIO SINGLE ERROR", interaction, str(e))
            try:
                await interaction.followup.send("❌ ไม่สามารถเล่นเพลงนี้ได้", ephemeral=True)
            except Exception:
                pass
        finally:
            self._busy = False

    async def radio_btn(self, interaction: discord.Interaction):
        if not await self._check_requester(interaction):
            return
        if self._busy:
            return await interaction.response.defer()

        self._busy = True
        try:
            await interaction.response.defer()

            # เชื่อมต่อ VC หลังผู้ใช้เลือก "โหลด Radio" เท่านั้น
            vc = await self._connect_voice(interaction)
            if not vc:
                return

            # ดึง metadata แบบ flat เพื่อสร้างตัวเลือกจำนวนเพลง
            playlist_tracks = await asyncio.to_thread(fetch_playlist_tracks, self.query)
            if not playlist_tracks:
                await interaction.followup.send(
                    "❌ ไม่พบเพลงจาก Radio/Mix นี้", ephemeral=True)
                return

            await self._close()
            count_view = PlaylistCountView(
                playlist_tracks,
                self.guild,
                self.channel,
                self.loop_getter,
                interaction.user,
                vc,
                source_label=" Radio/Mix",
            )
            prompt = await interaction.followup.send(
                embed=discord.Embed(
                    title="📋 เลือกจำนวนเพลง",
                    description=f"พบ **{len(playlist_tracks)} เพลง**\nต้องการเพิ่มกี่เพลง?",
                    color=0x1a1a2e,
                ),
                view=count_view,
                ephemeral=True,
                wait=True,
            )
            count_view.message = prompt
        except Exception as e:
            log("📻 RADIO PLAYLIST ERROR", interaction, str(e))
            try:
                await interaction.followup.send(
                    "❌ ไม่สามารถโหลดเพลงจาก Radio/Mix นี้ได้", ephemeral=True)
            except Exception:
                pass
        finally:
            self._busy = False

    async def on_timeout(self):
        await self._close()


# ─────────────────────────────────────────────
#  Search Modal
# ─────────────────────────────────────────────

class SearchModal(discord.ui.Modal, title="🔍 ค้นหาเพลง"):
    query = discord.ui.TextInput(
        label="ค้นหาเพลง หรือวาง URL YouTube",
        placeholder="ระบุชื่อเพลง หรือวาง URL YouTube ที่นี่",
        min_length=1, 
        max_length=100,
        custom_id="search_modal_input")

    def __init__(self, guild, channel, loop, loop_getter, done_msg_ref: list = None):
        super().__init__(custom_id="search_modal")
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter
        self.done_msg_ref = done_msg_ref

    async def _delete_done_msg(self):
        if self.done_msg_ref and self.done_msg_ref[0]:
            try: await self.done_msg_ref[0].delete()
            except Exception: pass
            self.done_msg_ref[0] = None
        old = queue_done_msgs.pop(self.guild.id, None)
        if old:
            try: await old.delete()
            except Exception: pass

    async def on_submit(self, interaction: discord.Interaction):
        query_str = str(self.query).strip()
        log("🔍 SEARCH", interaction, f"query: {_trunc(query_str, 50)}")
        await interaction.response.send_message(
            embed=discord.Embed(description=f"🔍 กำลังค้นหา **{query_str}**", color=0x1a1a2e))
        searching_msg = await interaction.original_response()

        async def _ack_done():
            try: await searching_msg.delete()
            except Exception: pass

        async def _send_error(text: str):
            try:
                err_msg = await interaction.followup.send(
                    embed=discord.Embed(description=text, color=discord.Color.red()),
                    ephemeral=True, wait=True)
                await asyncio.sleep(5)
                try: await err_msg.delete()
                except Exception: pass
            except Exception: pass

        try:
            # ตรวจ URL ก่อนเชื่อมต่อ VC เพื่อให้ Radio/Mix แสดงตัวเลือกทันที
            is_url = query_str.startswith("http://") or query_str.startswith("https://")

            # Radio/Mix: ไม่ต้องเชื่อม VC ก่อนแสดงตัวเลือก
            if is_url and is_youtube_radio_url(query_str):
                await _ack_done()
                await self._delete_done_msg()
                view = RadioChoiceView(
                    query_str, self.guild, self.channel, self.loop_getter,
                    interaction.user, self.loop,
                )
                prompt = await interaction.followup.send(
                    embed=discord.Embed(
                        title="📻 YouTube Radio / Mix",
                        description="ต้องการเล่นแบบไหน?",
                        color=0x1a1a2e,
                    ),
                    view=view,
                    ephemeral=True,
                    wait=True,
                )
                view.message = prompt
                return

            vc = self.guild.voice_client
            if not vc:
                if not interaction.user.voice:
                    await _ack_done()
                    await _send_error("❌ กรุณาเข้า Voice Channel ก่อน")
                    return
                try:
                    vc = await _connect_with_retry(interaction.user.voice.channel)
                except Exception as connect_error:
                    log("🔍 SEARCH CONNECT ERROR", interaction, str(connect_error))
                    await _ack_done()
                    await _send_error("❌ เชื่อมต่อ Voice Channel ไม่สำเร็จ (เน็ตบอทไม่เสถียร) กรุณาลองใหม่")
                    return
            elif interaction.user.voice and interaction.user.voice.channel != vc.channel:
                try:
                    await vc.move_to(interaction.user.voice.channel)
                except Exception as move_error:
                    log("🔍 SEARCH MOVE ERROR", interaction, str(move_error))
                    await _ack_done()
                    await _send_error("❌ ไม่สามารถย้ายบอทไป Voice Channel ของคุณได้")
                    return

            # ตรวจสอบว่าเป็น URL หรือไม่
            is_url = query_str.startswith("http://") or query_str.startswith("https://")

            # ตรวจ URL scheme ผิด เช่น ttps:// หรือ htp://
            looks_like_url = re.search(r'^[a-zA-Z]{2,10}://', query_str)
            if looks_like_url and not is_url:
                await _ack_done()
                await _send_error(f"❌ URL ไม่ถูกต้อง (`{query_str[:40]}`)\n💡 ลองวาง URL ใหม่อีกครั้ง")
                return


            # ตรวจสอบว่าเป็น playlist หรือไม่
            if is_url and is_playlist_url(query_str):
                # Handle playlist
                try:
                    playlist_tracks = await asyncio.to_thread(fetch_playlist_tracks, query_str)
                    if not playlist_tracks:
                        await _ack_done()
                        await _send_error("❌ ไม่พบเพลงในเพลย์ลิสต์")
                        return
                    
                    await _ack_done()
                    await self._delete_done_msg()
                    
                    added_results = await _add_playlist_to_queue(
                        vc, self.guild, self.channel, self.loop_getter,
                        playlist_tracks, interaction.user)
                    
                    if not added_results:
                        await _send_error("❌ ไม่สามารถดึงเพลงจากเพลย์ลิสต์ได้เลย")
                    return
                    
                except ValueError as e:
                    error_msg = str(e)
                    await _ack_done()
                    
                    if error_msg == "SPOTIFY_DRM_ERROR":
                        await _send_error("❌ Spotify ไม่สามารถเล่นได้ (DRM)\n💡 ค้นหาด้วยชื่อเพลงแทน")
                    elif error_msg == "SPOTIFY_SCRAPE_ERROR":
                        await _send_error("❌ ไม่สามารถดึงข้อมูลจาก Spotify ได้\n💡 ลองอีกครั้ง หรือค้นหาด้วยชื่อเพลงแทน")
                    else:
                        await _send_error("❌ ไม่สามารถโหลดเพลย์ลิสต์")
                    return

            # Handle single track URL
            if is_url:
                try:
                    url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query_str)
                except ValueError as e:
                    error_msg = str(e)
                    await _ack_done()
                    
                    if error_msg == "PLAYLIST_DETECTED":
                        await _send_error("❌ นี่คือเพลย์ลิสต์ ใช้เพื่อเพิ่มเพลงทั้งหมด")
                    elif error_msg == "SPOTIFY_SCRAPE_ERROR":
                        await _send_error("❌ ไม่สามารถดึงข้อมูลจาก Spotify ได้\n💡 ลองอีกครั้ง หรือค้นหาด้วยชื่อเพลงแทน")
                    elif error_msg == "SPOTIFY_NO_YOUTUBE_MATCH":
                        await _send_error("❌ ไม่พบบน YouTube\n💡 ลองค้นหาด้วยชื่อเพลง")
                    elif error_msg == "SPOTIFY_UNSUPPORTED_LINK":
                        await _send_error("❌ ไม่รองรับลิงก์ Spotify นี้ (เช่น พอดแคสต์/Show)\n💡 ลองส่งลิงก์เพลงเดี่ยว/เพลย์ลิสต์/ศิลปิน หรือค้นหาด้วยชื่อเพลงแทน")
                    else:
                        await _send_error("❌ เกิดข้อผิดพลาด")
                    return
                
                await self._delete_done_msg()
                track = (url, title, duration, interaction.user, thumbnail)
                await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track)
                await _ack_done()
                return

            # Search mode (not a URL)
            results = await asyncio.to_thread(search_tracks, query_str)
            if not results:
                await _ack_done()
                await _send_error("❌ ไม่พบเพลง")
                return

            await _ack_done()
            await self._delete_done_msg()

            await send_search_results(results, self.guild, self.channel, self.loop,
                                      self.loop_getter, interaction.user,
                                      done_msg_ref=self.done_msg_ref)

        except Exception as e:
            log("🔍 SEARCH SUBMIT ERROR", interaction, str(e))
            await _ack_done()
            await _send_error("❌ เกิดข้อผิดพลาด กรุณาลองใหม่")


# ─────────────────────────────────────────────
#  Search Result View
# ─────────────────────────────────────────────

class SearchResultView(discord.ui.View):
    def __init__(self, results, guild, channel, loop, loop_getter,
                 requester=None, done_msg_ref=None):
        super().__init__(timeout=30)
        self.results = results
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter
        self.requester = requester
        self.done_msg_ref = done_msg_ref
        self._selected: set[int] = set()
        self._adding_all = False
        self._selecting = False
        self.message: discord.Message | None = None
        self._select_item = discord.ui.Select(placeholder="เลือกเพลง", options=self._build_options(),
                                              custom_id="search_result_select")
        self._select_item.callback = self.select_callback
        self.add_item(self._select_item)

    def _build_options(self):
        options = []
        for i, r in enumerate(self.results):
            if i in self._selected:
                continue
            description = None
            duration = r.get("duration")
            if duration:
                description = f"⏱ {duration}"
            options.append(discord.SelectOption(label=_trunc(r["title"], 60), value=str(i), description=description))
        return options

    async def _check_requester(self, interaction):
        if self.requester and interaction.user.id != self.requester.id:
            await interaction.response.send_message(embed=discord.Embed(
                description="❌ เฉพาะผู้ค้นหาเท่านั้นที่เลือกได้", color=discord.Color.red()),
                ephemeral=True)
            return False
        return True

    def _remove_self_from_registry(self):
        msgs = search_result_msgs.get(self.guild.id, [])
        if self.message and self.message in msgs:
            msgs.remove(self.message)

    async def _close_message(self):
        self._remove_self_from_registry()
        self.stop()
        if self.message:
            try: await self.message.delete()
            except Exception: pass
            self.message = None

    async def _clear_done(self):
        if self.done_msg_ref and self.done_msg_ref[0]:
            try: await self.done_msg_ref[0].delete()
            except Exception: pass
            self.done_msg_ref[0] = None
        old_done = queue_done_msgs.pop(self.guild.id, None)
        if old_done:
            try: await old_done.delete()
            except Exception: pass

    @discord.ui.button(emoji="➕", label="เพิ่มทั้งหมด", style=discord.ButtonStyle.primary, row=1,
                       custom_id="search_result_add_all")
    async def add_all_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_requester(interaction): return
        if self._adding_all: return await interaction.response.defer()
        self._adding_all = True
        for item in self.children: item.disabled = True
        try: await interaction.response.edit_message(view=self)
        except Exception: await interaction.response.defer()

        vc = self.guild.voice_client
        if not vc:
            if not interaction.user.voice:
                await interaction.followup.send(embed=discord.Embed(
                    description="❌ กรุณาเข้า Voice Channel ก่อน", color=discord.Color.red()), ephemeral=True)
                return
            try:
                vc = await _connect_with_retry(interaction.user.voice.channel)
            except Exception:
                await interaction.followup.send(embed=discord.Embed(
                    description="❌ เชื่อมต่อ Voice Channel ไม่สำเร็จ (เน็ตบอทไม่เสถียร) กรุณาลองใหม่",
                    color=discord.Color.red()), ephemeral=True)
                return
        elif interaction.user.voice and interaction.user.voice.channel != vc.channel:
            try:
                await vc.move_to(interaction.user.voice.channel)
            except Exception:
                await interaction.followup.send(embed=discord.Embed(
                    description="❌ ไม่สามารถย้ายบอทไป Voice Channel ของคุณได้", color=discord.Color.red()), ephemeral=True)
                return

        await self._clear_done()
        # ใช้ flow เดียวกับ playlist: เพลงแรกเริ่มเล่นทันที ส่วนที่เหลือดึงพร้อมกัน
        # (จำกัดด้วย PLAYLIST_FETCH_CONCURRENCY) แล้วเพิ่ม/อัปเดตคิวเป็นชุดเดียว
        # จึงไม่ต้องรอ yt-dlp แบบทีละเพลง หรือแก้ข้อความ Now Playing ซ้ำทุกเพลง
        remaining_results = [
            result for i, result in enumerate(self.results)
            if i not in self._selected
        ]
        if remaining_results:
            await _add_playlist_to_queue(
                vc, self.guild, self.channel, self.loop_getter,
                remaining_results, interaction.user,
            )

        await self._close_message()

    @discord.ui.button(emoji="✖", label="ปิด", style=discord.ButtonStyle.danger, row=1,
                       custom_id="search_result_close")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._remove_self_from_registry()
        try:
            await interaction.response.defer()
            if self.message: await self.message.delete(); self.message = None
        except Exception: pass
        self.stop()

    async def select_callback(self, interaction: discord.Interaction):
        if not await self._check_requester(interaction): return
        if self._selecting: return await interaction.response.defer()
        self._selecting = True

        idx = int(interaction.data["values"][0])
        r = self.results[idx]
        self._selected.add(idx)

        try: await interaction.response.defer()
        except Exception: pass

        vc = self.guild.voice_client
        if not vc:
            if not interaction.user.voice:
                await interaction.followup.send(embed=discord.Embed(
                    description="❌ กรุณาเข้า Voice Channel ก่อน", color=discord.Color.red()), ephemeral=True)
                self._selected.discard(idx)
                self._selecting = False
                return
            try:
                vc = await _connect_with_retry(interaction.user.voice.channel)
            except Exception:
                await interaction.followup.send(embed=discord.Embed(
                    description="❌ เชื่อมต่อ Voice Channel ไม่สำเร็จ (เน็ตบอทไม่เสถียร) กรุณาลองใหม่",
                    color=discord.Color.red()), ephemeral=True)
                self._selected.discard(idx)
                self._selecting = False
                return
        elif interaction.user.voice and interaction.user.voice.channel != vc.channel:
            try:
                await vc.move_to(interaction.user.voice.channel)
            except Exception:
                await interaction.followup.send(embed=discord.Embed(
                    description="❌ ไม่สามารถย้ายบอทไป Voice Channel ของคุณได้", color=discord.Color.red()), ephemeral=True)
                self._selected.discard(idx)
                self._selecting = False
                return

        await self._clear_done()
        try:
            url, title, duration, thumbnail = await asyncio.to_thread(fetch_track_from_result, r)
            track = (url, title, duration, interaction.user, thumbnail)
            await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track)
        except Exception:
            await interaction.followup.send(embed=discord.Embed(
                description="❌ เกิดข้อผิดพลาด กรุณาลองใหม่", color=discord.Color.red()), ephemeral=True)
            self._selected.discard(idx)
            self._selecting = False
            return

        if len(self._selected) >= len(self.results):
            await self._close_message()
            return

        remaining = self._build_options()
        self._select_item.options = remaining
        self._select_item.placeholder = f"เลือกเพลง (เหลือ {len(remaining)} เพลง)"
        try: await interaction.edit_original_response(view=self)
        except Exception: pass
        self._selecting = False

    async def on_timeout(self):
        await self._close_message()


# ─────────────────────────────────────────────
#  Helper: เพิ่มเพลงและเล่น/queue
# ─────────────────────────────────────────────

def fetch_track_from_result(r: dict):
    """ดึงข้อมูลจาก search result dict เมื่อเลือกเพลงเท่านั้น"""
    video_id = r.get("id")
    if not video_id:
        raise ValueError("Missing video id")
    url = f"https://www.youtube.com/watch?v={video_id}"
    return fetch_track(url)

# _queue_locks = lock เบา ครอบแค่ add+play เพื่อป้องกัน race บน vc.play()
_queue_locks: dict[int, asyncio.Lock] = {}

def get_queue_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _queue_locks:
        _queue_locks[guild_id] = asyncio.Lock()
    return _queue_locks[guild_id]




def _fetch_playlist_track_sync(track_info: dict, guild_id: int, guild_name: str):
    """ดึงข้อมูล track เดียวจาก playlist entry (sync, รันใน thread)
    ถ้าดึงจาก URL/ID ตรงไม่ได้ (เช่น age-restricted, bot-check, private, ถูกลบ)
    จะลองค้นหาด้วยชื่อเพลงแทน เพื่อหาวิดีโอทดแทนที่เข้าถึงได้ แทนที่จะข้ามเพลงไปเฉยๆ
    รายละเอียดเต็ม (ชื่อเพลง/เหตุผล) บันทึกเข้าไฟล์ log ของ guild เท่านั้น (console=False)
    เพราะ console จะโชว์แค่ตัวเลขความคืบหน้ารวมผ่าน _PlaylistFetchProgress แทน
    คืนค่า (result, outcome) — outcome: "direct" | "fallback_ok" | "fallback_fail"
    """
    orig_title = track_info.get("title", "Unknown")

    def _reason(e: Exception) -> str:
        # ตัดข้อความ error ยาวๆ ของ yt-dlp เหลือแค่บรรทัดแรกสั้นๆ พอให้รู้สาเหตุ
        first_line = str(e).splitlines()[0] if str(e) else str(e)
        return _trunc(first_line.replace("ERROR: [youtube] ", ""), 60)

    try:
        if "url" in track_info:
            return fetch_track(track_info["url"]), "direct"

        elif "id" in track_info and track_info.get("id"):
            yt_url = f"https://www.youtube.com/watch?v={track_info['id']}"
            try:
                return fetch_track(yt_url), "direct"
            except Exception as e:
                glog(guild_id, guild_name,
                     f"⚠ ดึงตรงไม่ได้ [{_reason(e)}] — ลองหาแทน: {_trunc(orig_title, 40)}",
                     level="warning", console=False)
                try:
                    result = fetch_track(orig_title)
                    found_title = result[1] if result else "?"
                    glog(guild_id, guild_name,
                         f"✅ ทดแทนสำเร็จ: {_trunc(orig_title, 35)} → {_trunc(found_title, 35)}",
                         level="info", console=False)
                    return result, "fallback_ok"
                except Exception as e2:
                    glog(guild_id, guild_name,
                         f"❌ ข้ามเพลง [{_reason(e2)}]: {_trunc(orig_title, 40)}",
                         level="error", console=False)
                    return None, "fallback_fail"

        elif "artist" in track_info:
            search_query = f"{track_info['title']} {track_info['artist']}"
            glog(guild_id, guild_name,
                 f"🎵 Spotify→YT: {_trunc(track_info['title'], 40)} — {_trunc(track_info['artist'], 30)}",
                 level="info", console=False)
            return _fetch_spotify_track_from_search(search_query), "direct"

        return None, "fallback_fail"
    except Exception as e:
        glog(guild_id, guild_name,
             f"❌ ข้ามเพลง [{_reason(e)}]: {_trunc(orig_title, 40)}",
             level="error", console=False)
        return None, "fallback_fail"


class _PlaylistFetchProgress:
    """เก็บสถานะความคืบหน้าการดึงเพลงจาก playlist ใช้ร่วมกันระหว่างช่วงหาเพลงแรก (step1)
    และช่วงดึงที่เหลือใน background (_bg_fetch_rest) — รวมเป็นตัวนับเดียวกันตลอดทั้ง playlist
    เมธอด record() ต้องถูกเรียกจาก event loop thread เท่านั้น (หลัง await เสร็จ) จึงไม่ต้องใช้ lock
    console จะเห็นแค่ตัวเลข ไม่มีชื่อเพลง — รายละเอียดเต็มอยู่ในไฟล์ log ของ guild แทน

    แสดงผลบน console เป็นบรรทัดเดียว overwrite ตัวเองด้วย \\r (ไม่ print บรรทัดใหม่ทุกเพลง)
    ต้อง print_summary() ปิดท้ายเสมอ เพื่อขึ้นบรรทัดใหม่จริงก่อน log ถัดไปจะพิมพ์ทับกัน
    """
    def __init__(self, guild_id: int, guild_name: str, total: int):
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.total = total
        self.done = 0
        self.direct_ok = 0
        self.fallback_attempts = 0
        self.fallback_ok = 0
        self.skipped = 0
        self._last_line_len = 0

    def _p(self, msg: str):
        """เขียนทับบรรทัด progress เดิมด้วย \\r — เติม space ปิดท้ายกันตัวอักษรเก่าเหลือค้าง
        ถ้าบรรทัดใหม่สั้นกว่าบรรทัดก่อนหน้า (เช่น ตัวเลขเปลี่ยนจากหลักสิบเป็นหลักเดียว)
        """
        line = f"[{self.guild_name}] {msg}"
        pad = max(0, self._last_line_len - len(line))
        print(f"\r{line}{' ' * pad}", end="", flush=True)
        self._last_line_len = len(line)

    def record(self, outcome: str):
        if outcome == "direct":
            self.done += 1
            self.direct_ok += 1
        elif outcome == "fallback_ok":
            self.done += 1
            self.fallback_attempts += 1
            self.fallback_ok += 1
        else:  # fallback_fail
            self.fallback_attempts += 1
            self.skipped += 1

        parts = [f"กำลังเพิ่มเพลง {self.done}/{self.total}"]
        if self.fallback_attempts:
            parts.append(f"ทดแทน {self.fallback_ok}/{self.fallback_attempts}")
        if self.skipped:
            parts.append(f"ข้าม {self.skipped}")
        self._p(" · ".join(parts))

    def print_summary(self):
        self._p(f"เพิ่มเพลงครบ {self.done}/{self.total} "
                f"(ตรงสำเร็จ {self.direct_ok} · ทดแทน {self.fallback_ok}/{self.fallback_attempts} · ข้ามจริง {self.skipped})")
        print()  # ขึ้นบรรทัดใหม่จริง ปิดท้าย progress bar ก่อน log ถัดไป


async def _add_playlist_to_queue(vc, guild, channel, loop_getter, playlist_tracks, requester):
    """เพิ่ม playlist เข้า queue โดยเล่นเพลงแรกทันทีที่ดึงสำเร็จ (ไม่ต้องรอทั้งเพลย์ลิสต์)
    ถ้าเพลงแรกดึงไม่สำเร็จ (เช่น age-restricted) จะลองเพลงถัดไปเป็น "เพลงเริ่ม" แทนอัตโนมัติ
    ส่วนที่เหลือจะถูกดึงและเพิ่มเข้าคิวต่อใน background task (_bg_fetch_rest)
    คืนค่า list of (track_idx, title) — มีแค่เพลงแรกที่เล่นทันที (เพลงที่เหลือมาทีหลังผ่าน summary แยก)
    """
    if not playlist_tracks:
        return []

    progress = _PlaylistFetchProgress(guild.id, guild.name, len(playlist_tracks))

    # ── step 1: ดึงเพลงแรกก่อน (ทีละเพลง) เพื่อเริ่มเล่นให้เร็วที่สุด ──
    # ถ้าเพลงไหนดึงไม่ได้ (เช่น age-restricted) ข้ามไปลองเพลงถัดไปเป็น "เพลงเริ่ม" แทน
    first_result = None
    remaining_tracks = list(playlist_tracks)

    while remaining_tracks:
        candidate = remaining_tracks.pop(0)
        result, outcome = await asyncio.to_thread(_fetch_playlist_track_sync, candidate, guild.id, guild.name)

        if guild.id in guild_stopped:
            print(f"\n[{guild.name}] 🛑 Playlist fetch ยกเลิก — ถูก stop ระหว่าง fetch")
            return []

        progress.record(outcome)

        if result:
            first_result = result
            break
        # ดึงไม่สำเร็จ → ข้ามไปลองเพลงถัดไปเป็นเพลงเริ่มแทน (candidate ถูก pop ทิ้งแล้ว ไม่กลับมาลองอีก)

    if not first_result:
        progress.print_summary()
        return []  # ดึงไม่สำเร็จสักเพลงเลยในทั้งเพลย์ลิสต์

    # ── step 2: เพิ่มเพลงแรกเข้าคิว + เล่นทันที ──
    url, title, duration, thumbnail = first_result
    first_added = None
    async with get_queue_lock(guild.id):
        track = (url, title, duration, requester, thumbnail)
        track_idx = add_to_queue(guild.id, track)
        first_was_empty = not (vc.is_playing() or vc.is_paused())

        if first_was_empty:
            set_now_idx(guild.id, track_idx)
            _trim_queue(guild.id)
            track_idx = get_now_idx(guild.id)
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=get_guild_volume(guild.id))
            loop = loop_getter()
            session_id = _new_player_session(guild.id)
            view = PlayerView(
                guild, channel, loop,
                current_track=track,
                current_idx=track_idx,
                loop_getter=loop_getter,
                player_id=session_id,
            )
            active_views[guild.id] = view
            token = _next_playback_generation(guild.id)
            vc.play(
                source,
                after=lambda e, _session_id=session_id, _t=track, _ti=track_idx, _token=token:
                    asyncio.run_coroutine_threadsafe(
                        play_next(
                            guild, channel, loop,
                            current_track=_t,
                            current_idx=_ti,
                            error=e,
                            playback_token=_token,
                            player_session_id=_session_id,
                        ),
                        loop,
                    ),
            )
            embed = make_now_playing_embed(title, duration, requester, thumbnail,
                                           _queue_pos_str(guild.id, track_idx))
            msg = await channel.send(embed=embed, view=view)
            view.now_playing_msg = msg
        else:
            # เพลงแรกของชุดนี้ถูกต่อท้ายคิวอยู่แล้ว ต้องนำไปแสดงใน summary
            # ร่วมกับเพลงที่ background fetch เพิ่มภายหลังด้วย
            first_added = track
            await _refresh_queue_msg(guild.id)
            old_view = active_views.get(guild.id)
            if old_view and old_view.now_playing_msg and old_view.current_track:
                _u, _ti, _du, _rq, *_th = old_view.current_track
                _tn = _th[0] if _th else None
                try:
                    await old_view.now_playing_msg.edit(embed=make_now_playing_embed(
                        _ti, _du, _rq, _tn, _queue_pos_str(guild.id, get_now_idx(guild.id))))
                except Exception:
                    pass

    # ── step 3: ดึงเพลงที่เหลือ (ถ้ามี) แบบ concurrent ใน background — ไม่บล็อกการเล่นเพลงแรก ──
    if remaining_tracks:
        asyncio.create_task(_bg_fetch_rest(
            guild, channel, remaining_tracks, requester, progress,
            initial_added=[first_added] if first_added else [],
        ))
    else:
        progress.print_summary()
        if first_added:
            await _send_playlist_added_summary(guild.id, channel, requester, [first_added])

    return [(track_idx, title)]


async def _bg_fetch_rest(guild, channel, rest_tracks, requester, progress: "_PlaylistFetchProgress",
                         initial_added=None):
    """ดึงเพลงที่เหลือของ playlist (หลังเพลงแรก) แบบ concurrent (จำกัดจำนวนพร้อมกัน) ในพื้นหลัง
    แล้วเพิ่มเข้าคิวทั้งหมดพร้อมกันด้วย queue_lock ครั้งเดียว (atomic)
    จำกัด concurrency ด้วย Semaphore กัน YouTube rate-limit (429) ตอน playlist ยาวๆ
    progress: ตัวนับความคืบหน้าเดียวกับที่ใช้ใน step1 ของ _add_playlist_to_queue (นับรวมทั้ง playlist)
    """
    sem = asyncio.Semaphore(PLAYLIST_FETCH_CONCURRENCY)

    async def _fetch_one(track_info):
        async with sem:
            result, outcome = await asyncio.to_thread(_fetch_playlist_track_sync, track_info, guild.id, guild.name)
            progress.record(outcome)
            return result

    fetch_results = await asyncio.gather(*(_fetch_one(t) for t in rest_tracks))
    progress.print_summary()

    if guild.id in guild_stopped:
        print(f"[{guild.name}] 🛑 Playlist bg fetch ยกเลิก — ถูก stop ระหว่าง fetch")
        return

    fetched = [(url, title, duration, thumbnail)
               for r in fetch_results if r is not None
               for url, title, duration, thumbnail in [r]]

    added = list(initial_added or [])
    if not fetched:
        if added:
            await _send_playlist_added_summary(guild.id, channel, requester, added)
        return

    async with get_queue_lock(guild.id):
        for url, title, duration, thumbnail in fetched:
            track = (url, title, duration, requester, thumbnail)
            add_to_queue(guild.id, track)
            added.append(track)
        await _refresh_queue_msg(guild.id)
        # อัปเดต now playing embed ให้เลข "จาก X เพลง" ตรงกับจำนวนจริงทันที
        # ไม่งั้นเลขจะค้างที่ตอนเพลงแรกเริ่มเล่น จนกว่าจะ skip/prev หรือเพลงเปลี่ยนเอง
        old_view = active_views.get(guild.id)
        if old_view and old_view.now_playing_msg and old_view.current_track:
            _u, _ti, _du, _rq, *_th = old_view.current_track
            _tn = _th[0] if _th else None
            try:
                await old_view.now_playing_msg.edit(embed=make_now_playing_embed(
                    _ti, _du, _rq, _tn, _queue_pos_str(guild.id, get_now_idx(guild.id))))
            except Exception:
                pass

    await _send_playlist_added_summary(guild.id, channel, requester, added)

async def _add_and_play(vc, guild, channel, loop_getter, track):
    """เพิ่มเพลงเข้า queue และเล่นถ้าว่าง
    คืนค่า (track_idx, title) — track_idx คือลำดับจริงในคิว (0-based)
    """
    async with get_queue_lock(guild.id):
        track_idx = add_to_queue(guild.id, track)
        url, title, duration, requester, thumbnail, *_rest = track

        if vc.is_playing() or vc.is_paused():
            pos = display_no(guild.id, track_idx)
            display_title = queue_display_titles.get(url, title)
            short_title = _truncate_display_width(display_title, 50)
            pub_msg = await channel.send(embed=discord.Embed(
                description=f"📋 เพิ่มใน Queue **#{pos}**\n🎵 {short_title}  |  ขอโดย: {requester.mention}",
                color=0x1a1a2e))
            queue_add_msgs.setdefault(guild.id, {})[track_idx] = pub_msg
            await _refresh_queue_msg(guild.id)
            old_view = active_views.get(guild.id)
            if old_view and old_view.now_playing_msg and old_view.current_track:
                _u, _ti, _du, _rq, *_th = old_view.current_track
                _tn = _th[0] if _th else None
                try:
                    await old_view.now_playing_msg.edit(embed=make_now_playing_embed(
                        _ti, _du, _rq, _tn, _queue_pos_str(guild.id, get_now_idx(guild.id))))
                except Exception: pass
        else:
            set_now_idx(guild.id, track_idx)
            _trim_queue(guild.id)
            track_idx = get_now_idx(guild.id)
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=get_guild_volume(guild.id))
            loop = loop_getter()
            session_id = _new_player_session(guild.id)
            view = PlayerView(
                guild, channel, loop,
                current_track=track,
                current_idx=track_idx,
                loop_getter=loop_getter,
                player_id=session_id,
            )
            active_views[guild.id] = view
            _g, _ch, _lp, _t, _ti = guild, channel, loop, track, track_idx
            token = _next_playback_generation(guild.id)
            vc.play(
                source,
                after=lambda e, g=_g, ch=_ch, lp=_lp, t=_t, ti=_ti,
                               _session_id=session_id, _token=token:
                    asyncio.run_coroutine_threadsafe(
                        play_next(
                            g, ch, lp,
                            current_track=t,
                            current_idx=ti,
                            error=e,
                            playback_token=_token,
                            player_session_id=_session_id,
                        ),
                        lp,
                    ),
            )
            embed = make_now_playing_embed(title, duration, requester, thumbnail,
                                           _queue_pos_str(guild.id, track_idx))
            msg = await channel.send(embed=embed, view=view)
            view.now_playing_msg = msg

        return track_idx, title


async def _send_playlist_added_summary(guild_id: int, channel, requester, tracks: list):
    """ส่งสรุปเพลงที่เพิ่มจาก playlist เป็นข้อความเดียว ให้ทุกคนเห็น (ไม่ ephemeral)
    tracks: track tuples ที่ถูกเพิ่มในชุดนี้ ใช้ object identity เพื่อหาตำแหน่งล่าสุดในคิว
    หลัง _trim_queue เลื่อน index จึงไม่อ้าง index ที่บันทึกก่อนเพิ่มเพลงครบ
    """
    if not tracks:
        return
    queue = get_full_queue(guild_id)
    added_positions = [
        (idx, track)
        for track in tracks
        for idx, queued_track in enumerate(queue)
        if queued_track is track
    ]
    if not added_positions:
        return

    lines = [
        f"`#{display_no(guild_id, idx)}` "
        f"{_truncate_display_width(queue_display_titles.get(track[0], track[1]), 58)}"
        for idx, track in added_positions
    ]
    embed = discord.Embed(
        description="\n".join(lines) + f"\n\nขอโดย: {requester.mention}",
        color=0x1a1a2e,
    )
    embed.set_author(name=f"📋  เพิ่มเข้า Queue แล้ว {len(added_positions)} เพลง")
    try:
        pub_msg = await channel.send(embed=embed)
        last_idx = added_positions[-1][0]
        queue_add_msgs.setdefault(guild_id, {})[last_idx] = pub_msg
    except Exception:
        pass


# ─────────────────────────────────────────────
#  _do_play_at_idx — core ของการ skip/prev
#  เรียกหลัง interaction ถูก defer แล้วเท่านั้น
# ─────────────────────────────────────────────

async def _do_play_at_idx(view: "PlayerView", idx: int):
    """Switch to an existing queue item and start playback safely."""
    guild_id = view.guild.id
    session_id = _get_player_session(guild_id)
    if not session_id or view.player_id != session_id:
        raise RuntimeError("Player session is no longer active")

    async with get_queue_lock(guild_id):
        q = get_full_queue(guild_id)
        if not q or idx < 0 or idx >= len(q):
            raise IndexError(f"Invalid queue index: {idx}")

        set_now_idx(guild_id, idx)
        _trim_queue(guild_id)
        idx = get_now_idx(guild_id)

        q = get_full_queue(guild_id)
        if not q or idx < 0 or idx >= len(q):
            raise IndexError(f"Queue index became invalid after trim: {idx}")

        track = q[idx]
        url, title, duration, requester, thumbnail, *_rest = track
        vc = view.guild.voice_client
        if vc is None:
            raise RuntimeError("Voice client is not connected")

        view.current_track = track
        view.current_idx = idx
        view.volume_level = get_guild_volume(guild_id)
        active_views[guild_id] = view

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS),
            volume=view.volume_level,
        )

        # Generate a unique token before stopping the old source.
        token = _next_playback_generation(guild_id)
        vc.stop()
        vc.play(
            source,
            after=lambda e, _session_id=session_id, _token=token, _idx=idx, _track=track:
                asyncio.run_coroutine_threadsafe(
                    play_next(
                        view.guild, view.channel, view.loop,
                        current_track=_track,
                        current_idx=_idx,
                        error=e,
                        playback_token=_token,
                        player_session_id=_session_id,
                    ),
                    view.loop,
                ),
        )

        add_msg = queue_add_msgs.get(guild_id, {}).pop(idx, None)

    if add_msg:
        try:
            await add_msg.delete()
        except Exception:
            pass

    await _refresh_player(guild_id)
    await _refresh_queue_msg(guild_id)


# ─────────────────────────────────────────────
#  Full Queue View — ephemeral, per-user
# ─────────────────────────────────────────────

class QueueView(discord.ui.View):
    def __init__(self, guild, page=0):
        super().__init__(timeout=180)
        self.guild = guild
        self.page = page
        self._sync_buttons()

    def _page_count(self):
        q = get_full_queue(self.guild.id)
        return max(1, (len(q) + QUEUE_PAGE_SIZE - 1) // QUEUE_PAGE_SIZE)
    def _sync_buttons(self):
        total_pages = self._page_count()
        self.page = max(0, min(self.page, total_pages - 1))
        for item in self.children:
            if item.custom_id == "queue_previous_page":
                item.disabled = self.page <= 0
            elif item.custom_id == "queue_page":
                item.label = f"{self.page + 1} / {total_pages}"
                item.disabled = True
            elif item.custom_id == "queue_next_page":
                item.disabled = self.page >= total_pages - 1

    async def _update(self, interaction: discord.Interaction):
        self._sync_buttons()
        embed = make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id), page=self.page)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, custom_id="queue_previous_page", row=0)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        if self.page <= 0:
            return
        self.page -= 1
        await interaction.edit_original_response(
            embed=make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id), page=self.page),
            view=self,
        )

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True, custom_id="queue_page", row=0)
    async def page_indicator(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, custom_id="queue_next_page", row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        if self.page >= self._page_count() - 1:
            return
        self.page += 1
        await interaction.edit_original_response(
            embed=make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id), page=self.page),
            view=self,
        )

#  Player View
# ─────────────────────────────────────────────

class PlayerView(discord.ui.View):
    def __init__(self, guild, channel, loop, current_track=None, current_idx=None, loop_getter=None,
                 player_id=None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter or (lambda: loop)
        self.player_id = player_id or _get_player_session(guild.id)
        self.current_track = current_track
        self.current_idx = current_idx if current_idx is not None else get_now_idx(guild.id)
        self.now_playing_msg: discord.Message | None = None
        self.volume_level: float = get_guild_volume(guild.id)
        self._sync_state_buttons()

    def _sync_state_buttons(self):
        for item in self.children:
            if item.custom_id == "player_shuffle":
                item.style = (
                    discord.ButtonStyle.success
                    if self.guild.id in shuffle_enabled
                    else discord.ButtonStyle.secondary
                )
            elif item.custom_id == "player_loop":
                mode = loop_modes.get(self.guild.id, "off")
                item.style = (
                    discord.ButtonStyle.success
                    if mode != "off"
                    else discord.ButtonStyle.secondary
                )
                item.emoji = {"off": "🔁", "track": "🔂", "queue": "🔁"}[mode]
            elif item.custom_id == "player_stop":
                item.style = discord.ButtonStyle.danger
            elif item.custom_id == "player_show_queue":
                item.style = discord.ButtonStyle.primary

    async def delete_now_playing(self):
        if self.now_playing_msg:
            try: await self.now_playing_msg.delete()
            except Exception: pass
            self.now_playing_msg = None

    def _current_embed(self):
        if not self.current_track:
            return discord.Embed(description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red())
        _url, title, duration, requester, thumbnail, *_ = self.current_track
        return make_now_playing_embed(
            title, duration, requester, thumbnail,
            _queue_pos_str(self.guild.id, get_now_idx(self.guild.id)),
        )

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, row=0, custom_id="player_shuffle")
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        if not await check_in_voice(interaction): return
        try:
            await interaction.response.defer()
        except Exception:
            pass
        async with get_queue_lock(self.guild.id):
            q = get_full_queue(self.guild.id)
            idx = get_now_idx(self.guild.id)
            upcoming = q[idx + 1:]
            if len(upcoming) < 2:
                return await safe_respond(interaction, embed=discord.Embed(
                    description="❌ ต้องมีเพลงถัดไปอย่างน้อย 2 เพลงจึงจะ Shuffle ได้",
                    color=discord.Color.orange()), ephemeral=True)
            random.shuffle(upcoming)
            q[idx + 1:] = upcoming
            shuffle_enabled.add(self.guild.id)
        log("🔀 SHUFFLE", interaction, f"upcoming={len(upcoming)}")
        await asyncio.gather(
            _refresh_player(self.guild.id),
            _refresh_queue_msg(self.guild.id),
        )

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, row=0, custom_id="player_previous")
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        try:
            await interaction.response.defer()
        except Exception:
            pass
        if not await check_in_voice(interaction):
            return
        async with get_navigation_lock(self.guild.id):
            idx = get_now_idx(self.guild.id)
            if idx <= 0:
                return await safe_respond(interaction, embed=discord.Embed(
                    description="❌ ไม่มีเพลงก่อนหน้าแล้ว", color=discord.Color.red()), ephemeral=True)
            log("⏮ PREV", interaction, f"idx {idx} → {idx-1}")
            await _do_play_at_idx(self, idx - 1)

    @discord.ui.button(emoji="⏸️", style=discord.ButtonStyle.secondary, row=0, custom_id="player_pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        try:
            await interaction.response.defer()
        except Exception:
            pass
        if not await check_in_voice(interaction): return
        vc = self.guild.voice_client
        title = _trunc(self.current_track[1]) if self.current_track else "?"
        if vc.is_playing():
            vc.pause()
            log("⏸ PAUSE", interaction, f"Track: {title}")
        elif vc.is_paused():
            vc.resume()
            log("▶️ RESUME", interaction, f"Track: {title}")
        else:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
        await _refresh_player(self.guild.id)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, row=0, custom_id="player_skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        try:
            await interaction.response.defer()
        except Exception:
            pass
        if not await check_in_voice(interaction):
            return
        async with get_navigation_lock(self.guild.id):
            vc = self.guild.voice_client
            if not (vc.is_playing() or vc.is_paused()):
                return await safe_respond(interaction, embed=discord.Embed(
                    description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
            idx = get_now_idx(self.guild.id)
            q = get_full_queue(self.guild.id)
            if idx + 1 >= len(q):
                log("⏭ SKIP", interaction, f"idx {idx} → end")
                vc.stop()
                return
            log("⏭ SKIP", interaction, f"idx {idx} → {idx+1}")
            await _do_play_at_idx(self, idx + 1)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, row=0, custom_id="player_loop")
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        if not await check_in_voice(interaction): return
        try:
            await interaction.response.defer()
        except Exception:
            pass
        async with get_queue_lock(self.guild.id):
            current = loop_modes.get(self.guild.id, "off")
            next_mode = {"off": "track", "track": "queue", "queue": "off"}[current]
            loop_modes[self.guild.id] = next_mode
        mode_text = {"off": "ปิด Loop", "track": "วนเพลงนี้", "queue": "วน Queue"}[next_mode]
        log("🔁 LOOP", interaction, mode_text)
        await asyncio.gather(
            _refresh_player(self.guild.id),
            _refresh_queue_msg(self.guild.id),
        )

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, row=1, custom_id="player_search")
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        if not await check_in_voice(interaction): return
        loop = self.loop_getter()
        modal = SearchModal(self.guild, self.channel, loop, self.loop_getter)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1, custom_id="player_volume")
    async def volume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        if not await check_in_voice(interaction): return
        vc = self.guild.voice_client
        if not vc.source:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
        modal = VolumeModal(vc, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.primary, row=1, custom_id="player_show_queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await _is_current_player(self):
            return await safe_respond(interaction, content="❌ Player นี้หมดอายุแล้ว", ephemeral=True)
        view = QueueView(self.guild, page=0)
        embed = make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id), page=0)
        try:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            message = await interaction.original_response()
            key = (self.guild.id, interaction.user.id)
            old_entry = queue_view_msgs.get(key)
            if old_entry:
                try:
                    await old_entry[0].delete()
                except Exception:
                    pass
            queue_view_msgs[key] = (message, view)
        except Exception:
            pass

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, row=1, custom_id="player_stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        vc = self.guild.voice_client
        log("⏹ STOP", interaction, f"Track: {_trunc(self.current_track[1]) if self.current_track else '?'}")
        try: await interaction.response.defer()
        except Exception: pass
        async with get_navigation_lock(self.guild.id):
            await asyncio.gather(
                _delete_queue_add_msgs(self.guild.id),
                _delete_search_result_msgs(self.guild.id),
                _delete_queue_view_msg(self.guild.id),
            )
            guild_stopped.add(self.guild.id)
            now_playing_msg = self.now_playing_msg
            self.now_playing_msg = None
            clear_guild(self.guild.id)
            vc.stop()
            await vc.disconnect()
        old_done = queue_done_msgs.pop(self.guild.id, None)
        if old_done:
            try: await old_done.delete()
            except Exception: pass
        done_embed = make_done_embed()
        if now_playing_msg:
            try:
                await now_playing_msg.edit(embed=done_embed, view=None)
                queue_done_msgs[self.guild.id] = now_playing_msg
            except Exception:
                done_msg = await self.channel.send(embed=done_embed)
                queue_done_msgs[self.guild.id] = done_msg
        else:
            done_msg = await self.channel.send(embed=done_embed)
            queue_done_msgs[self.guild.id] = done_msg

#  handle_external_voice_disconnect#  handle_external_voice_disconnect
#  เรียกจาก bot.py เมื่อบอทถูก kick/disconnect จาก VC โดยไม่ได้ตั้งใจ
#  (เช่นแอดมิน kick, หลุดจากปัญหาเน็ต/Discord แล้ว reconnect ไม่ติด)
#  ต้องเก็บสถานะให้เหมือนกดปุ่มหยุด/ /stop แต่ห้ามยุ่งกับ vc เพราะหลุดไปแล้วจริง
# ─────────────────────────────────────────────

async def handle_external_voice_disconnect(guild: discord.Guild):
    """ทำความสะอาดสถานะเมื่อบอทหลุดจาก Voice Channel โดยไม่ได้มาจาก /stop หรือปุ่มหยุด
    ไม่เรียก vc.stop()/vc.disconnect() เพราะการเชื่อมต่อหลุดไปแล้วจริง (เรียกซ้ำจะพัง/ไม่มีผล)
    ไม่มี interaction ในสถานการณ์นี้ จึงต้องหา channel จาก active_view ที่เก็บไว้ก่อน clear_guild
    """
    old_view = active_views.get(guild.id)
    channel = old_view.channel if old_view else None
    now_playing_msg = old_view.now_playing_msg if old_view else None
    current_title = _trunc(old_view.current_track[1]) if old_view and old_view.current_track else "?"

    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_block = (
        f"\n[{ts}] ⏹ EXTERNAL_DISCONNECT\n"
        f"  {'Guild':<9}: {_trunc(guild.name, 30)} ({guild.id})\n"
        f"  {'เพลง':<9}: {current_title}"
    )
    print(full_block)
    get_guild_logger(guild.id, guild.name).info(full_block)

    if old_view:
        old_view.now_playing_msg = None

    async with get_navigation_lock(guild.id):
        guild_stopped.add(guild.id)
        clear_guild(guild.id)

    await asyncio.gather(
        _delete_queue_add_msgs(guild.id),
        _delete_search_result_msgs(guild.id),
        _delete_queue_view_msg(guild.id),
    )

    if not channel:
        return  # ไม่มี channel ให้แจ้งเตือน (เช่นบอทหลุดตอนยังไม่เคยเล่นเพลงเลย) — เคลียร์สถานะพอ

    old_done = queue_done_msgs.pop(guild.id, None)
    if old_done:
        try: await old_done.delete()
        except Exception: pass

    done_embed = discord.Embed(
        description="⏹ บอทถูกตัดการเชื่อมต่อจาก Voice Channel — หยุดเล่นเพลงแล้ว",
        color=discord.Color.orange())

    if now_playing_msg:
        try:
            await now_playing_msg.edit(embed=done_embed, view=None)
            queue_done_msgs[guild.id] = now_playing_msg
            return
        except Exception:
            pass

    try:
        done_msg = await channel.send(embed=done_embed)
        queue_done_msgs[guild.id] = done_msg
    except Exception:
        pass


# ─────────────────────────────────────────────
#  play_next — เรียกเมื่อเพลงจบตามธรรมชาติ
# ─────────────────────────────────────────────

async def play_next(guild: discord.Guild, channel: discord.TextChannel, loop,
                    current_track=None, current_idx: int = None, error=None,
                    playback_token: int = None, player_session_id: str = None):
    # Ignore callbacks from an older Player session before doing any state work.
    if player_session_id is not None and player_session_id != _get_player_session(guild.id):
        return

    # Ignore callbacks from an older playback generation.
    if playback_token is not None and playback_token != playback_generation.get(guild.id):
        return

    # หยุดจงใจ (stop)
    if guild.id in guild_stopped:
        guild_stopped.discard(guild.id)
        return

    q_current = get_full_queue(guild.id)
    if current_idx is not None:
        if current_idx != get_now_idx(guild.id):
            return
        if not q_current or current_idx < 0 or current_idx >= len(q_current):
            return
        if current_track is not None and q_current[current_idx] is not current_track:
            return

    # เพลงก่อนหน้าเล่นไม่ได้ (error จริง ไม่ใช่เล่นจบปกติ/ถูก stop ตั้งใจ)
    # แจ้งในแชทให้ทุกคนเห็น ก่อนข้ามไปเพลงถัดไป
    if error is not None:
        failed_title = None
        if current_track:
            failed_title = current_track[1]
        elif current_idx is not None:
            q_now = get_full_queue(guild.id)
            if 0 <= current_idx < len(q_now):
                failed_title = q_now[current_idx][1]
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        glog(
            guild.id,
            guild.name,
            f"[{ts}] ✗ PLAYBACK_ERROR\n"
            f"  {'Track':<9}: {_trunc(failed_title or '?', 60)}\n"
            f"  {'Error':<9}: {_trunc(str(error), 120)}",
            level="error",
            console=False,
        )
        try:
            await channel.send(embed=discord.Embed(
                description=f"❌ เล่น **{_trunc(failed_title or 'เพลงนี้', 60)}** ไม่ได้ กำลังข้ามไปเพลงถัดไป",
                color=discord.Color.red()))
        except Exception:
            pass

    if current_idx is None:
        current_idx = get_now_idx(guild.id)

    loop_mode = loop_modes.get(guild.id, "off")
    q_snapshot = get_full_queue(guild.id)

    if loop_mode == "track" and error is None and 0 <= current_idx < len(q_snapshot):
        next_idx = current_idx
    elif loop_mode == "queue" and error is None and q_snapshot and current_idx >= len(q_snapshot) - 1:
        next_idx = 0
    else:
        next_idx = current_idx + 1

    # Acquire lock ก่อนจะแก้ index ป้องกัน race condition กับ skip/prev
    async with get_queue_lock(guild.id):
        q = get_full_queue(guild.id)
        has_next = next_idx < len(q)

        if loop_mode == "track" and error is None and has_next:
            set_now_idx(guild.id, next_idx)
            track = q[next_idx]
            url, title, duration, requester, thumbnail, *_rest = track
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS),
                volume=get_guild_volume(guild.id),
            )
            embed = make_now_playing_embed(
                title, duration, requester, thumbnail,
                _queue_pos_str(guild.id, next_idx),
            )

            old_view = active_views.get(guild.id)
            if old_view and old_view.now_playing_msg:
                old_view.current_track = track
                old_view.current_idx = next_idx
                view = old_view
                try:
                    await view.now_playing_msg.edit(embed=embed, view=view)
                except Exception:
                    view.now_playing_msg = None
                    msg = await channel.send(embed=embed, view=view)
                    view.now_playing_msg = msg
            else:
                view = PlayerView(
                    guild, channel, loop,
                    current_track=track,
                    current_idx=next_idx,
                    loop_getter=lambda: loop,
                )
                active_views[guild.id] = view
                msg = await channel.send(embed=embed, view=view)
                view.now_playing_msg = msg

            token = _next_playback_generation(guild.id)
            session_id = _get_player_session(guild.id)
            if not session_id:
                return

            guild.voice_client.play(
                source,
                after=lambda e, _session_id=session_id, _token=token, _idx=next_idx, _track=track:
                    asyncio.run_coroutine_threadsafe(
                        play_next(
                            guild, channel, loop,
                            current_track=_track,
                            current_idx=_idx,
                            error=e,
                            playback_token=_token,
                            player_session_id=_session_id,
                        ),
                        loop,
                    ),
            )
            return

        if has_next:
            set_now_idx(guild.id, next_idx)
            _trim_queue(guild.id)
            next_idx = get_now_idx(guild.id)
            q = get_full_queue(guild.id)

            track = q[next_idx]
            url, title, duration, requester, thumbnail, *_rest = track
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=get_guild_volume(guild.id))
            embed = make_now_playing_embed(title, duration, requester, thumbnail,
                                           _queue_pos_str(guild.id, next_idx))

            old_view = active_views.get(guild.id)
            if old_view and old_view.now_playing_msg:
                # Reuse view เดิม — แค่ edit embed
                old_view.current_track = track
                old_view.current_idx = next_idx
                view = old_view
                try:
                    await view.now_playing_msg.edit(embed=embed, view=view)
                except Exception:
                    view.now_playing_msg = None
                    msg = await channel.send(embed=embed, view=view)
                    view.now_playing_msg = msg
            else:
                view = PlayerView(guild, channel, loop, current_track=track, current_idx=next_idx, loop_getter=lambda: loop)
                active_views[guild.id] = view
                msg = await channel.send(embed=embed, view=view)
                view.now_playing_msg = msg

            token = _next_playback_generation(guild.id)
            session_id = _get_player_session(guild.id)
            if not session_id:
                return

            guild.voice_client.play(
                source,
                after=lambda e, _session_id=session_id, _token=token, _idx=next_idx, _track=track:
                    asyncio.run_coroutine_threadsafe(
                        play_next(
                            guild, channel, loop,
                            current_track=_track,
                            current_idx=_idx,
                            error=e,
                            playback_token=_token,
                            player_session_id=_session_id,
                        ),
                        loop,
                    ),
            )

            # ลบ "เพิ่มใน Queue" ของเพลงนี้
            add_msg = queue_add_msgs.get(guild.id, {}).pop(next_idx, None)
            if add_msg:
                try: await add_msg.delete()
                except Exception: pass

            await _refresh_queue_msg(guild.id)

    # ── หมดคิวแล้ว (ไม่มีเพลงถัดไป) ──
    # ต้องทำ "นอก" queue_lock เสมอ เพราะมี await asyncio.sleep(300) ยาวมาก
    # ถ้าทำในนั้น lock จะถูกถือค้าง 5 นาที ทำให้ /play หรือปุ่มค้นหาเพลงใหม่
    # ขอเพลงไม่ได้เลยจนกว่าจะครบ 300 วิ หรือมีคน /stop (นี่คือ bug ตัวเดิมที่ทำให้ค้าง)
    if not has_next:
        glog(
            guild.id,
            guild.name,
            f"[QUEUE_END] Queue finished. current_idx={current_idx}, "
            f"next_idx={next_idx}, queue_len={len(get_full_queue(guild.id))}",
            level="info",
            console=True,
        )

        # ถ้าเป็น stop จริง ให้ callback นี้จบ
        if guild.id in guild_stopped:
            guild_stopped.discard(guild.id)
            return

        # ลบ Player เดิม
        old_view = active_views.pop(guild.id, None)
        if old_view and old_view.now_playing_msg:
            try:
                await old_view.now_playing_msg.delete()
            except Exception as e:
                glog(
                    guild.id,
                    guild.name,
                    f"[QUEUE_END] Delete player message failed: {e}",
                    level="error",
                    console=True,
                )
            finally:
                old_view.now_playing_msg = None

        # ล้างข้อความ Queue/Search
        try:
            await _delete_queue_view_msg(guild.id)
        except Exception as e:
            glog(
                guild.id,
                guild.name,
                f"[QUEUE_END] Delete queue view failed: {e}",
                level="error",
                console=True,
            )

        try:
            await _delete_queue_add_msgs(guild.id)
        except Exception as e:
            glog(
                guild.id,
                guild.name,
                f"[QUEUE_END] Delete queue add messages failed: {e}",
                level="error",
                console=True,
            )

        try:
            await _delete_search_result_msgs(guild.id)
        except Exception as e:
            glog(
                guild.id,
                guild.name,
                f"[QUEUE_END] Delete search results failed: {e}",
                level="error",
                console=True,
            )

        # แสดง Queue Done
        try:
            old_done = queue_done_msgs.pop(guild.id, None)
            if old_done:
                try:
                    await old_done.delete()
                except Exception:
                    pass

            done_msg_ref = [None]
            view = QueueDoneView(guild, channel, lambda: loop, done_msg_ref=done_msg_ref)
            done_embed = discord.Embed(
                description=(
                    "✅ เล่นเพลงครบ Queue แล้ว — "
                    "บอทจะออกใน 5 นาทีถ้าไม่มีเพลงใหม่"
                ),
                color=discord.Color.green(),
            )

            msg = await channel.send(embed=done_embed, view=view)
            done_msg_ref[0] = msg
            queue_done_msgs[guild.id] = msg

            glog(
                guild.id,
                guild.name,
                "[QUEUE_END] Queue Done message sent successfully.",
                level="info",
                console=True,
            )

        except Exception as e:
            glog(
                guild.id,
                guild.name,
                f"[QUEUE_END] FAILED TO SEND QUEUE DONE: {e}",
                level="error",
                console=True,
            )
            return

        # รอ 5 นาที แล้วตรวจว่ามีเพลงใหม่เข้ามาหรือยัง
        session_id_at_queue_end = _get_player_session(guild.id)
        await asyncio.sleep(300)

        # A new Player/Stop may have replaced or invalidated this session
        # while the 5-minute idle timer was sleeping.
        if session_id_at_queue_end != _get_player_session(guild.id):
            return

        q_after_wait = get_full_queue(guild.id)
        vc = guild.voice_client

        if (
            vc
            and not vc.is_playing()
            and not vc.is_paused()
            and next_idx >= len(q_after_wait)
        ):
            try:
                await _delete_search_result_msgs(guild.id)
            except Exception:
                pass

            try:
                await _delete_queue_view_msg(guild.id)
            except Exception:
                pass

            try:
                await vc.disconnect()
            except Exception:
                pass

            clear_guild(guild.id)

            done_msg = queue_done_msgs.pop(guild.id, None)
            if done_msg:
                try:
                    await done_msg.delete()
                except Exception:
                    pass

            glog(
                guild.id,
                guild.name,
                "[QUEUE_END] Disconnected after 5 minutes.",
                level="info",
                console=True,
            )


# ─────────────────────────────────────────────
#  Slash Commands
# ─────────────────────────────────────────────

def register(tree: app_commands.CommandTree, loop_getter):

    @tree.command(name="play", description="เล่นเพลงหรือค้นหาเพลง รองรับ YouTube/Spotify/SoundCloud/Bandcamp และ Playlist")
    @app_commands.describe(query="ชื่อเพลงที่จะค้นหา หรือ URL เพลง/Playlist จาก YouTube, Spotify, SoundCloud, Bandcamp")
    async def slash_play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ กรุณาเข้า Voice Channel ก่อนนะ!", color=discord.Color.red()), ephemeral=True)
        log("▶️ /play", interaction, f"query: {_trunc(query, 50)}")

        # ลบข้อความเก่าของบอทใน channel นี้
        asyncio.create_task(_cleanup_channel(interaction.channel))

        done_msg = queue_done_msgs.pop(interaction.guild.id, None)
        if done_msg:
            try: await done_msg.delete()
            except Exception: pass

        searching_msg = None
        try:
            await interaction.response.send_message(embed=discord.Embed(
                description=f"🔍 กำลังค้นหา **{query}**", color=discord.Color.blurple()))
            searching_msg = await interaction.original_response()
        except Exception: pass

        async def _del_search():
            if searching_msg:
                try: await searching_msg.delete()
                except Exception: pass

        try:
            is_url = query.strip().startswith("http://") or query.strip().startswith("https://")

            # Radio/Mix: แสดงตัวเลือกทันที ไม่ต้องเชื่อมต่อ VC หรือเรียก yt-dlp ก่อน
            if is_url and is_youtube_radio_url(query):
                await _del_search()
                view = RadioChoiceView(
                    query,
                    interaction.guild,
                    interaction.channel,
                    loop_getter,
                    interaction.user,
                    loop_getter(),
                )
                prompt = await interaction.followup.send(
                    embed=discord.Embed(
                        title="📻 YouTube Radio / Mix",
                        description="ต้องการเล่นแบบไหน?",
                        color=0x1a1a2e,
                    ),
                    view=view,
                    ephemeral=True,
                    wait=True,
                )
                view.message = prompt
                return

            # OLAK ก็ต้องตรวจรายการก่อนเลือกจำนวนเพลง แต่ยังไม่เชื่อมต่อ VC จนกว่าจะเลือก
            # flow นี้ยังใช้ logic เดิมของ PlaylistCountView ด้านล่าง
            voice_channel = interaction.user.voice.channel
            vc = interaction.guild.voice_client
            if not vc:
                try:
                    vc = await _connect_with_retry(voice_channel)
                except Exception as connect_error:
                    log("▶️ /play CONNECT ERROR", interaction, str(connect_error))
                    await _del_search()
                    return await interaction.followup.send(embed=discord.Embed(
                        description="❌ เชื่อมต่อ Voice Channel ไม่สำเร็จ (เน็ตบอทไม่เสถียร) กรุณาลองใหม่อีกครั้ง",
                        color=discord.Color.red()), ephemeral=True)
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)

            # ตรวจ URL scheme ผิด เช่น ttps:// หรือ htp://
            looks_like_url = re.search(r'^[a-zA-Z]{2,10}://', query.strip())
            if looks_like_url and not is_url:
                await _del_search()
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"❌ URL ไม่ถูกต้อง (`{query.strip()[:40]}`)\n💡 ลองวาง URL ใหม่อีกครั้ง",
                    color=discord.Color.red()), ephemeral=True)
            

            # ตรวจสอบว่าเป็น playlist หรือไม่
            if is_url and is_playlist_url(query):
                # Handle Playlist
                try:
                    playlist_tracks = await asyncio.to_thread(fetch_playlist_tracks, query)
                    if not playlist_tracks:
                        await _del_search()
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ ไม่พบเพลงในเพลย์ลิสต์", color=discord.Color.red()), ephemeral=True)
                    
                    added_results = await _add_playlist_to_queue(
                        vc, interaction.guild, interaction.channel, loop_getter,
                        playlist_tracks, interaction.user)
                    
                    await _del_search()
                    if not added_results:
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ ไม่สามารถดึงเพลงจากเพลย์ลิสต์ได้เลย", color=discord.Color.red()), ephemeral=True)
                    return
                    
                except ValueError as e:
                    error_msg = str(e)
                    await _del_search()
                    
                    if error_msg == "SPOTIFY_DRM_ERROR":
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ Spotify Playlist ไม่สามารถเล่นได้ (DRM Protection)\n\n💡 วิธีแก้: ค้นหาเพลงด้วยชื่อแทน เช่น `/play รักเธอขอบคุณทุกช่วงเวลา`",
                            color=discord.Color.red()), ephemeral=True)
                    elif error_msg == "SPOTIFY_SCRAPE_ERROR":
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ ไม่สามารถดึงข้อมูลจาก Spotify ได้\n\n💡 ลองอีกครั้ง หรือค้นหาเพลงด้วยชื่อแทน",
                            color=discord.Color.red()), ephemeral=True)
                    else:
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ ไม่สามารถโหลดเพลย์ลิสต์", color=discord.Color.red()), ephemeral=True)
                    
                except Exception as e:
                    print(f"Playlist error: {str(e)}")
                    await _del_search()
                    return await interaction.followup.send(embed=discord.Embed(
                        description="❌ ไม่สามารถโหลดเพลย์ลิสต์", color=discord.Color.red()), ephemeral=True)

            # Handle Single Track URL or Spotify Track
            if is_url:
                try:
                    url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query)
                except ValueError as e:
                    error_msg = str(e)
                    await _del_search()
                    
                    if error_msg == "PLAYLIST_DETECTED":
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ นี่คือเพลย์ลิสต์ โปรแกรมจะเพิ่มเพลงทั้งหมดสำหรับคุณ",
                            color=discord.Color.red()), ephemeral=True)
                    elif error_msg == "SPOTIFY_SCRAPE_ERROR":
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ ไม่สามารถดึงข้อมูลจาก Spotify ได้\n\n💡 ลองอีกครั้ง หรือค้นหาด้วยชื่อเพลงแทน",
                            color=discord.Color.red()), ephemeral=True)
                    elif error_msg == "SPOTIFY_NO_YOUTUBE_MATCH":
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ ไม่พบเพลง Spotify บน YouTube\n\n💡 ลองค้นหาด้วยชื่อเพลงแทน",
                            color=discord.Color.red()), ephemeral=True)
                    elif error_msg == "SPOTIFY_UNSUPPORTED_LINK":
                        return await interaction.followup.send(embed=discord.Embed(
                            description="❌ ไม่รองรับลิงก์ Spotify นี้ (เช่น พอดแคสต์/Show)\n\n💡 ลองส่งลิงก์เพลงเดี่ยว/เพลย์ลิสต์/ศิลปิน หรือค้นหาด้วยชื่อเพลงแทน",
                            color=discord.Color.red()), ephemeral=True)
                    else:
                        raise
                
                track = (url, title, duration, interaction.user, thumbnail)
                await _add_and_play(vc, interaction.guild, interaction.channel, loop_getter, track)
                await _del_search()
                return
            
            # Search Mode
            results = await asyncio.to_thread(search_tracks, query)
            if not results:
                await _del_search()
                return await interaction.followup.send(embed=discord.Embed(
                    description="❌ ไม่พบเพลง", color=discord.Color.red()), ephemeral=True)
            await _del_search()
            await send_search_results(results, interaction.guild, interaction.channel,
                                      loop_getter(), loop_getter, interaction.user)

        except Exception as e:
            err = str(e)
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if "Sign in" in err or "cookies" in err.lower():
                print(
                    f"[{ts}] ⚠ /play — YouTube bot detection (ต้องการ cookies)\n"
                    f"  {'Query':<9}: {_trunc(query, 60)}\n"
                    f"  {'Hint':<9}: ใช้ --cookies-from-browser หรือ export cookies ให้ yt-dlp"
                )
                msg_text = "❌ YouTube บล็อกการเข้าถึง กรุณาลองใหม่อีกครั้ง"
            else:
                print(
                    f"[{ts}] ✗ /play\n"
                    f"  {'Query':<9}: {_trunc(query, 60)}\n"
                    f"  {'Error':<9}: {_trunc(err, 120)}"
                )
                msg_text = "❌ เกิดข้อผิดพลาด กรุณาลองใหม่"
            try:
                await _del_search()
                await interaction.followup.send(embed=discord.Embed(
                    description=msg_text, color=discord.Color.red()), ephemeral=True)
            except Exception: pass

    @tree.command(name="stop", description="หยุดเพลงและออกจาก Voice Channel")
    async def slash_stop(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ บอทไม่ได้อยู่ใน Voice Channel", color=discord.Color.red()), ephemeral=True)
        if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
            return await safe_respond(interaction, embed=discord.Embed(
                description=f"❌ คุณต้องอยู่ใน **{vc.channel.name}** ถึงจะใช้งานได้",
                color=discord.Color.red()), ephemeral=True)
        cur = active_views.get(interaction.guild.id)
        log("⏹ /stop", interaction,
            f"Track: {_trunc(cur.current_track[1]) if cur and cur.current_track else '?'}")

        # ลบข้อความเก่าของบอทใน channel นี้
        asyncio.create_task(_cleanup_channel(interaction.channel))

        # ตอบ interaction ทันที กัน Discord ฟ้อง "the application did not respond"
        try: await interaction.response.send_message("⏳", ephemeral=True, delete_after=0)
        except Exception: pass

        old_view = active_views.pop(interaction.guild.id, None)
        await asyncio.gather(
            _delete_queue_add_msgs(interaction.guild.id),
            _delete_search_result_msgs(interaction.guild.id),
            _delete_queue_view_msg(interaction.guild.id),
        )

        now_playing_msg = old_view.now_playing_msg if old_view else None
        if old_view:
            old_view.now_playing_msg = None

        async with get_navigation_lock(interaction.guild.id):
            guild_stopped.add(interaction.guild.id)
            clear_guild(interaction.guild.id)
            vc.stop()
            await vc.disconnect()

        old_done = queue_done_msgs.pop(interaction.guild.id, None)
        if old_done:
            try: await old_done.delete()
            except Exception: pass

        done_embed = make_done_embed()
        if now_playing_msg:
            try:
                await now_playing_msg.edit(embed=done_embed, view=None)
                queue_done_msgs[interaction.guild.id] = now_playing_msg
            except Exception:
                done_msg = await interaction.channel.send(embed=done_embed)
                queue_done_msgs[interaction.guild.id] = done_msg
        else:
            done_msg = await interaction.channel.send(embed=done_embed)
            queue_done_msgs[interaction.guild.id] = done_msg

    @tree.command(name="clear", description="ลบข้อความในช่อง")
    @app_commands.describe(amount="จำนวนข้อความที่ต้องการลบ (1-100) — ไม่ระบุ = ลบสูงสุด 100")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_clear(interaction: discord.Interaction, amount: int = 100):
        if not 1 <= amount <= 100:
            return await safe_respond(interaction, "❌ ใส่จำนวน 1-100 เท่านั้น", ephemeral=True)

        await interaction.response.send_message(embed=discord.Embed(
            description="🧹 กำลังลบข้อความ", color=0x1a1a2e), ephemeral=True)

        # กันไม่ให้ลบข้อความ Now Playing ที่กำลังทำงานอยู่ (มีปุ่มควบคุมเพลง)
        # ส่วนข้อความอื่นๆ เช่น "เพิ่มใน Queue", ผลการค้นหา, เล่นครบ Queue ฯลฯ ลบได้ตามปกติ
        active_view = active_views.get(interaction.guild.id)
        protected_msg_id = active_view.now_playing_msg.id if active_view and active_view.now_playing_msg else None

        cutoff = discord.utils.utcnow() - datetime.timedelta(days=14)
        fetched = [msg async for msg in interaction.channel.history(limit=amount)]
        messages = [m for m in fetched if m.id != protected_msg_id]
        skipped_now_playing = len(fetched) != len(messages)
        bulk = [m for m in messages if m.created_at > cutoff]
        old_msgs = [m for m in messages if m.created_at <= cutoff]
        deleted = 0

        if bulk:
            await interaction.channel.delete_messages(bulk)
            deleted += len(bulk)

        if old_msgs:
            try:
                await interaction.edit_original_response(content=None, embed=discord.Embed(
                    description=f"⏳ กำลังลบข้อความเก่า {len(old_msgs)} อัน (อาจใช้เวลาสักครู่)",
                    color=0x1a1a2e))
            except Exception: pass
            for msg in old_msgs:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(1.2)
                except (discord.NotFound, discord.HTTPException): pass

        note_parts = []
        if old_msgs:
            note_parts.append(f"รวมข้อความเก่า {len(old_msgs)} ข้อความ")
        if skipped_now_playing:
            note_parts.append("เว้นเครื่องเล่นเพลงที่กำลังทำงานอยู่")
        note = f" ({' · '.join(note_parts)})" if note_parts else ""
        result_embed = discord.Embed(
            description=f"🗑️ ลบข้อความไปแล้ว {deleted} ข้อความ{note}", color=0x1a1a2e)
        try:
            await interaction.edit_original_response(content=None, embed=result_embed)
        except Exception:
            status_msg = await interaction.channel.send(embed=result_embed)
            await asyncio.sleep(5)
            try: await status_msg.delete()
            except Exception: pass

    @slash_clear.error
    async def slash_clear_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await safe_respond(interaction, "❌ คุณไม่มีสิทธิ์ลบข้อความ", ephemeral=True)

    @tree.command(name="skip", description="ข้ามเพลงปัจจุบัน")
    async def slash_skip(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ บอทไม่ได้อยู่ใน Voice Channel", color=discord.Color.red()), ephemeral=True)
        if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
            return await safe_respond(interaction, embed=discord.Embed(
                description=f"❌ คุณต้องอยู่ใน **{vc.channel.name}** ถึงจะใช้งานได้",
                color=discord.Color.red()), ephemeral=True)
        
        # Check if there's a current track playing
        current_view = active_views.get(interaction.guild.id)
        if not current_view or not current_view.current_track:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
        
        # Get the current index
        idx = get_now_idx(interaction.guild.id)
        q = get_full_queue(interaction.guild.id)
        
        # Check if there's a next track to play
        if idx + 1 >= len(q):
            # No more tracks, stop playback
            log("⏭ SKIP", interaction, f"idx {idx} → end")
            try: await interaction.response.send_message("⏳ กำลังข้าม...", ephemeral=True)
            except Exception: pass
            vc.stop()
            return

        # Skip to next track using existing logic from PlayerView.skip()
        log("⏭ SKIP", interaction, f"idx {idx} → {idx+1}")
        try: await interaction.response.send_message("⏳ กำลังข้าม...", ephemeral=True)
        except Exception: pass
        await _do_play_at_idx(current_view, idx + 1)