"""
register.py — รันครั้งเดียวเพื่อ register slash commands กับ Discord
รันด้วย: python register.py
"""

import discord
from discord.ext import commands
import asyncio
import os
from dotenv import load_dotenv
import slashcommands

load_dotenv()
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ผูก slash commands เข้ากับ tree
slashcommands.register(bot.tree, lambda: bot.loop)


@bot.event
async def on_ready():
    print(f"🔗 เชื่อมต่อแล้ว: {bot.user}\n")

    # Sync แบบ global เสมอ — คำสั่งจะขึ้นทุกเซิร์ฟที่บอทอยู่ โดยไม่ต้องระบุ GUILD_ID
    # (Discord อาจใช้เวลาสูงสุด ~1 ชั่วโมงในการกระจายคำสั่งใหม่ไปทุกเซิร์ฟ)
    synced = await bot.tree.sync()
    print(f"🌐 Synced {len(synced)} slash command(s) → Global (รอ Discord สูงสุด ~1 ชั่วโมง)")

    print("\nคำสั่งที่ register แล้ว:")
    for cmd in synced:
        print(f"  /{cmd.name}")

    print("\n✅ เสร็จแล้ว! ปิดหน้าต่างนี้ได้เลย")
    await bot.close()


asyncio.run(bot.start(BOT_TOKEN))