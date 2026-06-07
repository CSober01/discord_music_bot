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


async def play_next_ctx(ctx: commands.Context, prev_view=None):
    if prev_view:
        await prev_view.delete_now_playing()
    await slashcommands.play_next(ctx.guild, ctx.channel, bot.loop)


@bot.event
async def on_ready():
    print(f"✅ บอทออนไลน์แล้ว: {bot.user}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name=f"{PREFIX}p | /play 🎵",
        )
    )


@bot.command(name="p")
async def play(ctx: commands.Context, *, query: str):
    if not ctx.author.voice:
        return await ctx.send("❌ กรุณาเข้า Voice Channel ก่อนนะ!")
    voice_channel = ctx.author.voice.channel
    if not ctx.voice_client:
        await voice_channel.connect()
    elif ctx.voice_client.channel != voice_channel:
        await ctx.voice_client.move_to(voice_channel)

    try:
        await ctx.message.delete()
    except Exception:
        pass
    searching_msg = await ctx.send(f"🔍 กำลังค้นหา **{query}** ...")
    url, title, duration, thumbnail = await asyncio.to_thread(fetch_track, query)

    try:
        await searching_msg.delete()
    except Exception:
        pass

    requester = ctx.author
    track = (url, title, duration, requester, thumbnail)
    queue = get_queue(ctx.guild.id)
    queue.append(track)
    if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
        await ctx.send(f"📋 เพิ่มใน Queue #{len(queue)}: **{title}** by {ctx.author.mention}")
    else:
        q_idx = len(queue) - 1
        slashcommands.set_index(ctx.guild.id, q_idx)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS), volume=DEFAULT_VOLUME)
        view = slashcommands.PlayerView(ctx.guild, ctx.channel, bot.loop, current_index=q_idx)
        slashcommands.active_views[ctx.guild.id] = view
        ctx.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(
                play_next_ctx(ctx), bot.loop
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
    embed = slashcommands.make_queue_embed(ctx.guild.id)
    await ctx.send(embed=embed)


@bot.command(name="stop")
async def stop(ctx: commands.Context):
    if ctx.voice_client:
        slashcommands.clear_guild(ctx.guild.id)
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