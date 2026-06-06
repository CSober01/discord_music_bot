import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv
import slashcommands

load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_TOKEN", "").strip().strip('"')
PREFIX = "!"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

slashcommands.register(bot.tree, lambda: bot.loop)

get_ydl_options = slashcommands.get_ydl_options
FFMPEG_OPTIONS = slashcommands.FFMPEG_OPTIONS
DEFAULT_VOLUME = slashcommands.DEFAULT_VOLUME
get_queue = slashcommands.get_queue
fetch_track = slashcommands.fetch_track
queues = slashcommands.queues


async def play_next_ctx(ctx: commands.Context, current_track=None, prev_view=None):
    if prev_view:
        await prev_view.delete_now_playing()

    queue = get_queue(ctx.guild.id)
    if queue:
        url, title, duration, requester, *_thumb = queue.pop(0)
        thumbnail = _thumb[0] if _thumb else None
        track = (url, title, duration, requester, thumbnail)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = slashcommands.PlayerView(ctx.guild, ctx.channel, bot.loop, current_track=track)
        ctx.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next_ctx(ctx, current_track=track, prev_view=view), bot.loop
            ),
        )
        embed = slashcommands.make_now_playing_embed(title, duration, requester, thumbnail)
        msg = await ctx.send(embed=embed, view=view)
        view.now_playing_msg = msg
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

    searching_msg = await ctx.send(f"🔍 กำลังค้นหา **{query}** ...")
    url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query)

    try:
        await searching_msg.delete()
    except Exception:
        pass

    requester = ctx.author
    track = (url, title, duration, requester, thumbnail)
    queue = get_queue(ctx.guild.id)
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        queue.append(track)
        await ctx.send(f"📋 เพิ่มใน Queue: **{title}** (ตำแหน่ง #{len(queue)})")
    else:
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = slashcommands.PlayerView(ctx.guild, ctx.channel, bot.loop, current_track=track)
        ctx.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next_ctx(ctx, current_track=track, prev_view=view), bot.loop
            ),
        )
        embed = slashcommands.make_now_playing_embed(title, duration, requester, thumbnail)
        msg = await ctx.send(embed=embed, view=view)
        view.now_playing_msg = msg


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
    lines = [f"`{i+1}.` {t[1]}" for i, t in enumerate(queue)]
    embed = discord.Embed(title="📋 Queue เพลง", description="\n".join(lines), color=discord.Color.blurple())
    await ctx.send(embed=embed)


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    if ctx.voice_client:
        queues[ctx.guild.id] = []
        slashcommands.histories[ctx.guild.id] = []
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


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ ใส่ข้อมูลไม่ครบ! ลองพิมพ์ `{PREFIX}help` เพื่อดูวิธีใช้")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        await ctx.send(f"⚠️ เกิดข้อผิดพลาด: `{error}`")


bot.run(BOT_TOKEN)