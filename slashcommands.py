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

logging.getLogger("discord.player").setLevel(logging.ERROR)
logging.getLogger("discord.voice_state").setLevel(logging.WARNING)

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

DEFAULT_VOLUME = 0.10  # 10%

full_queues: dict[int, list] = {}
now_playing_idx: dict[int, int] = {}

active_views: dict[int, "PlayerView"] = {}
queue_done_msgs: dict[int, object] = {}
queue_add_msgs: dict[int, dict[int, object]] = {}
queue_view_msgs: dict[int, object] = {}
search_result_msgs: dict[int, list] = {}

# guild_stopped  = หยุดจงใจ (⏹ stop / /stop) → play_next ต้องหยุด
# guild_changing = กำลัง skip/prev → play_next callback เก่าต้องข้ามไป
guild_stopped:  set[int] = set()
guild_changing: set[int] = set()

MAX_QUEUE = 20


def get_full_queue(guild_id: int) -> list:
    if guild_id not in full_queues:
        full_queues[guild_id] = []
    return full_queues[guild_id]

def get_now_idx(guild_id: int) -> int:
    return now_playing_idx.get(guild_id, 0)

def set_now_idx(guild_id: int, idx: int):
    now_playing_idx[guild_id] = idx

def add_to_queue(guild_id: int, track) -> int:
    q = get_full_queue(guild_id)
    if len(q) >= MAX_QUEUE:
        idx = get_now_idx(guild_id)
        if idx > 0:
            del q[:idx]
            set_now_idx(guild_id, 0)
    q.append(track)
    return len(q) - 1

def clear_guild(guild_id: int):
    full_queues[guild_id] = []
    now_playing_idx[guild_id] = 0
    active_views.pop(guild_id, None)
    queue_view_msgs.pop(guild_id, None)
    search_result_msgs.pop(guild_id, None)

def _queue_pos_str(guild_id: int, idx: int) -> str:
    q = get_full_queue(guild_id)
    return f"กำลังเล่น #{idx + 1} จาก {len(q)} เพลง"

def get_ydl_options():
    return {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
    }

def fetch_track(query: str):
    with yt_dlp.YoutubeDL(get_ydl_options()) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        duration = info.get("duration", 0)
        minutes, seconds = divmod(int(duration), 60)
        return info["url"], info.get("title", "Unknown"), f"{minutes}:{seconds:02d}", info.get("thumbnail")

def search_tracks(query: str, limit: int = 5):
    opts = get_ydl_options()
    opts["extract_flat"] = False
    opts["default_search"] = "ytsearch5"
    results = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        entries = info.get("entries", [info]) if "entries" in info else [info]
        for entry in entries[:limit]:
            duration = entry.get("duration", 0) or 0
            m, s = divmod(int(duration), 60)
            results.append({
                "url": entry.get("url") or entry.get("webpage_url"),
                "title": entry.get("title", "Unknown"),
                "duration": f"{m}:{s:02d}",
                "thumbnail": entry.get("thumbnail"),
            })
    return results

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

_QUEUE_TITLE_NORMAL  = 40
_QUEUE_TITLE_PLAYING = 28

def make_queue_embed(guild_id: int, current_idx: int = None):
    q = get_full_queue(guild_id)
    idx = current_idx if current_idx is not None else get_now_idx(guild_id)
    if not q:
        return discord.Embed(description="📋 Queue ว่างเปล่า", color=discord.Color.blurple())
    lines = []
    for i, t in enumerate(q):
        if i == idx:
            t_cut = t[1][:_QUEUE_TITLE_PLAYING - 1] + "…" if len(t[1]) > _QUEUE_TITLE_PLAYING else t[1]
            lines.append(f"**▶ {i+1}. {t_cut} ◀ กำลังเล่น**")
        else:
            t_cut = t[1][:_QUEUE_TITLE_NORMAL - 1] + "…" if len(t[1]) > _QUEUE_TITLE_NORMAL else t[1]
            lines.append(f"`{i+1}.` {t_cut}")
    embed = discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=0x5865F2)
    embed.set_footer(text=f"กำลังเล่น #{idx + 1} จาก {len(q)} เพลง")
    return embed

MAX_TITLE_LOG = 40

def _trunc(text: str, n: int = MAX_TITLE_LOG) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"

def log(action: str, interaction: discord.Interaction, extra: str = ""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {action:<12} | {_trunc(interaction.guild.name,20)} ({interaction.guild.id}) | "
          f"#{_trunc(interaction.channel.name,16)} | {_trunc(interaction.user.display_name,20)} ({interaction.user.id})"
          + (f" | {extra}" if extra else ""))

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
    for m in search_result_msgs.pop(guild_id, []):
        try: await m.delete()
        except Exception: pass

async def _delete_queue_add_msgs(guild_id: int):
    for msg in queue_add_msgs.pop(guild_id, {}).values():
        try: await msg.delete()
        except Exception: pass


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
        vc = self.guild.voice_client
        if vc:
            await _delete_search_result_msgs(self.guild.id)
            await _delete_queue_view_msg(self.guild.id)
            await _delete_queue_add_msgs(self.guild.id)
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
        try: await interaction.response.defer()
        except Exception: pass


# ─────────────────────────────────────────────
#  Volume Modal
# ─────────────────────────────────────────────

class VolumeModal(discord.ui.Modal, title="🔊 ปรับระดับเสียง"):
    vol_input = discord.ui.TextInput(
        label="ระดับเสียง (0-100)", placeholder="ค่าเริ่มต้น: 10",
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
        self.player_view.volume_level = vol / 100
        if self.vc.source:
            self.vc.source.volume = vol / 100
        await interaction.response.send_message(f"🔊 ระดับเสียง: **{vol}%**", ephemeral=True)


# ─────────────────────────────────────────────
#  Search Modal
# ─────────────────────────────────────────────

class SearchModal(discord.ui.Modal, title="🔍 ค้นหาเพลง"):
    query = discord.ui.TextInput(
        label="ค้นหาเพลง หรือวาง URL YouTube",
        placeholder="ระบุชื่อเพลง หรือวาง URL YouTube ที่นี่",
        min_length=1, max_length=100)

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

            if query_str.startswith("http://") or query_str.startswith("https://"):
                url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query_str)
                await self._delete_done_msg()
                track = (url, title, duration, interaction.user, thumbnail)
                await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track)
                await _ack_done()
                return

            results = await asyncio.to_thread(search_tracks, query_str)
            if not results:
                await _ack_done()
                await _send_error("❌ ไม่พบเพลง")
                return

            await _ack_done()
            await self._delete_done_msg()

            lines = [f"`{i+1}.` {r['title']} `{r['duration']}`" for i, r in enumerate(results)]
            embed = discord.Embed(title="🔍 ผลการค้นหา", description="\n".join(lines), color=0x1a1a2e)
            embed.set_footer(text=f"กำลังรอ {interaction.user.display_name} เลือกเพลง • หมดเวลาใน 2 นาที")
            search_view = SearchResultView(results, self.guild, self.channel, self.loop,
                                           self.loop_getter, requester=interaction.user,
                                           done_msg_ref=self.done_msg_ref)
            pub_msg = await self.channel.send(
                content=f"🎵 {interaction.user.mention} กำลังเลือกเพลง", embed=embed, view=search_view)
            search_view.message = pub_msg
            search_result_msgs.setdefault(self.guild.id, []).append(pub_msg)

        except Exception:
            await _ack_done()
            await _send_error("❌ เกิดข้อผิดพลาด กรุณาลองใหม่")


# ─────────────────────────────────────────────
#  Search Result View
# ─────────────────────────────────────────────

class SearchResultView(discord.ui.View):
    def __init__(self, results, guild, channel, loop, loop_getter,
                 requester=None, done_msg_ref=None):
        super().__init__(timeout=120)
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
        return [discord.SelectOption(label=r["title"][:100], value=str(i),
                                     description=f"⏱ {r['duration']}")
                for i, r in enumerate(self.results) if i not in self._selected]

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
        for r in self.results:
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


# ─────────────────────────────────────────────
#  Helper: เพิ่มเพลงและเล่น/queue
# ─────────────────────────────────────────────

def fetch_track_from_result(r: dict):
    """ดึงข้อมูลจาก search result dict โดยตรง ไม่ re-fetch"""
    return r["url"], r["title"], r["duration"], r.get("thumbnail")

_play_locks: dict[int, asyncio.Lock] = {}

def get_play_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _play_locks:
        _play_locks[guild_id] = asyncio.Lock()
    return _play_locks[guild_id]


async def _add_and_play(vc, guild, channel, loop_getter, track):
    async with get_play_lock(guild.id):
        track_idx = add_to_queue(guild.id, track)
        url, title, duration, requester, *_thumb = track
        thumbnail = _thumb[0] if _thumb else None

        if vc.is_playing() or vc.is_paused():
            pos = track_idx + 1
            short_title = title if len(title) <= 50 else title[:47] + "…"
            pub_msg = await channel.send(embed=discord.Embed(
                description=f"📋 เพิ่มใน Queue **#{pos}**\n🎵 {short_title}  |  ขอโดย: {requester.mention}",
                color=0x1a1a2e))
            queue_add_msgs.setdefault(guild.id, {})[track_idx] = pub_msg
            await _refresh_queue_msg(guild.id)
            # อัปเดต footer ของ now playing embed
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
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
            loop = loop_getter()
            view = PlayerView(guild, channel, loop, current_track=track, current_idx=track_idx)
            active_views[guild.id] = view
            _g, _ch, _lp, _t, _ti = guild, channel, loop, track, track_idx
            vc.play(source, after=lambda e, g=_g, ch=_ch, lp=_lp, t=_t, ti=_ti:
                    asyncio.run_coroutine_threadsafe(
                        play_next(g, ch, lp, current_idx=ti), lp))
            embed = make_now_playing_embed(title, duration, requester, thumbnail,
                                           _queue_pos_str(guild.id, track_idx))
            msg = await channel.send(embed=embed, view=view)
            view.now_playing_msg = msg


# ─────────────────────────────────────────────
#  _do_play_at_idx — core ของการ skip/prev
#  เรียกหลัง interaction ถูก defer แล้วเท่านั้น
# ─────────────────────────────────────────────

async def _do_play_at_idx(view: "PlayerView", idx: int):
    """
    เล่นเพลงที่ idx โดยไม่สร้าง message ใหม่ — edit embed เดิม
    ต้องเรียกหลัง interaction.response.defer() หรือ edit_message แล้ว
    """
    q = get_full_queue(view.guild.id)
    track = q[idx]
    url, title, duration, requester, *_thumb = track
    thumbnail = _thumb[0] if _thumb else None
    vc = view.guild.voice_client

    # อัปเดต state ก่อนทุกอย่าง
    set_now_idx(view.guild.id, idx)
    view.current_track = track
    view.current_idx = idx
    active_views[view.guild.id] = view

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=view.volume_level)

    # guild_changing กัน play_next callback เก่า (ที่ยิงมาจาก vc.stop())
    guild_changing.add(view.guild.id)
    vc.stop()
    # ไม่ discard ที่นี่ — play_next จะ discard เอง

    vc.play(source, after=lambda e, _idx=idx:
            asyncio.run_coroutine_threadsafe(
                play_next(view.guild, view.channel, view.loop, current_idx=_idx), view.loop))

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
    def __init__(self, guild, channel, loop, current_track=None, current_idx=None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.current_track = current_track
        self.current_idx = current_idx if current_idx is not None else get_now_idx(guild.id)
        self.now_playing_msg: discord.Message | None = None
        self.volume_level: float = DEFAULT_VOLUME

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

        await _delete_queue_add_msgs(self.guild.id)
        await _delete_search_result_msgs(self.guild.id)
        await _delete_queue_view_msg(self.guild.id)

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

        try: await interaction.response.defer()
        except Exception: pass

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction): return
        log("🔍 SEARCH", interaction)
        loop = self.loop if self.loop else asyncio.get_event_loop()
        modal = SearchModal(self.guild, self.channel, loop, lambda: loop)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.primary, row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        log("📋 QUEUE", interaction)
        embed = make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id))
        old_wmsg = queue_view_msgs.pop(self.guild.id, None)
        if old_wmsg:
            try: await old_wmsg.delete()
            except Exception: pass
        await interaction.response.defer(ephemeral=True, thinking=False)
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
                    current_track=None, current_idx: int = None):
    # กำลัง skip/prev → callback เก่านี้ต้องข้ามไป
    if guild.id in guild_changing:
        guild_changing.discard(guild.id)
        return

    # หยุดจงใจ (stop)
    if guild.id in guild_stopped:
        guild_stopped.discard(guild.id)
        return

    if current_idx is None:
        current_idx = get_now_idx(guild.id)

    next_idx = current_idx + 1
    q = get_full_queue(guild.id)

    if next_idx < len(q):
        track = q[next_idx]
        set_now_idx(guild.id, next_idx)
        url, title, duration, requester, *_thumb = track
        thumbnail = _thumb[0] if _thumb else None
        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
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
            view = PlayerView(guild, channel, loop, current_track=track, current_idx=next_idx)
            active_views[guild.id] = view
            msg = await channel.send(embed=embed, view=view)
            view.now_playing_msg = msg

        guild.voice_client.play(source, after=lambda e, _idx=next_idx:
            asyncio.run_coroutine_threadsafe(
                play_next(guild, channel, loop, current_idx=_idx), loop))

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

    @tree.command(name="play", description="เล่นเพลงจาก YouTube")
    @app_commands.describe(query="ระบุชื่อเพลง หรือวาง URL YouTube ที่นี่")
    async def slash_play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await safe_respond(interaction, embed=discord.Embed(
                description="❌ กรุณาเข้า Voice Channel ก่อนนะ!", color=discord.Color.red()), ephemeral=True)
        log("▶️ /play", interaction, f"query: {_trunc(query, 50)}")

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

            if is_url:
                url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query)
                track = (url, title, duration, interaction.user, thumbnail)
                await _del_search()
                await _add_and_play(vc, interaction.guild, interaction.channel, loop_getter, track)
            else:
                results = await asyncio.to_thread(search_tracks, query)
                if not results:
                    await _del_search()
                    return await interaction.followup.send(embed=discord.Embed(
                        description="❌ ไม่พบเพลง", color=discord.Color.red()), ephemeral=True)
                await _del_search()
                lines = [f"`{i+1}.` {r['title']} `{r['duration']}`" for i, r in enumerate(results)]
                embed = discord.Embed(title="🔍 ผลการค้นหา", description="\n".join(lines), color=0x1a1a2e)
                embed.set_footer(
                    text=f"กำลังรอ {interaction.user.display_name} เลือกเพลง • หมดเวลาใน 2 นาที")
                search_view = SearchResultView(results, interaction.guild, interaction.channel,
                                               loop_getter(), loop_getter, requester=interaction.user)
                pub_msg = await interaction.channel.send(
                    content=f"🎵 {interaction.user.mention} กำลังเลือกเพลง", embed=embed, view=search_view)
                search_view.message = pub_msg
                search_result_msgs.setdefault(interaction.guild.id, []).append(pub_msg)

        except Exception as e:
            err = str(e)
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if "Sign in" in err or "cookies" in err.lower():
                print(f"[{ts}] ⚠ /play | age-restricted | {_trunc(query, 50)}")
                msg_text = "❌ YouTube บล็อกการเข้าถึง กรุณาลองใหม่อีกครั้ง"
            else:
                print(f"[{ts}] ✗ /play | {_trunc(err, 80)}")
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

        old_view = active_views.pop(interaction.guild.id, None)
        await _delete_queue_add_msgs(interaction.guild.id)
        await _delete_search_result_msgs(interaction.guild.id)
        await _delete_queue_view_msg(interaction.guild.id)

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

        try: await interaction.response.send_message("⏳", ephemeral=True, delete_after=0)
        except Exception: pass

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