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

queues: dict[int, list] = {}


def get_queue(guild_id: int) -> list:
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]


def fetch_track(query: str):
    with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info["url"], info.get("title", "Unknown")


# ─────────────────────────────────────────────
#  Player Buttons View
# ─────────────────────────────────────────────

class PlayerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, channel: discord.TextChannel, loop):
        super().__init__(timeout=None)
        self.guild = guild
        self.channel = channel
        self.loop = loop

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
            vc.stop()
            await interaction.response.defer()
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self.guild.voice_client
        if vc:
            queues[self.guild.id] = []
            vc.stop()
            await vc.disconnect()
            # ปิดปุ่มทั้งหมดหลัง stop
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message("❌ บอทไม่ได้อยู่ใน Voice Channel", ephemeral=True)


# ─────────────────────────────────────────────
#  play_next
# ─────────────────────────────────────────────

async def play_next(guild: discord.Guild, channel: discord.TextChannel, loop):
    queue = get_queue(guild.id)
    if queue:
        url, title = queue.pop(0)
        source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
        view = PlayerView(guild, channel, loop)
        guild.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(guild, channel, loop), loop
            ),
        )
        await channel.send(f"กำลังเล่น: {title}", view=view)
    else:
        await channel.send("เล่นเพลงครบ Queue แล้ว!")


# ─────────────────────────────────────────────
#  Slash Commands
# ─────────────────────────────────────────────

def register(tree: app_commands.CommandTree, loop_getter):

    @tree.command(name="play", description="เล่นเพลงจาก YouTube")
    @app_commands.describe(query="ชื่อเพลง หรือ URL YouTube")
    async def slash_play(interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.voice:
            return await interaction.followup.send("❌ กรุณาเข้า Voice Channel ก่อนนะ!", ephemeral=True)
        voice_channel = interaction.user.voice.channel
        vc = interaction.guild.voice_client
        if not vc:
            vc = await voice_channel.connect()
        elif vc.channel != voice_channel:
            await vc.move_to(voice_channel)
        url, title = await asyncio.to_thread(fetch_track, query)
        queue = get_queue(interaction.guild.id)
        if vc.is_playing() or vc.is_paused():
            queue.append((url, title))
            await interaction.followup.send(f"เพิ่มใน Queue: {title} (ตำแหน่ง #{len(queue)})", ephemeral=True)
        else:
            source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
            view = PlayerView(interaction.guild, interaction.channel, loop_getter())
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next(interaction.guild, interaction.channel, loop_getter()), loop_getter()
            ))
            await interaction.followup.send("_ _", ephemeral=True)
            await interaction.channel.send(f"กำลังเล่น: {title}", view=view)

    @tree.command(name="skip", description="ข้ามเพลงปัจจุบัน")
    async def slash_skip(interaction: discord.Interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            interaction.guild.voice_client.stop()
            await interaction.response.defer()
        else:
            await interaction.response.send_message("❌ ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)


    @tree.command(name="queue", description="ดู Queue เพลง")
    async def slash_queue(interaction: discord.Interaction):
        queue = get_queue(interaction.guild.id)
        if not queue:
            return await interaction.response.send_message("Queue ว่างเปล่า", ephemeral=True)
        lines = [f"`{i+1}.` {title}" for i, (_, title) in enumerate(queue)]
        embed = discord.Embed(title="Queue เพลง", description="\n".join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="stop", description="หยุดเพลงและล้าง Queue")
    async def slash_stop(interaction: discord.Interaction):
        if interaction.guild.voice_client:
            queues[interaction.guild.id] = []
            interaction.guild.voice_client.stop()
            await interaction.guild.voice_client.disconnect()
            await interaction.response.send_message("หยุดเพลงและออกจาก Voice Channel แล้ว", ephemeral=True)
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