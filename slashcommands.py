"""
slashcommands.py — ไฟล์กลางสำหรับ slash commands
ถูก import โดยทั้ง bot.py และ register.py
"""

import discord
from discord import app_commands
import yt_dlp
import asyncio

YDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

DEFAULT_VOLUME = 0.3  # 30%

queues: dict[int, list] = {}
histories: dict[int, list] = {}

# เก็บ PlayerView ที่กำลังเล่นอยู่ต่อ guild
active_views: dict[int, "PlayerView"] = {}


def get_queue(guild_id: int) -> list:
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]


def get_history(guild_id: int) -> list:
    if guild_id not in histories:
        histories[guild_id] = []
    return histories[guild_id]


def fetch_track(query: str):
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        duration = info.get("duration", 0)
        minutes, seconds = divmod(int(duration), 60)
        duration_str = f"{minutes}:{seconds:02d}"
        return info["url"], info.get("title", "Unknown"), duration_str


def make_now_playing_embed(title: str, duration: str, requester: discord.Member = None) -> discord.Embed:
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{title}**",
        color=discord.Color.dark_blue(),
    )
    embed.add_field(name="ความยาว", value=duration, inline=True)
    if requester:
        embed.add_field(name="ขอโดย", value=requester.mention, inline=True)
    return embed


# ─────────────────────────────────────────────
#  Player Buttons View
# ─────────────────────────────────────────────

class PlayerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, channel: discord.TextChannel, loop, current_track=None):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop
        self.current_track = current_track  # (url, title, duration, requester)
        self.now_playing_msg: discord.Message | None = None

    async def delete_now_playing(self):
        if self.now_playing_msg:
            try:
                await self.now_playing_msg.delete()
            except Exception:
                pass
            self.now_playing_msg = None

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)
        history = get_history(self.guild.id)
        if not history:
            return await interaction.response.send_message("❌ ไม่มีประวัติเพลงก่อนหน้า", ephemeral=True)

        prev_track = history.pop()
        queue = get_queue(self.guild.id)
        if self.current_track:
            queue.insert(0, self.current_track)

        await interaction.response.defer()
        await self.delete_now_playing()

        url, title, duration, requester = prev_track
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = PlayerView(self.guild, self.channel, self.loop, current_track=prev_track)
        active_views[self.guild.id] = view

        vc.stop()
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
            play_next(self.guild, self.channel, self.loop, current_track=prev_track), self.loop
        ))
        embed = make_now_playing_embed(title, duration, requester)
        msg = await self.channel.send(embed=embed, view=view)
        view.now_playing_msg = msg

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸"
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc and vc.is_playing():
            await interaction.response.defer()
            await self.delete_now_playing()
            vc.stop()
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.primary)
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        queue = get_queue(self.guild.id)
        if not queue:
            return await interaction.response.send_message("📋 Queue ว่างเปล่า", ephemeral=True)
        lines = [f"`{i+1}.` {title}" for i, (_, title, *_rest) in enumerate(queue)]
        embed = discord.Embed(
            title="📋 Queue เพลง",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger)
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
            await interaction.response.edit_message(view=self)
            await self.delete_now_playing()
        else:
            await interaction.response.send_message("❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)


# ─────────────────────────────────────────────
#  play_next
# ─────────────────────────────────────────────

async def play_next(guild: discord.Guild, channel: discord.TextChannel, loop, current_track=None):
    # ลบ Now Playing ของเพลงก่อนหน้าผ่าน active_views
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
        url, title, duration, requester = queue.pop(0)
        track = (url, title, duration, requester)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = PlayerView(guild, channel, loop, current_track=track)
        active_views[guild.id] = view
        guild.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(guild, channel, loop, current_track=track), loop
            ),
        )
        embed = make_now_playing_embed(title, duration, requester)
        msg = await channel.send(embed=embed, view=view)
        view.now_playing_msg = msg
    else:
        await channel.send("เล่นเพลงครบ Queue แล้ว!")


# ─────────────────────────────────────────────
#  Slash Commands
# ─────────────────────────────────────────────

def register(tree: app_commands.CommandTree, loop_getter):

    @tree.command(name="play", description="เล่นเพลงจาก YouTube")
    @app_commands.describe(query="ชื่อเพลง หรือ URL YouTube")
    async def slash_play(interaction: discord.Interaction, query: str):
        if not interaction.user.voice:
            return await interaction.response.send_message("❌ กรุณาเข้า Voice Channel ก่อนนะ!", ephemeral=True)

        await interaction.response.send_message(f"🔍 กำลังค้นหา **{query}** ...")
        searching_msg = await interaction.original_response()

        voice_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if not vc:
            vc = await voice_channel.connect()
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)

        url, title, duration = await asyncio.to_thread(fetch_track, query)
        requester = interaction.user
        track = (url, title, duration, requester)

        try:
            await searching_msg.delete()
        except Exception:
            pass

        queue = get_queue(interaction.guild.id)
        if vc.is_playing() or vc.is_paused():
            queue.append(track)
            await interaction.channel.send(f"📋 เพิ่มใน Queue: **{title}** (ตำแหน่ง #{len(queue)})")
        else:
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
            view = PlayerView(interaction.guild, interaction.channel, loop_getter(), current_track=track)
            active_views[interaction.guild.id] = view
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(interaction.guild, interaction.channel, loop_getter(), current_track=track), loop_getter()
            ))
            embed = make_now_playing_embed(title, duration, requester)
            msg = await interaction.channel.send(embed=embed, view=view)
            view.now_playing_msg = msg

    @tree.command(name="skip", description="ข้ามเพลงปัจจุบัน")
    async def slash_skip(interaction: discord.Interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            await interaction.response.defer()
            old_view = active_views.get(interaction.guild.id)
            if old_view:
                await old_view.delete_now_playing()
            vc.stop()
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @tree.command(name="queue", description="ดู Queue เพลง")
    async def slash_queue(interaction: discord.Interaction):
        queue = get_queue(interaction.guild.id)
        if not queue:
            return await interaction.response.send_message("Queue ว่างเปล่า", ephemeral=True)
        lines = [f"`{i+1}.` {title}" for i, (_, title, *_rest) in enumerate(queue)]
        embed = discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
            await interaction.response.send_message("⏹️ หยุดเพลงและออกจาก Voice Channel แล้ว", ephemeral=True)
        else:
            await interaction.response.send_message("❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)

    @tree.command(name="volume", description="ปรับระดับเสียง (0-100)")
    @app_commands.describe(vol="ระดับเสียง 0-100")
    async def slash_volume(interaction: discord.Interaction, vol: int):
        if not interaction.guild.voice_client:
            return await interaction.response.send_message("❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)
        if not 0 <= vol <= 100:
            return await interaction.response.send_message("❌ ระดับเสียงต้องอยู่ระหว่าง 0-100", ephemeral=True)
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source = discord.PCMVolumeTransformer(
                interaction.guild.voice_client.source, volume=vol / 100
            )
        await interaction.response.send_message(f"🔊 ระดับเสียง: {vol}%", ephemeral=True)

    @tree.command(name="clear", description="ลบข้อความในช่อง")
    @app_commands.describe(amount="จำนวนข้อความที่ต้องการลบ (1-100)")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def slash_clear(interaction: discord.Interaction, amount: int):
        if not 1 <= amount <= 100:
            return await interaction.response.send_message("❌ ใส่จำนวน 1-100 เท่านั้น", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🗑️ ลบข้อความไปแล้ว {len(deleted)} ข้อความ", ephemeral=True)

    @slash_clear.error
    async def slash_clear_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ลบข้อความ", ephemeral=True)