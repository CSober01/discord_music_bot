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

# กรอง log ที่ไม่จำเป็นของ discord.py
logging.getLogger("discord.player").setLevel(logging.ERROR)
logging.getLogger("discord.voice_state").setLevel(logging.WARNING)

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

DEFAULT_VOLUME = 0.10  # 10%

# full_queue เก็บเพลงทั้งหมด ไม่ลบเมื่อเล่น (สูงสุด 20)
full_queues: dict[int, list] = {}
# index เพลงที่กำลังเล่นอยู่ใน full_queue
now_playing_idx: dict[int, int] = {}

active_views: dict[int, "PlayerView"] = {}
queue_done_msgs: dict[int, object] = {}
queue_add_msgs: dict[int, list] = {}
queue_view_msgs: dict[int, object] = {}  # followup WebhookMessage ของ queue ต่อ guild
guild_stopped: set[int] = set()  # guilds ที่ถูก stop intentionally
guild_skip_once: set[int] = set()  # guilds ที่ _play_at_idx กำลัง stop เพื่อเปลี่ยนเพลง (play_next ต้อง skip 1 ครั้ง)

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
    """เพิ่มเพลงใน queue คืน index ที่เพิ่ม"""
    q = get_full_queue(guild_id)
    if len(q) >= MAX_QUEUE:
        # ลบเพลงที่เล่นผ่านไปแล้วออก (ก่อน now_playing_idx) ถ้ายังเกิน
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
    # หมายเหตุ: ไม่ discard guild_stopped ที่นี่ — ให้ play_next จัดการเอง


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
        duration_str = f"{minutes}:{seconds:02d}"
        thumbnail = info.get("thumbnail", None)
        return info["url"], info.get("title", "Unknown"), duration_str, thumbnail


def search_tracks(query: str, limit: int = 5):
    opts = get_ydl_options()
    opts["extract_flat"] = False
    opts["default_search"] = "ytsearch5"
    results = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(query, download=False)
        entries = info.get("entries", [info]) if "entries" in info else [info]
        for entry in entries[:limit]:
            duration = entry.get("duration", 0)
            m, s = divmod(int(duration), 60)
            results.append({
                "url": entry.get("url") or entry.get("webpage_url"),
                "title": entry.get("title", "Unknown"),
                "duration": f"{m}:{s:02d}",
                "thumbnail": entry.get("thumbnail", None),
            })
    return results


def make_now_playing_embed(title: str, duration: str, requester: discord.Member = None, thumbnail: str = None) -> discord.Embed:
    requester_str = requester.mention if requester else ""
    embed = discord.Embed(
        description=f"### 🎵  {title}\n⏱ `{duration}`　🎧 {requester_str}",
        color=0x1a1a2e,
    )
    embed.set_author(name="▶  Now Playing")
    embed.set_footer(text="SEa Music  •  ใช้ปุ่มด้านล่างเพื่อควบคุม")
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def make_done_embed() -> discord.Embed:
    return discord.Embed(
        description="⏹ หยุดเพลงและออกจาก Voice Channel แล้ว",
        color=discord.Color.green()
    )


_QUEUE_TITLE_NORMAL  = 40   # `` `N.` ชื่อ ``
_QUEUE_TITLE_PLAYING = 28   # 🎵 **N. ชื่อ** ◀ กำลังเล่น  (prefix+suffix กินพื้นที่มากกว่า)

def make_queue_embed(guild_id: int, current_idx: int = None) -> discord.Embed:
    q = get_full_queue(guild_id)
    idx = current_idx if current_idx is not None else get_now_idx(guild_id)
    if not q:
        return discord.Embed(description="📋 Queue ว่างเปล่า", color=discord.Color.blurple())
    lines = []
    for i, t in enumerate(q):
        if i == idx:
            t_cut = t[1] if len(t[1]) <= _QUEUE_TITLE_PLAYING else t[1][:_QUEUE_TITLE_PLAYING - 1] + "…"
            lines.append(f"**▶ {i+1}. {t_cut} ◀ กำลังเล่น**")
        else:
            t_cut = t[1] if len(t[1]) <= _QUEUE_TITLE_NORMAL else t[1][:_QUEUE_TITLE_NORMAL - 1] + "…"
            lines.append(f"`{i+1}.` {t_cut}")
    embed = discord.Embed(
        title="📋 Queue เพลง",
        description="\n".join(lines),
        color=0x5865F2,
    )
    embed.set_footer(text=f"กำลังเล่น #{idx + 1} จาก {len(q)} เพลง")
    return embed


MAX_TITLE_LOG = 40

def _trunc(text: str, n: int = MAX_TITLE_LOG) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"

def log(action: str, interaction: discord.Interaction, extra: str = ""):
    ts        = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    guild_str = _trunc(interaction.guild.name, 20)
    ch_str    = f"#{_trunc(interaction.channel.name, 16)}"
    user_str  = _trunc(interaction.user.display_name, 20)
    extra_str = f" | {extra}" if extra else ""
    print(f"[{ts}] {action:<12} | {guild_str} | {ch_str} | {user_str}{extra_str}")


async def check_in_voice(interaction: discord.Interaction) -> bool:
    vc = interaction.guild.voice_client
    if not vc:
        await safe_respond(interaction, embed=discord.Embed(description="❌ บอทไม่ได้อยู่ใน Voice Channel", color=discord.Color.red()), ephemeral=True)
        return False
    if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
        await safe_respond(interaction, embed=discord.Embed(description=f"❌ คุณต้องอยู่ใน **{vc.channel.name}** ถึงจะใช้งานได้", color=discord.Color.red()), ephemeral=True)
        return False
    return True


async def safe_respond(interaction: discord.Interaction, content=None, embed=None, view=None, ephemeral=False):
    kwargs = {"ephemeral": ephemeral}
    if content:
        kwargs["content"] = content
    if embed:
        kwargs["embed"] = embed
    if view:
        kwargs["view"] = view
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
    """edit followup queue message ที่เปิดอยู่ หรือลบถ้า expire แล้ว"""
    wmsg = queue_view_msgs.get(guild_id)
    if not wmsg:
        return
    try:
        await wmsg.edit(embed=make_queue_embed(guild_id))
    except Exception:
        # message expire หรือถูกลบไปแล้ว
        queue_view_msgs.pop(guild_id, None)


# ─────────────────────────────────────────────
#  Queue Done View (แสดงเมื่อเล่นครบ Queue)
# ─────────────────────────────────────────────

class QueueDoneView(discord.ui.View):
    def __init__(self, guild, channel, loop_getter, done_msg_ref: list = None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop_getter = loop_getter
        self.done_msg_ref = done_msg_ref  # list[Message] เพื่อให้แก้ reference ได้หลัง send

    @discord.ui.button(emoji="🔍", label="ค้นหาเพลง", style=discord.ButtonStyle.primary)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        loop = self.loop_getter()
        modal = SearchModal(self.guild, self.channel, loop, self.loop_getter, done_msg_ref=self.done_msg_ref)
        await interaction.response.send_modal(modal)

# ─────────────────────────────────────────────
#  Volume Modal
# ─────────────────────────────────────────────

class VolumeModal(discord.ui.Modal, title="🔊 ปรับระดับเสียง"):
    vol_input = discord.ui.TextInput(
        label="ระดับเสียง (0-100)",
        placeholder="ค่าเริ่มต้น: 10",
        min_length=1,
        max_length=3,
    )

    def __init__(self, vc, player_view):
        super().__init__()
        self.vc = vc
        self.player_view = player_view

    async def on_submit(self, interaction: discord.Interaction):
        try:
            vol = int(str(self.vol_input))
            if not 0 <= vol <= 100:
                raise ValueError
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
        min_length=1,
        max_length=100,
    )

    def __init__(self, guild, channel, loop, loop_getter, done_msg_ref: list = None):
        super().__init__()
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter
        self.done_msg_ref = done_msg_ref  # list[Message | None] reference จาก QueueDoneView

    async def _delete_done_msg(self):
        """ลบข้อความ 'เล่นครบ Queue' ถ้ามี"""
        if self.done_msg_ref and self.done_msg_ref[0]:
            try:
                await self.done_msg_ref[0].delete()
            except Exception:
                pass
            self.done_msg_ref[0] = None
        # ลบจาก queue_done_msgs ด้วย (ถ้ายังค้างอยู่)
        old = queue_done_msgs.pop(self.guild.id, None)
        if old:
            try:
                await old.delete()
            except Exception:
                pass

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(description=f"🔍 กำลังค้นหา **{self.query}**", color=0x1a1a2e), ephemeral=True
        )
        query_str = str(self.query).strip()
        try:
            if query_str.startswith("http://") or query_str.startswith("https://"):
                url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query_str)
                vc = self.guild.voice_client
                if not vc:
                    if not interaction.user.voice:
                        await interaction.edit_original_response(
                            embed=discord.Embed(description="❌ กรุณาเข้า Voice Channel ก่อน", color=discord.Color.red())
                        )
                        return
                    vc = await interaction.user.voice.channel.connect()
                await self._delete_done_msg()
                track = (url, title, duration, interaction.user, thumbnail)
                await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track, interaction)
                try:
                    await interaction.delete_original_response()
                except Exception:
                    pass
                return

            results = await asyncio.to_thread(search_tracks, query_str)
            if not results:
                await interaction.edit_original_response(
                    embed=discord.Embed(description="❌ ไม่พบเพลง", color=discord.Color.red())
                )
                return
            view = SearchResultView(results, self.guild, self.channel, self.loop, self.loop_getter, done_msg_ref=self.done_msg_ref)
            lines = [f"`{i+1}.` {r['title']} `{r['duration']}`" for i, r in enumerate(results)]
            embed = discord.Embed(title="🔍 ผลการค้นหา", description="\n".join(lines), color=0x1a1a2e)
            embed.set_footer(text="เลือกเพลงที่ต้องการเพิ่มใน Queue")
            await interaction.edit_original_response(embed=embed, view=view)

        except Exception:
            await interaction.edit_original_response(
                embed=discord.Embed(description="❌ เกิดข้อผิดพลาด กรุณาลองใหม่", color=discord.Color.red())
            )


# ─────────────────────────────────────────────
#  Search Result View
# ─────────────────────────────────────────────

class SearchResultView(discord.ui.View):
    def __init__(self, results, guild, channel, loop, loop_getter, done_msg_ref: list = None):
        super().__init__(timeout=60)
        self.results = results
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter
        self.done_msg_ref = done_msg_ref
        self._used = False  # guard ป้องกัน double-select
        options = [
            discord.SelectOption(label=r["title"][:100], value=str(i), description=f"⏱ {r['duration']}")
            for i, r in enumerate(results)
        ]
        select = discord.ui.Select(placeholder="เลือกเพลง", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        if self._used:
            return await interaction.response.defer()
        self._used = True
        # disable select ทันทีเพื่อป้องกันกด/เลือกซ้ำ
        for item in self.children:
            item.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            await interaction.response.defer()
        idx = int(interaction.data["values"][0])
        r = self.results[idx]
        vc = self.guild.voice_client
        if not vc:
            if not interaction.user.voice:
                await interaction.edit_original_response(
                    embed=discord.Embed(description="❌ กรุณาเข้า Voice Channel ก่อน", color=discord.Color.red())
                )
                return
            vc = await interaction.user.voice.channel.connect()
        # ลบข้อความ "เล่นครบ Queue" ถ้ามี
        if self.done_msg_ref and self.done_msg_ref[0]:
            try:
                await self.done_msg_ref[0].delete()
            except Exception:
                pass
            self.done_msg_ref[0] = None
        old_done = queue_done_msgs.pop(self.guild.id, None)
        if old_done:
            try:
                await old_done.delete()
            except Exception:
                pass
        track = (r["url"], r["title"], r["duration"], interaction.user, r.get("thumbnail"))
        await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track, interaction)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass


# ─────────────────────────────────────────────
#  Helper: เพิ่มเพลงและเล่น/queue
# ─────────────────────────────────────────────

_play_locks: dict[int, asyncio.Lock] = {}

def get_play_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _play_locks:
        _play_locks[guild_id] = asyncio.Lock()
    return _play_locks[guild_id]


async def _add_and_play(vc, guild, channel, loop_getter, track, interaction=None):
    """เพิ่มเพลงใน full_queue แล้วเล่นถ้า bot ว่างอยู่"""
    async with get_play_lock(guild.id):
        q = get_full_queue(guild.id)
        track_idx = add_to_queue(guild.id, track)
        url, title, duration, requester, *_thumb = track
        thumbnail = _thumb[0] if _thumb else None

        if vc.is_playing() or vc.is_paused():
            # มีเพลงเล่นอยู่ → แสดง "เพิ่มใน Queue"
            pos = track_idx + 1  # เลขลำดับจริงใน queue (1-based)
            # ตัดชื่อเพลงให้ไม่เกิน 50 ตัวอักษรเพื่อไม่ให้ล้น
            short_title = title if len(title) <= 50 else title[:47] + "…"
            pub_msg = await channel.send(
                embed=discord.Embed(
                    description=f"📋 เพิ่มใน Queue **#{pos}**\n🎵 {short_title}\n👤 {requester.mention}",
                    color=0x1a1a2e
                )
            )
            if guild.id not in queue_add_msgs:
                queue_add_msgs[guild.id] = []
            queue_add_msgs[guild.id].append(pub_msg)
            await _refresh_queue_msg(guild.id)
        else:
            # bot ว่าง → เล่นเลย
            set_now_idx(guild.id, track_idx)
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
            loop = loop_getter()
            view = PlayerView(guild, channel, loop, current_track=track, current_idx=track_idx)
            active_views[guild.id] = view
            _g, _ch, _lp, _t, _ti = guild, channel, loop, track, track_idx
            vc.play(source, after=lambda e, g=_g, ch=_ch, lp=_lp, t=_t, ti=_ti: asyncio.run_coroutine_threadsafe(
                play_next(g, ch, lp, current_track=t, current_idx=ti), lp
            ))
            embed = make_now_playing_embed(title, duration, requester, thumbnail)
            msg = await channel.send(embed=embed, view=view)
            view.now_playing_msg = msg


# ─────────────────────────────────────────────
#  Player View
# ─────────────────────────────────────────────

class PlayerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, channel: discord.TextChannel, loop, current_track=None, current_idx: int = None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.current_track = current_track
        self.current_idx: int = current_idx if current_idx is not None else get_now_idx(guild.id)
        self.now_playing_msg: discord.Message | None = None
        self.volume_level: float = DEFAULT_VOLUME

    async def delete_now_playing(self):
        if self.now_playing_msg:
            try:
                await self.now_playing_msg.delete()
            except Exception:
                pass
            self.now_playing_msg = None

    async def _play_at_idx(self, interaction: discord.Interaction, idx: int):
        """เล่นเพลงที่ index idx ใน full_queue"""
        q = get_full_queue(self.guild.id)
        if not (0 <= idx < len(q)):
            return await safe_respond(interaction, "❌ ไม่มีเพลงในตำแหน่งนั้น", ephemeral=True)
        track = q[idx]
        set_now_idx(self.guild.id, idx)
        url, title, duration, requester, *_thumb = track
        thumbnail = _thumb[0] if _thumb else None
        vc = self.guild.voice_client
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        new_view = PlayerView(self.guild, self.channel, self.loop, current_track=track, current_idx=idx)
        active_views[self.guild.id] = new_view
        guild_skip_once.add(self.guild.id)  # บอก play_next ว่า after= callback ตัวเก่าต้อง skip ไป
        vc.stop()
        # ไม่ discard ที่นี่ — ให้ play_next เป็นคน discard เมื่อถูกเรียก (ป้องกัน race condition กับ after= thread)
        vc.play(source, after=lambda e, _idx=idx: asyncio.run_coroutine_threadsafe(
            play_next(self.guild, self.channel, self.loop, current_idx=_idx), self.loop
        ))
        embed = make_now_playing_embed(title, duration, requester, thumbnail)
        try:
            await interaction.response.edit_message(embed=embed, view=new_view)
            msg = await interaction.original_response()
        except Exception:
            await self.delete_now_playing()
            msg = await self.channel.send(embed=embed, view=new_view)
        new_view.now_playing_msg = msg
        await _refresh_queue_msg(self.guild.id)

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction):
            return
        idx = get_now_idx(self.guild.id)
        if idx <= 0:
            return await safe_respond(interaction, embed=discord.Embed(description="❌ ไม่มีเพลงก่อนหน้าแล้ว", color=discord.Color.red()), ephemeral=True)
        log("⏮ PREV", interaction, f"เพลง: {_trunc(self.current_track[1]) if self.current_track else '?'}")
        await self._play_at_idx(interaction, idx - 1)

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction):
            return
        vc = self.guild.voice_client
        title = _trunc(self.current_track[1]) if self.current_track else "?"
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            log("⏸ PAUSE", interaction, f"เพลง: {title}")
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸"
            log("▶️ RESUME", interaction, f"เพลง: {title}")
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
        else:
            await safe_respond(interaction, embed=discord.Embed(description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction):
            return
        vc = self.guild.voice_client
        if not (vc.is_playing() or vc.is_paused()):
            return await safe_respond(interaction, embed=discord.Embed(description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
        idx = get_now_idx(self.guild.id)
        q = get_full_queue(self.guild.id)
        if idx + 1 >= len(q):
            return await safe_respond(interaction, embed=discord.Embed(description="❌ ไม่มีเพลงถัดไปใน Queue", color=discord.Color.red()), ephemeral=True)
        log("⏭ SKIP", interaction, f"เพลง: {_trunc(self.current_track[1]) if self.current_track else '?'}")
        await self._play_at_idx(interaction, idx + 1)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction):
            return
        vc = self.guild.voice_client
        log("⏹ STOP", interaction, f"เพลง: {_trunc(self.current_track[1]) if self.current_track else '?'}")

        for msg in queue_add_msgs.pop(self.guild.id, []):
            try:
                await msg.delete()
            except Exception:
                pass

        guild_stopped.add(self.guild.id)  # บอก play_next ว่าหยุดจงใจ
        clear_guild(self.guild.id)
        vc.stop()
        await vc.disconnect()

        # ลบข้อความ done เดิมถ้ามี (ป้องกันซ้อน)
        old_done = queue_done_msgs.pop(self.guild.id, None)
        if old_done:
            try:
                await old_done.delete()
            except Exception:
                pass

        # edit now_playing เป็น done message แทนส่งใหม่
        done_embed = make_done_embed()
        if self.now_playing_msg:
            try:
                await self.now_playing_msg.edit(embed=done_embed, view=None)
                queue_done_msgs[self.guild.id] = self.now_playing_msg
                self.now_playing_msg = None
            except Exception:
                done_msg = await self.channel.send(embed=done_embed)
                queue_done_msgs[self.guild.id] = done_msg
        else:
            done_msg = await self.channel.send(embed=done_embed)
            queue_done_msgs[self.guild.id] = done_msg

        try:
            await interaction.response.defer()
        except Exception:
            pass

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction):
            return
        log("🔍 SEARCH", interaction)
        loop = self.loop if self.loop else asyncio.get_event_loop()
        modal = SearchModal(self.guild, self.channel, loop, lambda: loop)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.primary, row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        log("📋 QUEUE", interaction)
        embed = make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id))
        # ลบอันเก่าก่อน (followup WebhookMessage สามารถ edit/delete ได้ต่างจาก ephemeral ปกติ)
        old_wmsg = queue_view_msgs.pop(self.guild.id, None)
        if old_wmsg:
            try:
                await old_wmsg.delete()
            except Exception:
                pass
        await interaction.response.defer(ephemeral=True, thinking=False)
        wmsg = await interaction.followup.send(embed=embed, ephemeral=True, wait=True)
        queue_view_msgs[self.guild.id] = wmsg

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await check_in_voice(interaction):
            return
        vc = self.guild.voice_client
        if not vc.source:
            return await safe_respond(interaction, embed=discord.Embed(description="❌ ไม่มีเพลงที่กำลังเล่นอยู่", color=discord.Color.red()), ephemeral=True)
        log("🔊 VOLUME", interaction)
        modal = VolumeModal(vc, self)
        await interaction.response.send_modal(modal)


# ─────────────────────────────────────────────
#  play_next
# ─────────────────────────────────────────────

async def play_next(guild: discord.Guild, channel: discord.TextChannel, loop, current_track=None, current_idx: int = None):
    # ถ้าหยุดจงใจ ไม่ต้องทำอะไร
    if guild.id in guild_stopped:
        guild_stopped.discard(guild.id)
        return

    # ถ้า _play_at_idx สั่ง stop เพื่อเปลี่ยนเพลง → callback ตัวเก่านี้ควร skip ไปเฉยๆ (เพลงใหม่ถูก play แล้ว)
    if guild.id in guild_skip_once:
        guild_skip_once.discard(guild.id)
        return

    old_view = active_views.pop(guild.id, None)
    if old_view:
        await old_view.delete_now_playing()

    if current_idx is None:
        current_idx = get_now_idx(guild.id)

    next_idx = current_idx + 1
    q = get_full_queue(guild.id)

    if next_idx < len(q):
        track = q[next_idx]
        set_now_idx(guild.id, next_idx)
        url, title, duration, requester, *_thumb = track
        thumbnail = _thumb[0] if _thumb else None
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = PlayerView(guild, channel, loop, current_track=track, current_idx=next_idx)
        active_views[guild.id] = view
        guild.voice_client.play(
            source,
            after=lambda e, _idx=next_idx: asyncio.run_coroutine_threadsafe(
                play_next(guild, channel, loop, current_idx=_idx), loop
            ),
        )
        embed = make_now_playing_embed(title, duration, requester, thumbnail)
        msg = await channel.send(embed=embed, view=view)
        view.now_playing_msg = msg
        await _refresh_queue_msg(guild.id)
    else:
        # ตรวจอีกครั้ง — อาจถูก stop ระหว่าง asyncio.sleep หรือ race condition
        if guild.id in guild_stopped:
            guild_stopped.discard(guild.id)
            return
        # เล่นครบ Queue → ลบ queue view + ข้อความ "เพิ่มใน Queue" ทั้งหมด
        old_qv = queue_view_msgs.pop(guild.id, None)
        if old_qv:
            try:
                await old_qv.delete()
            except Exception:
                pass
        for msg in queue_add_msgs.pop(guild.id, []):
            try:
                await msg.delete()
            except Exception:
                pass
        await asyncio.sleep(1)
        vc = guild.voice_client
        if not vc or not vc.is_playing():
            old_done = queue_done_msgs.pop(guild.id, None)
            if old_done:
                try:
                    await old_done.delete()
                except Exception:
                    pass
            view = QueueDoneView(guild, channel, lambda: loop)
            msg = await channel.send(
                embed=discord.Embed(
                    description="✅ เล่นเพลงครบ Queue แล้ว — บอทจะออกใน 5 นาทีถ้าไม่มีเพลงใหม่",
                    color=discord.Color.green()
                ),
                view=view
            )
            queue_done_msgs[guild.id] = msg

            await asyncio.sleep(300)
            vc = guild.voice_client
            if vc and not vc.is_playing() and not vc.is_paused() and next_idx >= len(get_full_queue(guild.id)):
                await vc.disconnect()
                clear_guild(guild.id)
                done_msg = queue_done_msgs.pop(guild.id, None)
                if done_msg:
                    try:
                        await done_msg.delete()
                    except Exception:
                        pass


# ─────────────────────────────────────────────
#  Slash Commands
# ─────────────────────────────────────────────

def register(tree: app_commands.CommandTree, loop_getter):

    @tree.command(name="play", description="เล่นเพลงจาก YouTube")
    @app_commands.describe(query="ระบุชื่อเพลง หรือวาง URL YouTube ที่นี่")
    async def slash_play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await safe_respond(interaction, embed=discord.Embed(description="❌ กรุณาเข้า Voice Channel ก่อนนะ!", color=discord.Color.red()), ephemeral=True)
        log("▶️ /play", interaction, f"query: {_trunc(query, 50)}")

        done_msg = queue_done_msgs.pop(interaction.guild.id, None)
        if done_msg:
            try:
                await done_msg.delete()
            except Exception:
                pass

        searching_msg = None
        try:
            await interaction.response.send_message(
                embed=discord.Embed(description=f"🔍 กำลังค้นหา **{query}**", color=discord.Color.blurple())
            )
            searching_msg = await interaction.original_response()
        except Exception:
            pass

        async def _delete_searching():
            if searching_msg:
                try:
                    await searching_msg.delete()
                except Exception:
                    pass

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
                await _delete_searching()
                await _add_and_play(vc, interaction.guild, interaction.channel, loop_getter, track)
            else:
                results = await asyncio.to_thread(search_tracks, query)
                if not results:
                    await _delete_searching()
                    return await interaction.followup.send(
                        embed=discord.Embed(description="❌ ไม่พบเพลง", color=discord.Color.red()),
                        ephemeral=True
                    )
                search_view = SearchResultView(results, interaction.guild, interaction.channel, loop_getter(), loop_getter)
                lines = [f"`{i+1}.` {r['title']} `{r['duration']}`" for i, r in enumerate(results)]
                embed = discord.Embed(title="🔍 ผลการค้นหา", description="\n".join(lines), color=0x1a1a2e)
                embed.set_footer(text="เลือกเพลงที่ต้องการเพิ่มใน Queue")
                await _delete_searching()
                await interaction.followup.send(embed=embed, view=search_view, ephemeral=True)

        except Exception as e:
            err = str(e)
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if "Sign in" in err or "cookies" in err.lower():
                print(f"[{ts}] ⚠ /play       | age-restricted | {_trunc(query, 50)}")
                msg_text = "❌ YouTube บล็อกการเข้าถึง กรุณาลองใหม่อีกครั้ง"
            else:
                print(f"[{ts}] ✗ /play       | {_trunc(err, 80)}")
                msg_text = "❌ เกิดข้อผิดพลาด กรุณาลองใหม่"
            try:
                await _delete_searching()
                await interaction.followup.send(
                    embed=discord.Embed(description=msg_text, color=discord.Color.red()),
                    ephemeral=True
                )
            except Exception:
                pass

    @tree.command(name="stop", description="หยุดเพลงและออกจาก Voice Channel")
    async def slash_stop(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if not vc:
            return await safe_respond(interaction, embed=discord.Embed(description="❌ บอทไม่ได้อยู่ใน Voice Channel", color=discord.Color.red()), ephemeral=True)
        if not interaction.user.voice or interaction.user.voice.channel != vc.channel:
            return await safe_respond(interaction, embed=discord.Embed(description=f"❌ คุณต้องอยู่ใน **{vc.channel.name}** ถึงจะใช้งานได้", color=discord.Color.red()), ephemeral=True)
        cur = active_views.get(interaction.guild.id)
        log("⏹ /stop", interaction, f"เพลง: {_trunc(cur.current_track[1]) if cur and cur.current_track else '?'}")

        old_view = active_views.pop(interaction.guild.id, None)

        for msg in queue_add_msgs.pop(interaction.guild.id, []):
            try:
                await msg.delete()
            except Exception:
                pass

        now_playing_msg = old_view.now_playing_msg if old_view else None
        if old_view:
            old_view.now_playing_msg = None  # ป้องกัน delete_now_playing ลบก่อน

        guild_stopped.add(interaction.guild.id)  # บอก play_next ว่าหยุดจงใจ
        clear_guild(interaction.guild.id)
        vc.stop()
        await vc.disconnect()

        # ลบข้อความ done เดิมถ้ามี (ป้องกันซ้อน)
        old_done = queue_done_msgs.pop(interaction.guild.id, None)
        if old_done:
            try:
                await old_done.delete()
            except Exception:
                pass

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

        try:
            await interaction.response.send_message("⏳", ephemeral=True, delete_after=0)
        except Exception:
            pass

    @tree.command(name="clear", description="ลบข้อความในช่อง")
    @app_commands.describe(amount="จำนวนข้อความที่ต้องการลบ (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_clear(interaction: discord.Interaction, amount: int):
        if not 1 <= amount <= 100:
            return await safe_respond(interaction, "❌ ใส่จำนวน 1-100 เท่านั้น", ephemeral=True)

        await interaction.response.send_message(
            embed=discord.Embed(description="🧹 กำลังลบข้อความ", color=0x1a1a2e), ephemeral=True
        )

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
                await interaction.edit_original_response(
                    content=None,
                    embed=discord.Embed(
                        description=f"⏳ กำลังลบข้อความเก่า {len(old_msgs)} อัน (อาจใช้เวลาสักครู่)",
                        color=0x1a1a2e
                    )
                )
            except Exception:
                pass
            for msg in old_msgs:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(1.2)
                except (discord.NotFound, discord.HTTPException):
                    pass

        note = f" (รวมข้อความเก่า {len(old_msgs)} ข้อความ)" if old_msgs else ""
        result_embed = discord.Embed(
            description=f"🗑️ ลบข้อความไปแล้ว {deleted} ข้อความ{note}",
            color=0x1a1a2e
        )
        try:
            await interaction.edit_original_response(content=None, embed=result_embed)
        except Exception:
            status_msg = await interaction.channel.send(embed=result_embed)
            await asyncio.sleep(5)
            try:
                await status_msg.delete()
            except Exception:
                pass

    @slash_clear.error
    async def slash_clear_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await safe_respond(interaction, "❌ คุณไม่มีสิทธิ์ลบข้อความ", ephemeral=True)