import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Словники для відстеження активності
voice_time_tracker = {}
tracked_channels = {}  # Зберігає {guild_id: {"channel_id": channel_id, "log_channel": log_channel_id}}
warning_sent = set()  # Для відстеження вже надісланих попереджень

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
    for guild_id, data in tracked_channels.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
            
        channel = guild.get_channel(data["channel_id"])
        log_channel = guild.get_channel(data["log_channel"])
        
        if not channel or not log_channel:
            continue
            
        for member in channel.members:
            if member.bot:
                continue
                
            member_key = f"{guild_id}_{member.id}"
            
            if member_key not in voice_time_tracker:
                voice_time_tracker[member_key] = current_time
                warning_sent.discard(member_key)
                continue
                
            time_in_channel = current_time - voice_time_tracker[member_key]
            
            # Попередження після 10 хвилин
            if time_in_channel > timedelta(minutes=10) and member_key not in warning_sent:
                try:
                    await member.send(
                        "⚠️ Ви знаходитесь у голосовому каналі більше 10 хвилин. "
                        "Будь ласка, будьте активні або вийдіть з каналу, інакше ви будете відключені."
                    )
                    warning_sent.add(member_key)
                except Exception as e:
                    print(f"Не вдалося надіслати попередження {member}: {e}")
            
            # Відключення після 15 хвилин (10+5 на реакцію)
            if time_in_channel > timedelta(minutes=15):
                try:
                    await member.move_to(None, reason="Автоматичне відключення за неактивність")
                    await log_channel.send(
                        f"🔴 Користувача {member.mention} було відключено з каналу {channel.mention} "
                        f"через надто тривале перебування ({time_in_channel.seconds//60} хв)."
                    )
                    del voice_time_tracker[member_key]
                    warning_sent.discard(member_key)
                except Exception as e:
                    print(f"Не вдалося відключити {member}: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.id in [data["channel_id"] for data in tracked_channels.values()]:
        member_key = f"{member.guild.id}_{member.id}"
        if member_key in voice_time_tracker:
            del voice_time_tracker[member_key]
            warning_sent.discard(member_key)

@bot.tree.command(name="track_voice", description="Налаштувати відстеження голосового каналу")
@app_commands.describe(
    voice_channel="Голосовий канал для відстеження",
    log_channel="Канал для логування відключень"
)
async def track_voice(interaction: discord.Interaction, 
                     voice_channel: discord.VoiceChannel, 
                     log_channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    tracked_channels[interaction.guild_id] = {
        "channel_id": voice_channel.id,
        "log_channel": log_channel.id
    }
    
    await interaction.response.send_message(
        f"🔊 Відстежування голосового каналу {voice_channel.mention} активовано.\n"
        f"📝 Логування відключень буде в каналі {log_channel.mention}.\n"
        "Користувачі отримають попередження через 10 хвилин та будуть відключені через 15 хвилин неактивності.",
        ephemeral=True
    )

# ... (інші ваші команди залишаються без змін) ...

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Не знайдено змінну середовища DISCORD_TOKEN")

bot.run(TOKEN)
