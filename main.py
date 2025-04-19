import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True  # Додаємо відстеження голосових каналів

bot = commands.Bot(command_prefix="!", intents=intents)

# Словники для відстеження активності
voice_time_tracker = {}
tracked_channels = {}

@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно підключений до Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації команд: {e}")
    check_voice_activity.start()

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
                if time_in_channel > timedelta(minutes=30):  # Ліміт 30 хвилин
                    try:
                        await member.send(
                            f"🔔 Ви знаходитесь у голосовому каналі {channel.name} вже більше 30 хвилин. "
                            "Будь ласка, зробіть перерву, щоб не перевантажувати сервер."
                        )
                        voice_time_tracker[member.id] = current_time  # Скидаємо таймер
                    except Exception as e:
                        print(f"Не вдалося надіслати повідомлення {member}: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.id in tracked_channels.values():
        if member.id in voice_time_tracker:
            del voice_time_tracker[member.id]

### Нова команда для відстеження голосових каналів ###
@bot.tree.command(name="track_voice", description="Відстежувати перебування у голосовому каналі")
@app_commands.describe(channel="Голосовий канал для відстеження")
async def track_voice(interaction: discord.Interaction, channel: discord.VoiceChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    tracked_channels[interaction.guild_id] = channel.id
    await interaction.response.send_message(
        f"🔊 Відстежування голосового каналу {channel.mention} активовано. "
        "Користувачі отримуватимуть сповіщення після 30 хвилин безперервного перебування.",
        ephemeral=True
    )

### Існуючі команди (без змін) ###
@bot.tree.command(name="remove_default_only", description="Видаляє користувачів, які мають тільки роль @everyone")
async def remove_default_only(interaction: discord.Interaction):
    # ... (ваш існуючий код) ...

@bot.tree.command(name="remove_by_role", description="Видаляє всіх користувачів з обраною роллю")
@app_commands.describe(role="Роль для видалення")
async def remove_by_role(interaction: discord.Interaction, role: discord.Role):
    # ... (ваш існуючий код) ...

@bot.tree.command(name="list_no_roles", description="Виводить список користувачів без ролей (крім @everyone)")
async def list_no_roles(interaction: discord.Interaction):
    # ... (ваш існуючий код) ...

@bot.tree.command(name="show_role_users", description="Показує список користувачів з обраною роллю")
@app_commands.describe(role="Роль для перегляду")
async def show_role_users(interaction: discord.Interaction, role: discord.Role):
    # ... (ваш існуючий код) ...

bot.run(os.getenv('DISCORD_TOKEN'))
