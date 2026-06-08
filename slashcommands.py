"""
slashcommands.py — ไฟล์กลางสำหรับ slash commands
ถูก import โดยทั้ง bot.py และ register.py
"""

import discord
from discord import app_commands
import yt_dlp
import asyncio
import datetime

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

DEFAULT_VOLUME = 0.05  # 5%

# full_queue เก็บเพลงทั้งหมด ไม่ลบเมื่อเล่น (สูงสุด 20)
full_queues: dict[int, list] = {}
# index เพลงที่กำลังเล่นอยู่ใน full_queue
now_playing_idx: dict[int, int] = {}

active_views: dict[int, "PlayerView"] = {}
queue_done_msgs: dict[int, object] = {}
queue_add_msgs: dict[int, list] = {}
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


def make_queue_embed(guild_id: int, current_idx: int = None) -> discord.Embed:
    q = get_full_queue(guild_id)
    idx = current_idx if current_idx is not None else get_now_idx(guild_id)
    if not q:
        return discord.Embed(description="📋 Queue ว่างเปล่า", color=discord.Color.blurple())
    lines = []
    for i, t in enumerate(q):
        marker = "  ◀ กำลังเล่น" if i == idx else ""
        lines.append(f"`{i+1}.` {t[1]}{marker}")
    return discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=discord.Color.blurple())


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




# ─────────────────────────────────────────────
#  Queue Done View (แสดงเมื่อเล่นครบ Queue)
# ─────────────────────────────────────────────

class QueueDoneView(discord.ui.View):
    def __init__(self, guild, channel, loop_getter):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop_getter = loop_getter

    @discord.ui.button(emoji="🔍", label="ค้นหาเพลง", style=discord.ButtonStyle.primary)
    async def search_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        loop = self.loop_getter()
        modal = SearchModal(self.guild, self.channel, loop, self.loop_getter)
        await interaction.response.send_modal(modal)

# ─────────────────────────────────────────────
#  Volume Modal
# ─────────────────────────────────────────────

class VolumeModal(discord.ui.Modal, title="🔊 ปรับระดับเสียง"):
    vol_input = discord.ui.TextInput(
        label="ระดับเสียง (0-100)",
        placeholder="ค่าเริ่มต้น: 5",
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

    def __init__(self, guild, channel, loop, loop_getter):
        super().__init__()
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(description="🔍 กำลังค้นหา", color=0x1a1a2e), ephemeral=True
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
            view = SearchResultView(results, self.guild, self.channel, self.loop, self.loop_getter)
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
    def __init__(self, results, guild, channel, loop, loop_getter):
        super().__init__(timeout=60)
        self.results = results
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.loop_getter = loop_getter
        options = [
            discord.SelectOption(label=r["title"][:100], value=str(i), description=f"⏱ {r['duration']}")
            for i, r in enumerate(results)
        ]
        select = discord.ui.Select(placeholder="เลือกเพลง", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=discord.Embed(description="⏳ กำลังเพิ่มเพลง", color=0x1a1a2e), ephemeral=True
        )
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
        track = (r["url"], r["title"], r["duration"], interaction.user, r.get("thumbnail"))
        await _add_and_play(vc, self.guild, self.channel, self.loop_getter, track, interaction)
        try:
            await interaction.delete_original_response()
        except Exception:
            pass


# ─────────────────────────────────────────────
#  Helper: เพิ่มเพลงและเล่น/queue
# ─────────────────────────────────────────────

async def _add_and_play(vc, guild, channel, loop_getter, track, interaction=None):
    """เพิ่มเพลงใน full_queue แล้วเล่นถ้า bot ว่างอยู่"""
    q = get_full_queue(guild.id)
    track_idx = add_to_queue(guild.id, track)
    url, title, duration, requester, *_thumb = track
    thumbnail = _thumb[0] if _thumb else None

    if vc.is_playing() or vc.is_paused():
        # มีเพลงเล่นอยู่ → แสดง "เพิ่มใน Queue"
        pos = track_idx - get_now_idx(guild.id) + 1  # ตำแหน่งถัดจากปัจจุบัน (นับ 1-based รวมเพลงที่กำลังเล่น)
        pub_msg = await channel.send(
            embed=discord.Embed(
                description=f"📋 เพิ่มใน Queue ตำแหน่ง #{pos}: **{title}** by {requester.mention}",
                color=0x1a1a2e
            )
        )
        if guild.id not in queue_add_msgs:
            queue_add_msgs[guild.id] = []
        queue_add_msgs[guild.id].append(pub_msg)
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

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc:
            return await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)
        idx = get_now_idx(self.guild.id)
        if idx <= 0:
            return await safe_respond(interaction, "❌ ไม่มีเพลงก่อนหน้าแล้ว", ephemeral=True)
        await self._play_at_idx(interaction, idx - 1)

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.secondary, row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc:
            return await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸"
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
        else:
            await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary, row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            return await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)
        idx = get_now_idx(self.guild.id)
        q = get_full_queue(self.guild.id)
        if idx + 1 >= len(q):
            return await safe_respond(interaction, "❌ ไม่มีเพลงถัดไปใน Queue", ephemeral=True)
        await self._play_at_idx(interaction, idx + 1)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc:
            return await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)

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
        loop = self.loop if self.loop else asyncio.get_event_loop()
        modal = SearchModal(self.guild, self.channel, loop, lambda: loop)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.primary, row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = make_queue_embed(self.guild.id, current_idx=get_now_idx(self.guild.id))
        await safe_respond(interaction, embed=embed, ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc or not vc.source:
            return await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)
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
    else:
        # ตรวจอีกครั้ง — อาจถูก stop ระหว่าง asyncio.sleep หรือ race condition
        if guild.id in guild_stopped:
            guild_stopped.discard(guild.id)
            return
        # เล่นครบ Queue → ลบข้อความ "เพิ่มใน Queue" ทั้งหมด
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
            return await safe_respond(interaction, "❌ กรุณาเข้า Voice Channel ก่อนนะ!", ephemeral=True)

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
            print(f"PLAY ERROR: {e}")
            try:
                err = str(e)
                msg_text = "❌ YouTube บล็อกการเข้าถึง กรุณาลองใหม่อีกครั้ง" if ("Sign in" in err or "cookies" in err.lower()) else "❌ เกิดข้อผิดพลาด กรุณาลองใหม่"
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
            return await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)

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