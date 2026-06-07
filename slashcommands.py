"""
slashcommands.py — ไฟล์กลางสำหรับ slash commands
ถูก import โดยทั้ง bot.py และ register.py
"""

import discord
from discord import app_commands
import yt_dlp
import asyncio
import datetime
import os

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

DEFAULT_VOLUME = 0.15  # 15%

# queue ไม่ pop แล้ว — เก็บ list ทั้งหมดไว้ ใช้ index แทน
queues: dict[int, list] = {}
current_index: dict[int, int] = {}  # guild_id -> index ที่กำลังเล่น

active_views: dict[int, "PlayerView"] = {}
queue_done_msgs: dict[int, object] = {}
queue_add_msgs: dict[int, list] = {}


def get_queue(guild_id: int) -> list:
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]


def get_index(guild_id: int) -> int:
    return current_index.get(guild_id, 0)


def set_index(guild_id: int, idx: int):
    current_index[guild_id] = idx


def clear_guild(guild_id: int):
    queues[guild_id] = []
    current_index.pop(guild_id, None)
    active_views.pop(guild_id, None)


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
    opts["quiet"] = True
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


def make_queue_embed(guild_id: int) -> discord.Embed:
    queue = get_queue(guild_id)
    idx = get_index(guild_id)
    if not queue:
        return discord.Embed(description="📋 Queue ว่างเปล่า", color=discord.Color.blurple())
    lines = []
    for i, t in enumerate(queue):
        title = t[1]
        marker = "  ◀ กำลังเล่น" if i == idx else ""
        lines.append(f"`{i+1}.` {title}{marker}")
    embed = discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=discord.Color.blurple())
    return embed


async def safe_respond(interaction: discord.Interaction, content=None, embed=None, view=None, ephemeral=False, wait=False):
    kwargs = {"ephemeral": ephemeral}
    if content:
        kwargs["content"] = content
    if embed:
        kwargs["embed"] = embed
    if view:
        kwargs["view"] = view
    try:
        if interaction.response.is_done():
            if wait:
                return await interaction.followup.send(**kwargs, wait=True)
            else:
                return await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
            return None
    except discord.errors.HTTPException:
        try:
            if wait:
                return await interaction.followup.send(**kwargs, wait=True)
            else:
                await interaction.followup.send(**kwargs)
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────
#  Volume Modal
# ─────────────────────────────────────────────

class VolumeModal(discord.ui.Modal, title="🔊 ปรับระดับเสียง"):
    vol_input = discord.ui.TextInput(
        label="ระดับเสียง (0-100)",
        placeholder="ค่าเริ่มต้น: 15",
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
                queue = get_queue(self.guild.id)
                queue.append(track)
                if vc.is_playing() or vc.is_paused():
                    pub_msg = await self.channel.send(
                        embed=discord.Embed(description=f"📋 เพิ่มใน Queue #{len(queue)}: **{title}** by {interaction.user.mention}", color=0x1a1a2e)
                    )
                    if self.guild.id not in queue_add_msgs:
                        queue_add_msgs[self.guild.id] = []
                    queue_add_msgs[self.guild.id].append(pub_msg)
                else:
                    idx = len(queue) - 1
                    set_index(self.guild.id, idx)
                    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
                    view = PlayerView(self.guild, self.channel, self.loop_getter(), current_index=idx)
                    active_views[self.guild.id] = view
                    _g, _ch, _lp = self.guild, self.channel, self.loop_getter()
                    vc.play(source, after=lambda e, g=_g, ch=_ch, lp=_lp: asyncio.run_coroutine_threadsafe(
                        play_next(g, ch, lp), lp
                    ))
                    embed = make_now_playing_embed(title, duration, interaction.user, thumbnail)
                    msg = await self.channel.send(embed=embed, view=view)
                    view.now_playing_msg = msg
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
            embed=discord.Embed(description=":hourglass_flowing_sand: กำลังเพิ่มเพลง", color=0x1a1a2e), ephemeral=True
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
        queue = get_queue(self.guild.id)
        queue.append(track)
        if vc.is_playing() or vc.is_paused():
            pub_msg = await self.channel.send(
                embed=discord.Embed(description=f"📋 เพิ่มใน Queue #{len(queue)}: **{r['title']}** by {interaction.user.mention}", color=0x1a1a2e)
            )
            if self.guild.id not in queue_add_msgs:
                queue_add_msgs[self.guild.id] = []
            queue_add_msgs[self.guild.id].append(pub_msg)
        else:
            q_idx = len(queue) - 1
            set_index(self.guild.id, q_idx)
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(r["url"], **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
            view = PlayerView(self.guild, self.channel, self.loop_getter(), current_index=q_idx)
            active_views[self.guild.id] = view
            _guild, _channel, _loop = self.guild, self.channel, self.loop_getter()
            vc.play(source, after=lambda e, g=_guild, ch=_channel, lp=_loop: asyncio.run_coroutine_threadsafe(
                play_next(g, ch, lp), lp
            ))
            embed = make_now_playing_embed(r["title"], r["duration"], interaction.user, r.get("thumbnail"))
            msg = await self.channel.send(embed=embed, view=view)
            view.now_playing_msg = msg
        try:
            await interaction.delete_original_response()
        except Exception:
            pass


# ─────────────────────────────────────────────
#  Player Buttons View
# ─────────────────────────────────────────────

class PlayerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, channel: discord.TextChannel, loop, current_index: int = 0):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.current_index = current_index
        self.now_playing_msg: discord.Message | None = None
        self.volume_level: float = DEFAULT_VOLUME

    async def delete_now_playing(self):
        if self.now_playing_msg:
            try:
                await self.now_playing_msg.delete()
            except Exception:
                pass
            self.now_playing_msg = None

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc:
            return await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)
        idx = get_index(self.guild.id)
        if idx <= 0:
            return await safe_respond(interaction, "❌ ไม่มีเพลงก่อนหน้าแล้ว", ephemeral=True)
        new_idx = idx - 1
        set_index(self.guild.id, new_idx)
        try:
            await interaction.response.defer()
        except Exception:
            pass
        await self.delete_now_playing()
        queue = get_queue(self.guild.id)
        url, title, duration, requester, *_thumb = queue[new_idx]
        thumbnail = _thumb[0] if _thumb else None
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = PlayerView(self.guild, self.channel, self.loop, current_index=new_idx)
        active_views[self.guild.id] = view
        vc.stop()
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
            play_next(self.guild, self.channel, self.loop), self.loop
        ))
        embed = make_now_playing_embed(title, duration, requester, thumbnail)
        msg = await self.channel.send(embed=embed, view=view)
        view.now_playing_msg = msg

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
        if vc and (vc.is_playing() or vc.is_paused()):
            try:
                await interaction.response.defer()
            except Exception:
                pass
            await self.delete_now_playing()
            vc.stop()
        else:
            await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc:
            clear_guild(self.guild.id)
            vc.stop()
            await vc.disconnect()
            for item in self.children:
                item.disabled = True
            try:
                await interaction.response.edit_message(view=self)
            except Exception:
                pass
            await self.delete_now_playing()
        else:
            await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        loop = self.loop if self.loop else asyncio.get_event_loop()
        modal = SearchModal(self.guild, self.channel, loop, lambda: loop)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.primary, row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = make_queue_embed(self.guild.id)
        await safe_respond(interaction, embed=embed, ephemeral=True)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc or not vc.source:
            return await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)
        modal = VolumeModal(vc, self)
        await interaction.response.send_modal(modal)


# ─────────────────────────────────────────────
#  play_next — เดิน index ไปเรื่อยๆ
# ─────────────────────────────────────────────

async def play_next(guild: discord.Guild, channel: discord.TextChannel, loop):
    old_view = active_views.pop(guild.id, None)
    if old_view:
        await old_view.delete_now_playing()

    # ลบข้อความ "เพิ่มใน Queue" ทั้งหมด
    for msg in queue_add_msgs.pop(guild.id, []):
        try:
            await msg.delete()
        except Exception:
            pass

    queue = get_queue(guild.id)
    idx = get_index(guild.id)
    next_idx = idx + 1

    if next_idx < len(queue):
        set_index(guild.id, next_idx)
        url, title, duration, requester, *_thumb = queue[next_idx]
        thumbnail = _thumb[0] if _thumb else None
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = PlayerView(guild, channel, loop, current_index=next_idx)
        active_views[guild.id] = view
        guild.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(guild, channel, loop), loop
            ),
        )
        embed = make_now_playing_embed(title, duration, requester, thumbnail)
        msg = await channel.send(embed=embed, view=view)
        view.now_playing_msg = msg
    else:
        await asyncio.sleep(1)
        vc = guild.voice_client
        if not vc or not vc.is_playing():
            embed = discord.Embed(
                description="✅ เล่นเพลงครบ Queue แล้ว — บอทจะออกใน 3 นาทีถ้าไม่มีเพลงใหม่",
                color=discord.Color.green()
            )
            msg = await channel.send(embed=embed)
            queue_done_msgs[guild.id] = msg

            await asyncio.sleep(180)
            vc = guild.voice_client
            if vc and not vc.is_playing() and get_index(guild.id) >= len(get_queue(guild.id)) - 1:
                await vc.disconnect()
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

            old_done_msg = queue_done_msgs.pop(interaction.guild.id, None)
            if old_done_msg:
                try:
                    await old_done_msg.delete()
                except Exception:
                    pass

            is_url = query.strip().startswith("http://") or query.strip().startswith("https://")

            if is_url:
                url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query)
                requester = interaction.user
                track = (url, title, duration, requester, thumbnail)
                queue = get_queue(interaction.guild.id)
                queue.append(track)
                if vc.is_playing() or vc.is_paused():
                    await _delete_searching()
                    pub_msg = await interaction.channel.send(
                        embed=discord.Embed(description=f"📋 เพิ่มใน Queue #{len(queue)}: **{title}** by {interaction.user.mention}", color=0x1a1a2e)
                    )
                    if interaction.guild.id not in queue_add_msgs:
                        queue_add_msgs[interaction.guild.id] = []
                    queue_add_msgs[interaction.guild.id].append(pub_msg)
                else:
                    q_idx = len(queue) - 1
                    set_index(interaction.guild.id, q_idx)
                    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
                    view = PlayerView(interaction.guild, interaction.channel, loop_getter(), current_index=q_idx)
                    active_views[interaction.guild.id] = view
                    _guild, _channel, _loop = interaction.guild, interaction.channel, loop_getter()
                    def _after(e, g=_guild, ch=_channel, lp=_loop):
                        asyncio.run_coroutine_threadsafe(play_next(g, ch, lp), lp)
                    vc.play(source, after=_after)
                    embed = make_now_playing_embed(title, duration, requester, thumbnail)
                    await _delete_searching()
                    msg = await interaction.channel.send(embed=embed, view=view)
                    view.now_playing_msg = msg
            else:
                results = await asyncio.to_thread(search_tracks, query)
                if not results:
                    await _delete_searching()
                    await interaction.followup.send(
                        embed=discord.Embed(description="❌ ไม่พบเพลง", color=discord.Color.red()),
                        ephemeral=True
                    )
                    return
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
                msg = "❌ YouTube บล็อกการเข้าถึง กรุณาลองใหม่อีกครั้ง" if ("Sign in" in err or "cookies" in err.lower()) else "❌ เกิดข้อผิดพลาด กรุณาลองใหม่"
                await _delete_searching()
                await interaction.followup.send(
                    embed=discord.Embed(description=msg, color=discord.Color.red()),
                    ephemeral=True
                )
            except Exception:
                pass

    @tree.command(name="stop", description="หยุดเพลงและล้าง Queue")
    async def slash_stop(interaction: discord.Interaction):
        if interaction.guild.voice_client:
            old_view = active_views.pop(interaction.guild.id, None)
            if old_view:
                await old_view.delete_now_playing()
            clear_guild(interaction.guild.id)
            interaction.guild.voice_client.stop()
            await interaction.guild.voice_client.disconnect()
            await safe_respond(interaction, "⏹️ หยุดเพลงและออกจาก Voice Channel แล้ว", ephemeral=True)
        else:
            await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)

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
        old  = [m for m in messages if m.created_at <= cutoff]
        deleted = 0

        if bulk:
            await interaction.channel.delete_messages(bulk)
            deleted += len(bulk)

        if old:
            try:
                await interaction.edit_original_response(
                    content=None,
                    embed=discord.Embed(
                        description=f"⏳ กำลังลบข้อความเก่า {len(old)} อัน (อาจใช้เวลาสักครู่)",
                        color=0x1a1a2e
                    )
                )
            except Exception:
                pass
            for msg in old:
                try:
                    await msg.delete()
                    deleted += 1
                    await asyncio.sleep(1.2)
                except (discord.NotFound, discord.HTTPException):
                    pass

        note = f" (รวมข้อความเก่า {len(old)} ข้อความ)" if old else ""
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