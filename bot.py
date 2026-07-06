import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv
import slashcommands

load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_TOKEN", "").strip().strip('"')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

slashcommands.register(bot.tree, lambda: bot.loop)


@bot.event
async def on_ready():
    print(f"✅ บอทออนไลน์แล้ว: {bot.user}")
    # ลบข้อความเก่าได้เมื่อรีสตาร์ทแล้ว
    await slashcommands.cleanup_old_messages(bot)
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="/play 🎵",
        )
    )
    # ไม่ sync คำสั่งอัตโนมัติที่นี่แล้ว — ถ้าต้องการอัปเดต/เพิ่มคำสั่งใหม่ ให้รัน `python register.py` เอง


bot.run(BOT_TOKEN)