import discord
from discord import app_commands
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно підключений до Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації команд: {e}")

@bot.tree.command(name="remove_default_only", description="Видаляє користувачів, які мають тільки роль @everyone")
async def remove_default_only(interaction: discord.Interaction):
    # ... ваш код ...

# ... інші команди ...

bot.run(os.getenv('DISCORD_TOKEN'))
