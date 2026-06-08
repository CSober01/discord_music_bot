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
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="/play 🎵",
        )
    )
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"Sync error: {e}")


bot.run(BOT_TOKEN)