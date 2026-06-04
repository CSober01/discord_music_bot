import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv
import slashcommands

load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = "!"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ผูก slash commands จาก slashcommands.py
slashcommands.register(bot.tree, lambda: bot.loop)

YDL_OPTIONS = slashcommands.YDL_OPTIONS
FFMPEG_OPTIONS = slashcommands.FFMPEG_OPTIONS
get_queue = slashcommands.get_queue
fetch_track = slashcommands.fetch_track
queues = slashcommands.queues


async def play_next_ctx(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)
    if queue:
        url, title = queue.pop(0)
        source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
        view = slashcommands.PlayerView(ctx.guild, ctx.channel, bot.loop)
        ctx.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(play_next_ctx(ctx), bot.loop),
        )
        await ctx.send(f"กำลังเล่น: {title}", view=view)
    else:
        await ctx.send("เล่นเพลงครบ Queue แล้ว!")


@bot.event
async def on_ready():
    print(f"✅ บอทออนไลน์แล้ว: {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{PREFIX}play | /play 🎵",
        )
    )


@bot.command(name="play", aliases=["p"])
async def play(ctx: commands.Context, *, query: str):
    if not ctx.author.voice:
        return await ctx.send("❌ กรุณาเข้า Voice Channel ก่อนนะ!")
    voice_channel = ctx.author.voice.channel
    if not ctx.voice_client:
        await voice_channel.connect()
    elif ctx.voice_client.channel != voice_channel:
        await ctx.voice_client.move_to(voice_channel)
    async with ctx.typing():
        url, title = await asyncio.to_thread(fetch_track, query)
    queue = get_queue(ctx.guild.id)
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        queue.append((url, title))
        await ctx.send(f"📋 เพิ่มใน Queue: **{title}** (ตำแหน่ง #{len(queue)})")
    else:
        source = discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS)
        view = slashcommands.PlayerView(ctx.guild, ctx.channel, bot.loop)
        ctx.voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next_ctx(ctx), bot.loop))
        await ctx.send(f"กำลังเล่น: {title}", view=view)


@bot.command(name="skip", aliases=["s"])
async def skip(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ ข้ามเพลงแล้ว!")
    else:
        await ctx.send("❌ ไม่มีเพลงที่กำลังเล่นอยู่")


@bot.command(name="pause")
async def pause(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ หยุดเพลงชั่วคราว")
    else:
        await ctx.send("❌ ไม่มีเพลงที่กำลังเล่นอยู่")


@bot.command(name="resume", aliases=["r"])
async def resume(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ เล่นเพลงต่อแล้ว!")
    else:
        await ctx.send("❌ เพลงไม่ได้ถูกหยุดอยู่")


@bot.command(name="queue", aliases=["q"])
async def queue_list(ctx: commands.Context):
    queue = get_queue(ctx.guild.id)
    if not queue:
        return await ctx.send("📋 Queue ว่างเปล่า")
    lines = [f"`{i+1}.` {title}" for i, (_, title) in enumerate(queue)]
    embed = discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=discord.Color.blurple())
    await ctx.send(embed=embed)


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    if ctx.voice_client:
        queues[ctx.guild.id] = []
        ctx.voice_client.stop()
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ หยุดเพลงและออกจาก Voice Channel แล้ว")
    else:
        await ctx.send("❌ บอทไม่ได้อยู่ใน Voice Channel")


@bot.command(name="nowplaying", aliases=["np"])
async def now_playing(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        await ctx.send("🎵 กำลังเล่นเพลงอยู่! ใช้ `!queue` เพื่อดูรายการถัดไป")
    else:
        await ctx.send("❌ ไม่มีเพลงที่กำลังเล่นอยู่")


@bot.command(name="volume", aliases=["vol"])
async def volume(ctx: commands.Context, vol: int):
    if not ctx.voice_client:
        return await ctx.send("❌ บอทไม่ได้อยู่ใน Voice Channel")
    if not 0 <= vol <= 100:
        return await ctx.send("❌ ระดับเสียงต้องอยู่ระหว่าง 0-100")
    if ctx.voice_client.source:
        ctx.voice_client.source = discord.PCMVolumeTransformer(ctx.voice_client.source, volume=vol / 100)
        await ctx.send(f"🔊 ระดับเสียง: {vol}%")


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ ใส่ข้อมูลไม่ครบ! ลองพิมพ์ `{PREFIX}help` เพื่อดูวิธีใช้")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"⚠️ เกิดข้อผิดพลาด: `{error}`")


bot.run(BOT_TOKEN)