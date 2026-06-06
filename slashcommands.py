"""
slashcommands.py — ไฟล์กลางสำหรับ slash commands
ถูก import โดยทั้ง bot.py และ register.py
"""

import discord
from discord import app_commands
import yt_dlp
import asyncio
import os

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

DEFAULT_VOLUME = 0.3  # 30%

queues: dict[int, list] = {}
histories: dict[int, list] = {}

active_views: dict[int, "PlayerView"] = {}
queue_done_msgs: dict[int, object] = {}  # เก็บ message "เล่นครบแล้ว" ต่อ guild


def get_queue(guild_id: int) -> list:
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]


def get_history(guild_id: int) -> list:
    if guild_id not in histories:
        histories[guild_id] = []
    return histories[guild_id]


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


def make_now_playing_embed(title: str, duration: str, requester: discord.Member = None) -> discord.Embed:
    requester_str = requester.mention if requester else ""
    embed = discord.Embed(
        description=f"### 🎶  {title}\n⏱ `{duration}`　🎧 {requester_str}",
        color=0x1a1a2e,
    )
    embed.set_author(name="▶  Now Playing")
    embed.set_footer(text="SEa Music  •  ใช้ปุ่มด้านล่างเพื่อควบคุม")
    return embed


async def safe_respond(interaction: discord.Interaction, content=None, embed=None, view=None, ephemeral=False, wait=False):
    """ส่ง response โดยไม่สนว่า interaction ถูก acknowledge แล้วหรือยัง"""
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
        placeholder="เช่น 50",
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
        label="ชื่อเพลงหรือศิลปิน",
        placeholder="เช่น Bryan Adams Heaven",
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
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            results = await asyncio.to_thread(search_tracks, str(self.query))
            if not results:
                return await interaction.followup.send("❌ ไม่พบเพลง", ephemeral=True)
            view = SearchResultView(results, self.guild, self.channel, self.loop, self.loop_getter)
            lines = [f"`{i+1}.` {r['title']} `{r['duration']}`" for i, r in enumerate(results)]
            embed = discord.Embed(
                title="🔍 ผลการค้นหา",
                description="\n".join(lines),
                color=0x1a1a2e,
            )
            embed.set_footer(text="เลือกเพลงที่ต้องการเพิ่มใน Queue")
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาด กรุณาลองใหม่", ephemeral=True)


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
        select = discord.ui.Select(placeholder="เลือกเพลง...", options=options)
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        idx = int(interaction.data["values"][0])
        r = self.results[idx]
        vc = self.guild.voice_client
        if not vc:
            if not interaction.user.voice:
                return await interaction.followup.send("❌ กรุณาเข้า Voice Channel ก่อน", ephemeral=True)
            vc = await interaction.user.voice.channel.connect()
        track = (r["url"], r["title"], r["duration"], interaction.user, r.get("thumbnail"))
        queue = get_queue(self.guild.id)
        if vc.is_playing() or vc.is_paused():
            queue.append(track)
            await interaction.followup.send(
                embed=discord.Embed(description=f"📋 เพิ่มใน Queue: **{r['title']}** (ตำแหน่ง #{len(queue)})", color=0x1a1a2e),
                ephemeral=True
            )
        else:
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(r["url"], **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
            view = PlayerView(self.guild, self.channel, self.loop_getter(), current_track=track)
            active_views[self.guild.id] = view
            _guild, _channel, _loop, _track = self.guild, self.channel, self.loop_getter(), track
            vc.play(source, after=lambda e, g=_guild, ch=_channel, lp=_loop, t=_track: asyncio.run_coroutine_threadsafe(play_next(g, ch, lp, current_track=t), lp))
            embed = make_now_playing_embed(r["title"], r["duration"], interaction.user, r.get("thumbnail"))
            msg = await self.channel.send(embed=embed, view=view)
            view.now_playing_msg = msg
            await interaction.followup.send("▶️ เริ่มเล่นเพลงแล้ว!", ephemeral=True)

# ─────────────────────────────────────────────
#  Player Buttons View
# ─────────────────────────────────────────────

class PlayerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, channel: discord.TextChannel, loop, current_track=None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.current_track = current_track
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
        history = get_history(self.guild.id)
        if not history:
            return await safe_respond(interaction, "❌ ไม่มีประวัติเพลงก่อนหน้า", ephemeral=True)

        prev_track = history.pop()
        queue = get_queue(self.guild.id)
        if self.current_track:
            queue.insert(0, self.current_track)

        try:
            await interaction.response.defer()
        except Exception:
            pass
        await self.delete_now_playing()

        url, title, duration, requester, *_thumb = prev_track
        thumbnail = _thumb[0] if _thumb else None
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = PlayerView(self.guild, self.channel, self.loop, current_track=prev_track)
        active_views[self.guild.id] = view

        vc.stop()
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
            play_next(self.guild, self.channel, self.loop, current_track=prev_track), self.loop
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
        if vc and vc.is_playing():
            try:
                await interaction.response.defer()
            except Exception:
                pass
            await self.delete_now_playing()
            vc.stop()
        else:
            await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @discord.ui.button(emoji="🟰", style=discord.ButtonStyle.primary, row=1)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(self.guild.id)
        if not queue:
            return await safe_respond(interaction, "📋 Queue ว่างเปล่า", ephemeral=True)
        lines = [f"`{i+1}.` {t[1]}" for i, t in enumerate(queue)]
        embed = discord.Embed(
            title="📋 Queue เพลง",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await safe_respond(interaction, embed=embed, ephemeral=True)

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        loop = self.loop if self.loop else asyncio.get_event_loop()
        modal = SearchModal(self.guild, self.channel, loop, lambda: loop)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=1)
    async def volume_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc or not vc.source:
            return await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)
        modal = VolumeModal(vc, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc:
            queues[self.guild.id] = []
            histories[self.guild.id] = []
            active_views.pop(self.guild.id, None)
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


# ─────────────────────────────────────────────
#  play_next
# ─────────────────────────────────────────────

async def play_next(guild: discord.Guild, channel: discord.TextChannel, loop, current_track=None):
    old_view = active_views.pop(guild.id, None)
    if old_view:
        await old_view.delete_now_playing()

    if current_track:
        history = get_history(guild.id)
        history.append(current_track)
        if len(history) > 20:
            history.pop(0)

    queue = get_queue(guild.id)
    if queue:
        url, title, duration, requester, *_thumb = queue.pop(0)
        thumbnail = _thumb[0] if _thumb else None
        track = (url, title, duration, requester, thumbnail)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = PlayerView(guild, channel, loop, current_track=track)
        active_views[guild.id] = view
        guild.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(guild, channel, loop, current_track=track), loop
            ),
        )
        embed = make_now_playing_embed(title, duration, requester, thumbnail)
        msg = await channel.send(embed=embed, view=view)
        view.now_playing_msg = msg
    else:
        # รอสักครู่ก่อนแสดงข้อความ เผื่อมีเพลงใหม่เข้ามาพอดี
        await asyncio.sleep(1)
        queue = get_queue(guild.id)
        vc = guild.voice_client
        if not queue and (not vc or not vc.is_playing()):
            embed = discord.Embed(description="✅ เล่นเพลงครบ Queue แล้ว", color=discord.Color.green())
            msg = await channel.send(embed=embed)
            queue_done_msgs[guild.id] = msg


# ─────────────────────────────────────────────
#  Slash Commands
# ─────────────────────────────────────────────

def register(tree: app_commands.CommandTree, loop_getter):

    @tree.command(name="play", description="เล่นเพลงจาก YouTube")
    @app_commands.describe(query="ชื่อเพลง หรือ URL YouTube")
    async def slash_play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await safe_respond(interaction, "❌ กรุณาเข้า Voice Channel ก่อนนะ!", ephemeral=True)

        try:
            search_embed = discord.Embed(
                description=f"🔍 กำลังค้นหา **{query}** ...",
                color=discord.Color.blurple()
            )
            await interaction.response.send_message(embed=search_embed)
        except Exception:
            pass

        try:
            voice_channel = interaction.user.voice.channel
            vc = interaction.guild.voice_client

            if not vc:
                vc = await voice_channel.connect()
            elif vc.channel != voice_channel:
                await vc.move_to(voice_channel)

            url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query)
            requester = interaction.user
            track = (url, title, duration, requester, thumbnail)
            queue = get_queue(interaction.guild.id)

            # ลบข้อความ "เล่นครบแล้ว" ถ้ามี
            old_done_msg = queue_done_msgs.pop(interaction.guild.id, None)
            if old_done_msg:
                try:
                    await old_done_msg.delete()
                except Exception:
                    pass

            if vc.is_playing() or vc.is_paused():
                queue.append(track)
                await interaction.edit_original_response(content=None, embed=discord.Embed(description=f"📋 เพิ่มใน Queue: **{title}** (ตำแหน่ง #{len(queue)})", color=discord.Color.blurple()))
            else:
                source = discord.PCMVolumeTransformer(
                    discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS),
                    volume=DEFAULT_VOLUME
                )
                view = PlayerView(
                    interaction.guild,
                    interaction.channel,
                    loop_getter(),
                    current_track=track
                )
                active_views[interaction.guild.id] = view
                _guild = interaction.guild
                _channel = interaction.channel
                _loop = loop_getter()
                _track = track
                def _after(e, g=_guild, ch=_channel, lp=_loop, t=_track):
                    asyncio.run_coroutine_threadsafe(play_next(g, ch, lp, current_track=t), lp)
                vc.play(source, after=_after)
                embed = make_now_playing_embed(title, duration, requester, thumbnail)
                await interaction.edit_original_response(content=None, embed=embed, view=view)
                msg = await interaction.original_response()
                view.now_playing_msg = msg

        except Exception as e:
            print(f"PLAY ERROR: {e}")
            try:
                err = str(e)
                if "Sign in" in err or "cookies" in err.lower():
                    msg = "❌ YouTube บล็อกการเข้าถึง กรุณาลองใหม่อีกครั้ง"
                else:
                    msg = "❌ เกิดข้อผิดพลาด กรุณาลองใหม่"
                await interaction.edit_original_response(content=None, embed=discord.Embed(description=msg, color=discord.Color.red()))
            except Exception:
                pass

    @tree.command(name="skip", description="ข้ามเพลงปัจจุบัน")
    async def slash_skip(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            try:
                await interaction.response.defer()
            except Exception:
                pass
            old_view = active_views.get(interaction.guild.id)
            if old_view:
                await old_view.delete_now_playing()
            vc.stop()
        else:
            await safe_respond(interaction, "❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @tree.command(name="queue", description="ดู Queue เพลง")
    async def slash_queue(interaction: discord.Interaction):
        queue = get_queue(interaction.guild.id)
        if not queue:
            return await safe_respond(interaction, "Queue ว่างเปล่า", ephemeral=True)
        lines = [f"`{i+1}.` {t[1]}" for i, t in enumerate(queue)]
        embed = discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=discord.Color.blurple())
        await safe_respond(interaction, embed=embed, ephemeral=True)

    @tree.command(name="stop", description="หยุดเพลงและล้าง Queue")
    async def slash_stop(interaction: discord.Interaction):
        if interaction.guild.voice_client:
            old_view = active_views.pop(interaction.guild.id, None)
            if old_view:
                await old_view.delete_now_playing()
            queues[interaction.guild.id] = []
            histories[interaction.guild.id] = []
            interaction.guild.voice_client.stop()
            await interaction.guild.voice_client.disconnect()
            await safe_respond(interaction, "⏹️ หยุดเพลงและออกจาก Voice Channel แล้ว", ephemeral=True)
        else:
            await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)

    @tree.command(name="volume", description="ปรับระดับเสียง (0-100)")
    @app_commands.describe(vol="ระดับเสียง 0-100")
    async def slash_volume(interaction: discord.Interaction, vol: int):
        if not interaction.guild.voice_client:
            return await safe_respond(interaction, "❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)
        if not 0 <= vol <= 100:
            return await safe_respond(interaction, "❌ ระดับเสียงต้องอยู่ระหว่าง 0-100", ephemeral=True)
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.volume = vol / 100
        await safe_respond(interaction, f"🔊 ระดับเสียง: {vol}%", ephemeral=True)

    @tree.command(name="clear", description="ลบข้อความในช่อง")
    @app_commands.describe(amount="จำนวนข้อความที่ต้องการลบ (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_clear(interaction: discord.Interaction, amount: int):
        if not 1 <= amount <= 100:
            return await safe_respond(interaction, "❌ ใส่จำนวน 1-100 เท่านั้น", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.edit_original_response(content=f"🗑️ ลบข้อความไปแล้ว {len(deleted)} ข้อความ")

    @slash_clear.error
    async def slash_clear_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await safe_respond(interaction, "❌ คุณไม่มีสิทธิ์ลบข้อความ", ephemeral=True)