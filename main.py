import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta
import asyncio

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True  # Необхідно для відстеження голосових каналів

bot = commands.Bot(command_prefix="!", intents=intents)

# Словник для зберігання часу підключення користувачів
voice_time_tracker = {}
# Словник для зберігання каналів, які потрібно відстежувати
tracked_channels = {}

@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно підключений до Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації команд: {e}")
    check_voice_activity.start()  # Запускаємо фонову задачу

@tasks.loop(minutes=1)
async def check_voice_activity():
    current_time = datetime.utcnow()
    for guild_id, channel_id in tracked_channels.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
            
        channel = guild.get_channel(channel_id)
        if not channel:
            continue
            
        for member in channel.members:
            if member.bot:
                continue
                
            if member.id not in voice_time_tracker:
                voice_time_tracker[member.id] = current_time
            else:
                time_in_channel = current_time - voice_time_tracker[member.id]
                if time_in_channel > timedelta(minutes=15):  # 15 хвилин - ліміт
                    try:
                        await member.send(
                            f"🔔 Ви знаходитесь у голосовому каналі {channel.name} вже більше 15 хвилин. "
                            f"Будь ласка, зробіть перерву або перейдіть до іншого каналу, щоб не перевантажувати сервер."
                        )
                        # Скидаємо лічильник після попередження
                        voice_time_tracker[member.id] = current_time
                    except Exception as e:
                        print(f"Не вдалося надіслати повідомлення {member}: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    # Якщо користувач вийшов з каналу або перейшов до іншого
    if before.channel and before.channel.id in tracked_channels.values():
        if member.id in voice_time_tracker:
            del voice_time_tracker[member.id]

# Нова команда для додавання каналу до відстеження
@bot.tree.command(name="track_voice_channel", description="Додає голосовий канал для відстеження активності")
@app_commands.describe(channel="Голосовий канал для відстеження")
async def track_voice_channel(interaction: discord.Interaction, channel: discord.VoiceChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    tracked_channels[interaction.guild_id] = channel.id
    await interaction.response.send_message(
        f"🔊 Голосовий канал {channel.mention} тепер відстежується. "
        f"Користувачі отримуватимуть сповіщення після 30 хвилин перебування.",
        ephemeral=True
    )

# Інші існуючі команди (remove_default_only, remove_by_role, list_no_roles, show_role_users)
# ... (ваш попередній код команд) ...

@bot.event
async def on_disconnect():
    check_voice_activity.cancel()

bot.run(os.getenv('DISCORD_TOKEN'))
