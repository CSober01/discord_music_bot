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
import re
import json
import requests

logging.getLogger("discord.player").setLevel(logging.ERROR)
logging.getLogger("discord.voice_state").setLevel(logging.WARNING)

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
queue_view_msgs: dict[int, object] = {}
search_result_msgs: dict[int, list] = {}

# guild_volumes = ระดับเสียงที่ผู้ใช้ตั้งไว้ต่อ server (guild)
# จำไว้ตราบใดที่บอทยังอยู่ใน Voice Channel (ไม่ว่าเพลงจะเปลี่ยนกี่รอบ)
# จะถูกล้างกลับเป็นค่า default ทุกครั้งที่บอท disconnect ออกจาก VC (ดู clear_guild)
guild_volumes: dict[int, float] = {}

# guild_stopped  = หยุดจงใจ (⏹ stop / /stop) → play_next ต้องหยุด
# guild_changing = กำลัง skip/prev → play_next callback เก่าต้องข้ามไป
guild_stopped:  set[int] = set()
guild_changing: set[int] = set()

MAX_QUEUE = 20   # เก็บเพลงใน memory สูงสุด 20 อัน (ย้อนกลับได้สูงสุด 20 เพลง)
MAX_PLAYLIST_FETCH = 50  # ดึงเพลงจาก playlist สูงสุด 50 อัน


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
    """ตัดเพลงเก่าออกจากบนสุดของคิว ถ้าคิวยาวเกิน MAX_QUEUE
    ตัดได้มากสุดเท่าที่ไม่กระทบเพลงที่กำลังเล่นอยู่ (now_idx) — ถ้าตัดได้ไม่ครบ
    ที่เหลือจะถูกตัดในรอบหลัง (ตอนเพลงเปลี่ยน/now_idx ขยับสูงขึ้น) แทน
    เลขลำดับที่แสดงผล (display_no) ยังนับสะสมต่อเนื่องเสมอ ไม่รีเซ็ต
    """
    q = get_full_queue(guild_id)
    if len(q) <= MAX_QUEUE:
        return
    now_idx = get_now_idx(guild_id)
    excess = len(q) - MAX_QUEUE
    trim_count = min(excess, now_idx)  # กันไม่ให้ตัดเพลงที่กำลังเล่นทิ้ง
    if trim_count > 0:
        del q[:trim_count]
        set_now_idx(guild_id, now_idx - trim_count)
        queue_seq_offset[guild_id] = get_seq_offset(guild_id) + trim_count

        # ปรับ key ของ queue_add_msgs (ข้อความ "เพิ่มใน Queue #") ให้ตรงกับตำแหน่งใหม่
        # ไม่งั้นข้อความจะไม่ถูกลบตอนเพลงนั้นเริ่มเล่นจริง เพราะ key เดิมอ้างถึง index ที่ไม่มีอยู่แล้ว
        old_msgs = queue_add_msgs.get(guild_id, {})
        if old_msgs:
            shifted = {}
            for old_key, msg in old_msgs.items():
                new_key = old_key - trim_count
                if new_key >= 0:
                    shifted[new_key] = msg
                else:
                    # เพลงที่ key อ้างถึงถูกตัดออกไปแล้ว (ไม่ควรเกิดขึ้นได้จริง เพราะ
                    # ตัดได้แค่ไม่เกิน now_idx เท่านั้น แต่กันไว้เผื่อไว้)
                    pass
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
    full_queues[guild_id] = []
    now_playing_idx[guild_id] = 0
    queue_seq_offset[guild_id] = 0
    guild_total_added[guild_id] = 0
    guild_volumes.pop(guild_id, None)
    active_views.pop(guild_id, None)
    queue_view_msgs.pop(guild_id, None)
    search_result_msgs.pop(guild_id, None)

def _queue_pos_str(guild_id: int, idx: int) -> str:
    return f"กำลังเล่น #{display_no(guild_id, idx)} จาก {get_total_added(guild_id)} เพลง"

def get_ydl_options(include_playlist: bool = False) -> dict:
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
    }
    # ถ้า include_playlist เป็น True จะดึง playlist ทั้งหมด
    opts["noplaylist"] = not include_playlist
    return opts

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

def is_playlist_url(query: str) -> bool:
    """ตรวจสอบว่า URL มีหลายเพลง (playlist/album/artist) หรือไม่"""
    query_lower = query.lower()
    # YouTube Playlist
    if "youtube.com" in query_lower or "youtu.be" in query_lower:
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
                        
                        
                        return info["url"], info.get("title", "Unknown"), f"{minutes}:{seconds:02d}", info.get("thumbnail")
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
            
            
            return info["url"], info.get("title", "Unknown"), f"{minutes}:{seconds:02d}", info.get("thumbnail")
    except ValueError as e:
        if str(e) in ("PLAYLIST_DETECTED", "SPOTIFY_SCRAPE_ERROR", "SPOTIFY_NO_YOUTUBE_MATCH", "SPOTIFY_UNSUPPORTED_LINK"):
            raise
        print(f"Fetch track error: {str(e)}")
        raise
    except Exception as e:
        print(f"Fetch track error: {str(e)}")
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
    requester_str = f"ขอโดย: {requester.mention}" if requester else ""
    footer_parts = ["SEa Music  •  ใช้ปุ่มด้านล่างเพื่อควบคุม"]
    if queue_pos:
        footer_parts.append(queue_pos)
    embed = discord.Embed(
        description=f"### 🎵  {title}\n⏱ `{duration}`　{requester_str}",
        color=0x1a1a2e,
    )
    embed.set_author(name="▶  Now Playing")
    embed.set_footer(text="  •  ".join(footer_parts))
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed

def make_done_embed():
    return discord.Embed(
        description="⏹ หยุดเพลงและออกจาก Voice Channel แล้ว",
        color=discord.Color.green()
    )

_QUEUE_TITLE_NORMAL  = 60
_QUEUE_TITLE_PLAYING = 48

def make_queue_embed(guild_id: int, current_idx: int = None):
    q = get_full_queue(guild_id)
    idx = current_idx if current_idx is not None else get_now_idx(guild_id)
    if not q:
        return discord.Embed(description="📋 Queue ว่างเปล่า", color=discord.Color.blurple())
    lines = []
    for i, t in enumerate(q):
        no = display_no(guild_id, i)
        if i == idx:
            t_cut = t[1][:_QUEUE_TITLE_PLAYING - 1] + "…" if len(t[1]) > _QUEUE_TITLE_PLAYING else t[1]
            lines.append(f"**▶ {no}. {t_cut} ◀ กำลังเล่น**")
        else:
            t_cut = t[1][:_QUEUE_TITLE_NORMAL - 1] + "…" if len(t[1]) > _QUEUE_TITLE_NORMAL else t[1]
            lines.append(f"`{no}.` {t_cut}")
    embed = discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=0x5865F2)
    embed.set_footer(text=f"กำลังเล่น #{display_no(guild_id, idx)} จาก {get_total_added(guild_id)} เพลง")
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
    print("\n".join(lines))

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
    wmsg = queue_view_msgs.get(guild_id)
    if not wmsg:
        return
    try:
        await wmsg.edit(embed=make_queue_embed(guild_id))
    except Exception:
        queue_view_msgs.pop(guild_id, None)

async def _delete_queue_view_msg(guild_id: int):
    wmsg = queue_view_msgs.pop(guild_id, None)
    if wmsg:
        try: await wmsg.delete()
        except Exception: pass

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
    all_msgs.extend(queue_view_msgs.values())
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

    @discord.ui.button(emoji="🔍", label="ค้นหาเพลง", style=discord.ButtonStyle.primary)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        loop = self.loop_getter()
        modal = SearchModal(self.guild, self.channel, loop, self.loop_getter,
                            done_msg_ref=self.done_msg_ref)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="⏹", label="หยุดและออก", style=discord.ButtonStyle.danger)
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
    vol_input = discord.ui.TextInput(
        label="ระดับเสียง (0-100)",
        placeholder="ค่าเริ่มต้น: 10",
        min_length=1, max_length=3)

    def __init__(self, vc, player_view):
        super().__init__()
        self.vc = vc
        self.player_view = player_view

    async def on_submit(self, interaction: discord.Interaction):
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
        await interaction.response.send_message(f"🔊 ระดับเสียง: **{vol}%**", ephemeral=True)


# ─────────────────────────────────────────────
#  Search Modal
# ─────────────────────────────────────────────

class SearchModal(discord.ui.Modal, title="🔍 ค้นหาเพลง"):
    query = discord.ui.TextInput(
        label="ค้นหาเพลง หรือวาง URL YouTube",
        placeholder="ระบุชื่อเพลง หรือวาง URL YouTube ที่นี่",
        min_length=1, 
        max_length=100)

    def __init__(self, guild, channel, loop, loop_getter, done_msg_ref: list = None):
        super().__init__()
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
            vc = self.guild.voice_client
            if not vc:
                if not interaction.user.voice:
                    await _ack_done()
                    await _send_error("❌ กรุณาเข้า Voice Channel ก่อน")
                    return
                vc = await interaction.user.voice.channel.connect()

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
                    
                    added_titles = [(display_no(self.guild.id, idx), title, idx)
                                    for idx, title in added_results]
                    await _send_playlist_added_summary(self.guild.id, self.channel, interaction.user, added_titles)
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

        except Exception:
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
        self._select_item = discord.ui.Select(placeholder="เลือกเพลง", options=self._build_options())
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

    @discord.ui.button(emoji="➕", label="เพิ่มทั้งหมด", style=discord.ButtonStyle.primary, row=1)
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
            vc = await interaction.user.voice.channel.connect()

        await self._clear_done()
        # Serial loop: await ทีละอัน เพื่อป้องกัน race condition บน index
        for i, r in enumerate(self.results):
            if i in self._selected:
                continue  # ข้ามเพลงที่เลือกแล้ว
            try:
                url, title, duration, thumbnail = await asyncio.to_thread(fetch_track_from_result, r)
                track = (url, title, duration, interaction.user, thumbnail)
                await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track)
            except Exception: pass

        await self._close_message()

    @discord.ui.button(emoji="✖", label="ปิด", style=discord.ButtonStyle.danger, row=1)
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
            vc = await interaction.user.voice.channel.connect()

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




async def _add_playlist_to_queue(vc, guild, channel, loop_getter, playlist_tracks, requester):
    """เพิ่ม playlist ทั้งหมดเข้า queue แบบ atomic:
    1. prefetch ทุกเพลงพร้อมกัน (concurrent) — เร็ว
    2. เรียงผลตามลำดับ playlist จริง
    3. เช็ค guild_stopped — ถ้า stop ระหว่าง fetch ให้ยกเลิกทันที
    4. add ทั้งหมดเข้า queue ด้วย queue_lock ครั้งเดียว — รับประกันลำดับ ไม่มีใครแทรกกลาง
    คืนค่า list of (track_idx, title)
    """
    # ── step 1: prefetch ทุกเพลงพร้อมกัน ──
    async def _fetch_one(track_info):
        try:
            if "url" in track_info:
                return await asyncio.to_thread(fetch_track, track_info["url"])
            elif "id" in track_info and track_info.get("id"):
                yt_url = f"https://www.youtube.com/watch?v={track_info['id']}"
                return await asyncio.to_thread(fetch_track, yt_url)
            elif "artist" in track_info:
                search_query = f"{track_info['title']} {track_info['artist']}"
                print(f"  {'🎵 Spotify→YT':<13}: {_trunc(track_info['title'], 40)} — {_trunc(track_info['artist'], 30)}")
                return await asyncio.to_thread(_fetch_spotify_track_from_search, search_query)
            else:
                return None
        except Exception as e:
            print(f"Error fetching track from playlist: {str(e)}")
            return None

    fetch_results = await asyncio.gather(*(_fetch_one(t) for t in playlist_tracks))

    # ── step 2: เช็ค stop ก่อน add — ถ้าผู้ใช้กด stop ระหว่าง fetch ให้ยกเลิกทันที ──
    if guild.id in guild_stopped:
        print(f"  🛑 Playlist fetch ยกเลิก — guild {guild.id} ถูก stop ระหว่าง fetch")
        return []

    # ── step 3: filter None (fetch ล้มเหลว) แต่รักษาลำดับ ──
    fetched = [(url, title, duration, thumbnail)
               for r in fetch_results if r is not None
               for url, title, duration, thumbnail in [r]]

    if not fetched:
        return []

    # ── step 3: add ทั้งหมดด้วย queue_lock ครั้งเดียว ──
    added = []
    async with get_queue_lock(guild.id):
        first_was_empty = not (vc.is_playing() or vc.is_paused())

        for url, title, duration, thumbnail in fetched:
            track = (url, title, duration, requester, thumbnail)
            track_idx = add_to_queue(guild.id, track)
            added.append((track_idx, title, track, url, duration, thumbnail))

        if first_was_empty and added:
            # เล่นเพลงแรก
            track_idx, title, track, url, duration, thumbnail = added[0]
            set_now_idx(guild.id, track_idx)
            _trim_queue(guild.id)
            track_idx = get_now_idx(guild.id)
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=get_guild_volume(guild.id))
            loop = loop_getter()
            view = PlayerView(guild, channel, loop, current_track=track, current_idx=track_idx, loop_getter=loop_getter)
            active_views[guild.id] = view
            vc.play(source, after=lambda e, _t=track, _ti=track_idx:
                    asyncio.run_coroutine_threadsafe(
                        play_next(guild, channel, loop, current_track=_t, current_idx=_ti, error=e), loop))
            embed = make_now_playing_embed(title, duration, requester, thumbnail,
                                           _queue_pos_str(guild.id, track_idx))
            msg = await channel.send(embed=embed, view=view)
            view.now_playing_msg = msg
        else:
            # อัปเดต footer now playing
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

    return [(idx, t) for idx, t, *_ in added]

async def _add_and_play(vc, guild, channel, loop_getter, track):
    """เพิ่มเพลงเข้า queue และเล่นถ้าว่าง
    คืนค่า (track_idx, title) — track_idx คือลำดับจริงในคิว (0-based)
    """
    async with get_queue_lock(guild.id):
        track_idx = add_to_queue(guild.id, track)
        url, title, duration, requester, thumbnail, *_rest = track

        if vc.is_playing() or vc.is_paused():
            pos = display_no(guild.id, track_idx)
            short_title = title if len(title) <= 50 else title[:47] + "…"
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
            view = PlayerView(guild, channel, loop, current_track=track, current_idx=track_idx, loop_getter=loop_getter)
            active_views[guild.id] = view
            _g, _ch, _lp, _t, _ti = guild, channel, loop, track, track_idx
            vc.play(source, after=lambda e, g=_g, ch=_ch, lp=_lp, t=_t, ti=_ti:
                    asyncio.run_coroutine_threadsafe(
                        play_next(g, ch, lp, current_track=t, current_idx=ti, error=e), lp))
            embed = make_now_playing_embed(title, duration, requester, thumbnail,
                                           _queue_pos_str(guild.id, track_idx))
            msg = await channel.send(embed=embed, view=view)
            view.now_playing_msg = msg

        return track_idx, title


async def _send_playlist_added_summary(guild_id: int, channel, requester, titles: list):
    """ส่งสรุปเพลงที่เพิ่มจาก playlist เป็นข้อความเดียว ให้ทุกคนเห็น (ไม่ ephemeral)
    titles: list of (display_no, title, list_idx) — display_no คือเลขลำดับสะสมที่จะแสดงผล,
            list_idx คือตำแหน่งจริงในคิว (0-based) ของเพลงนั้น
    เก็บ reference ไว้ใน queue_add_msgs โดยใช้ list_idx ของ "เพลงสุดท้าย" ในสรุปเป็นคีย์
    """
    if not titles:
        return
    lines = [f"`#{no}` {_trunc(t, 58)}" for no, t, _ in titles]
    embed = discord.Embed(
        description="\n".join(lines) + f"\n\nขอโดย: {requester.mention}",
        color=0x1a1a2e,
    )
    embed.set_author(name=f"📋  เพิ่มเข้า Queue แล้ว {len(titles)} เพลง")
    try:
        pub_msg = await channel.send(embed=embed)
        last_idx = titles[-1][2]
        queue_add_msgs.setdefault(guild_id, {})[last_idx] = pub_msg
    except Exception:
        pass


# ─────────────────────────────────────────────
#  _do_play_at_idx — core ของการ skip/prev
#  เรียกหลัง interaction ถูก defer แล้วเท่านั้น
# ─────────────────────────────────────────────

async def _do_play_at_idx(view: "PlayerView", idx: int):
    """
    เล่นเพลงที่ idx โดยไม่สร้าง message ใหม่ — edit embed เดิม
    ต้องเรียกหลัง interaction.response.defer() หรือ edit_message แล้ว
    """
    # อัปเดต now_idx แล้วลอง trim ก่อนดึง track ออกมา กัน index เพี้ยนหลัง trim
    set_now_idx(view.guild.id, idx)
    _trim_queue(view.guild.id)
    idx = get_now_idx(view.guild.id)

    q = get_full_queue(view.guild.id)
    track = q[idx]
    url, title, duration, requester, thumbnail, *_rest = track
    vc = view.guild.voice_client

    view.current_track = track
    view.current_idx = idx
    active_views[view.guild.id] = view

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=view.volume_level)

    # guild_changing กัน play_next callback เก่า (ที่ยิงมาจาก vc.stop())
    guild_changing.add(view.guild.id)
    vc.stop()
    # ไม่ discard ที่นี่ — play_next จะ discard เอง

    vc.play(source, after=lambda e, _idx=idx, _track=track:
            asyncio.run_coroutine_threadsafe(
                play_next(view.guild, view.channel, view.loop, current_track=_track, current_idx=_idx, error=e), view.loop))

    embed = make_now_playing_embed(title, duration, requester, thumbnail,
                                   _queue_pos_str(view.guild.id, idx))

    # ลบ "เพิ่มใน Queue" ของเพลงที่เริ่มเล่น
    add_msg = queue_add_msgs.get(view.guild.id, {}).pop(idx, None)
    if add_msg:
        try: await add_msg.delete()
        except Exception: pass

    # Edit embed ของ now_playing_msg เดิม
    if view.now_playing_msg:
        try:
            await view.now_playing_msg.edit(embed=embed, view=view)
        except Exception:
            view.now_playing_msg = None
            msg = await view.channel.send(embed=embed, view=view)
            view.now_playing_msg = msg
    else:
        msg = await view.channel.send(embed=embed, view=view)
        view.now_playing_msg = msg

    await _refresh_queue_msg(view.guild.id)


# ─────────────────────────────────────────────
#  Player View
# ─────────────────────────────────────────────

class PlayerView(discord.ui.View):
    def __init__(self, guild, channel, loop, current_track=None, current_idx=None, loop_getter=None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter or (lambda: loop)
        self.current_track = current_track
        self.current_idx = current_idx if current_idx is not None else get_now_idx(guild.id)
        self.now_playing_msg: discord.Message | None = None
        self.volume_level: float = get_guild_volume(guild.id)

    async def delete_now_playing(self):
        if self.now_playing_msg:
            try: await self.now_playing_msg.delete()
            except Exception: pass
            self.now_playing_msg = None

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        idx = get_now_idx(self.guild.id)
        if idx <= 0:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงก่อนหน้าแล้ว", color=discord.Color.red()), ephemeral=True)
        log("⏮ PREV", interaction, f"idx {idx} → {idx-1}")
        try: await interaction.response.defer()
        except Exception: pass
        await _do_play_at_idx(self, idx - 1)

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        vc = self.guild.voice_client
        title = _trunc(self.current_track[1]) if self.current_track else "?"
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            log("⏸ PAUSE", interaction, f"เพลง: {title}")
            try: await interaction.response.edit_message(view=self)
            except Exception: pass
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸"
            log("▶️ RESUME", interaction, f"เพลง: {title}")
            try: await interaction.response.edit_message(view=self)
            except Exception: pass
        else:
            await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        vc = self.guild.voice_client
        if not (vc.is_playing() or vc.is_paused()):
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
        idx = get_now_idx(self.guild.id)
        q = get_full_queue(self.guild.id)
        if idx + 1 >= len(q):
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงถัดไปใน Queue", color=discord.Color.red()), ephemeral=True)
        log("⏭ SKIP", interaction, f"idx {idx} → {idx+1}")
        try: await interaction.response.defer()
        except Exception: pass
        await _do_play_at_idx(self, idx + 1)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        vc = self.guild.voice_client
        log("⏹ STOP", interaction, f"เพลง: {_trunc(self.current_track[1]) if self.current_track else '?'}")

        # ตอบ interaction ทันที ก่อนงานลบ/disconnect ที่ใช้เวลา กัน Discord ฟ้อง "ปุ่มไม่ตอบสนอง"
        try: await interaction.response.defer()
        except Exception: pass

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

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        log("🔍 SEARCH", interaction)
        loop = self.loop_getter()
        modal = SearchModal(self.guild, self.channel, loop, self.loop_getter)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.primary, row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        log("📋 QUEUE", interaction)
        # defer ก่อนทุกอย่าง กัน interaction token หมดอายุ (3 วินาที)
        try:
            await interaction.response.defer(ephemeral=True, thinking=False)
        except Exception:
            return  # token หมดอายุไปแล้ว ทำอะไรต่อไม่ได้

        embed = make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id))
        old_wmsg = queue_view_msgs.pop(self.guild.id, None)
        if old_wmsg:
            try: await old_wmsg.delete()
            except Exception: pass

        wmsg = await interaction.followup.send(embed=embed, ephemeral=True, wait=True)
        queue_view_msgs[self.guild.id] = wmsg

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        vc = self.guild.voice_client
        if not vc.source:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
        log("🔊 VOLUME", interaction)
        modal = VolumeModal(vc, self)
        await interaction.response.send_modal(modal)


# ─────────────────────────────────────────────
#  play_next — เรียกเมื่อเพลงจบตามธรรมชาติ
# ─────────────────────────────────────────────

async def play_next(guild: discord.Guild, channel: discord.TextChannel, loop,
                    current_track=None, current_idx: int = None, error=None):
    # กำลัง skip/prev → callback เก่านี้ต้องข้ามไป
    if guild.id in guild_changing:
        guild_changing.discard(guild.id)
        return

    # หยุดจงใจ (stop)
    if guild.id in guild_stopped:
        guild_stopped.discard(guild.id)
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
        print(
            f"[{ts}] ✗ PLAYBACK_ERROR\n"
            f"  {'Track':<9}: {_trunc(failed_title or '?', 60)}\n"
            f"  {'Error':<9}: {_trunc(str(error), 120)}"
        )
        try:
            await channel.send(embed=discord.Embed(
                description=f"❌ เล่น **{_trunc(failed_title or 'เพลงนี้', 60)}** ไม่ได้ กำลังข้ามไปเพลงถัดไป",
                color=discord.Color.red()))
        except Exception:
            pass

    if current_idx is None:
        current_idx = get_now_idx(guild.id)

    next_idx = current_idx + 1
    
    # Acquire lock ก่อนจะแก้ index ป้องกัน race condition กับ skip/prev
    async with get_queue_lock(guild.id):
        q = get_full_queue(guild.id)

        if next_idx < len(q):
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

            guild.voice_client.play(source, after=lambda e, _idx=next_idx, _track=track:
                asyncio.run_coroutine_threadsafe(
                    play_next(guild, channel, loop, current_track=_track, current_idx=_idx, error=e), loop))

            # ลบ "เพิ่มใน Queue" ของเพลงนี้
            add_msg = queue_add_msgs.get(guild.id, {}).pop(next_idx, None)
            if add_msg:
                try: await add_msg.delete()
                except Exception: pass

            await _refresh_queue_msg(guild.id)

        else:
            if guild.id in guild_stopped:
                guild_stopped.discard(guild.id)
                return

            old_view = active_views.pop(guild.id, None)
            if old_view and old_view.now_playing_msg:
                try: await old_view.now_playing_msg.delete()
                except Exception: pass
                old_view.now_playing_msg = None

            await _delete_queue_view_msg(guild.id)
            await _delete_queue_add_msgs(guild.id)
            await _delete_search_result_msgs(guild.id)

            await asyncio.sleep(1)
            vc = guild.voice_client
            if not vc or not vc.is_playing():
                old_done = queue_done_msgs.pop(guild.id, None)
                if old_done:
                    try: await old_done.delete()
                    except Exception: pass

                done_msg_ref = [None]
                view = QueueDoneView(guild, channel, lambda: loop, done_msg_ref=done_msg_ref)
                msg = await channel.send(embed=discord.Embed(
                    description="✅ เล่นเพลงครบ Queue แล้ว — บอทจะออกใน 5 นาทีถ้าไม่มีเพลงใหม่",
                    color=discord.Color.green()), view=view)
                done_msg_ref[0] = msg
                queue_done_msgs[guild.id] = msg

                await asyncio.sleep(300)
                vc = guild.voice_client
                if vc and not vc.is_playing() and not vc.is_paused() \
                        and next_idx >= len(get_full_queue(guild.id)):
                    await _delete_search_result_msgs(guild.id)
                    await _delete_queue_view_msg(guild.id)
                    await vc.disconnect()
                    clear_guild(guild.id)
                    done_msg = queue_done_msgs.pop(guild.id, None)
                    if done_msg:
                        try: await done_msg.delete()
                        except Exception: pass


# ─────────────────────────────────────────────
#  Slash Commands
# ─────────────────────────────────────────────

def register(tree: app_commands.CommandTree, loop_getter):

    @tree.command(name="play", description="เล่นเพลง YouTube/Spotify หรือ Playlist")
    @app_commands.describe(query="ชื่อเพลง URL YouTube/Spotify/Playlist Link")
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
            voice_channel = interaction.user.voice.channel
            vc = interaction.guild.voice_client
            if not vc:
                vc = await voice_channel.connect()
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)

            is_url = query.strip().startswith("http://") or query.strip().startswith("https://")

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
                    added_titles = [(display_no(interaction.guild.id, idx), title, idx)
                                    for idx, title in added_results]
                    await _send_playlist_added_summary(interaction.guild.id, interaction.channel, interaction.user, added_titles)
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
            f"เพลง: {_trunc(cur.current_track[1]) if cur and cur.current_track else '?'}")

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
    @app_commands.describe(amount="จำนวนข้อความที่ต้องการลบ (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_clear(interaction: discord.Interaction, amount: int):
        if not 1 <= amount <= 100:
            return await safe_respond(interaction, "❌ ใส่จำนวน 1-100 เท่านั้น", ephemeral=True)

        await interaction.response.send_message(embed=discord.Embed(
            description="🧹 กำลังลบข้อความ", color=0x1a1a2e), ephemeral=True)

        cutoff = discord.utils.utcnow() - datetime.timedelta(days=14)
        messages = [msg async for msg in interaction.channel.history(limit=amount)]
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

        note = f" (รวมข้อความเก่า {len(old_msgs)} ข้อความ)" if old_msgs else ""
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