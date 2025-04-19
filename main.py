import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True  # Для відстеження голосових каналів

bot = commands.Bot(command_prefix="!", intents=intents)

# Словники для відстеження активності
voice_time_tracker = {}
tracked_channels = {}  # {guild_id: {"voice_channel": voice_channel_id, "log_channel": log_channel_id}}
warning_sent = set()   # Для відстеження вже надісланих попереджень

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
            
        voice_channel = guild.get_channel(data["voice_channel"])
        log_channel = guild.get_channel(data["log_channel"])
        
        if not voice_channel or not log_channel:
            continue
            
        for member in voice_channel.members:
            if member.bot:
                continue
                
            member_key = f"{guild_id}_{member.id}"
            
            if member_key not in voice_time_tracker:
                voice_time_tracker[member_key] = current_time
                warning_sent.discard(member_key)
                continue
                
            time_in_channel = current_time - voice_time_tracker[member_key]
            
            # Попередження через 10 хвилин
            if time_in_channel > timedelta(minutes=10) and member_key not in warning_sent:
                try:
                    await member.send(
                        "⚠️ Ви знаходитесь на каналі для неактивних учасників більше 10 хвилин. "
                        "✅Будьте активні або вийдіть, інакше ви будете автоматично відключені."
                    )
                    warning_sent.add(member_key)
                except Exception as e:
                    print(f"Не вдалося надіслати попередження {member}: {e}")
            
            # Відключення через 15 хвилин
            if time_in_channel > timedelta(minutes=15):
                try:
                    await member.move_to(None, reason="Автоматичне відключення за неактивність")
                    await log_channel.send(
                        f"🔴 {member.mention} був відключений з {voice_channel.mention} "
                        f"через надто тривале перебування ({time_in_channel.seconds//60} хв)."
                    )
                    del voice_time_tracker[member_key]
                    warning_sent.discard(member_key)
                except Exception as e:
                    print(f"Не вдалося відключити {member}: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.id in [data["voice_channel"] for data in tracked_channels.values()]:
        member_key = f"{member.guild.id}_{member.id}"
        if member_key in voice_time_tracker:
            del voice_time_tracker[member_key]
            warning_sent.discard(member_key)

# ========== НОВА КОМАНДА ==========
@bot.tree.command(name="track_voice", description="Налаштувати відстеження неактивності у голосовому каналі")
@app_commands.describe(
    voice_channel="Голосовий канал для відстеження",
    log_channel="Канал для повідомлень про відключення"
)
async def track_voice(interaction: discord.Interaction, 
                     voice_channel: discord.VoiceChannel, 
                     log_channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    tracked_channels[interaction.guild_id] = {
        "voice_channel": voice_channel.id,
        "log_channel": log_channel.id
    }
    
    await interaction.response.send_message(
        f"🔊 Відстежування голосового каналу {voice_channel.mention} активовано.\n"
        f"📝 Повідомлення про відключення будуть надходити у {log_channel.mention}.\n"
        "Користувачі отримають попередження через 10 хвилин та будуть відключені через 15 хвилин.",
        ephemeral=True
    )

# ========== ІСНУЮЧІ КОМАНДИ (БЕЗ ЗМІН) ==========
@bot.tree.command(name="remove_default_only", description="Видаляє користувачів, які мають тільки роль @everyone")
async def remove_default_only(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    deleted_count = 0
    
    for member in guild.members:
        if not member.bot and len(member.roles) == 1:
            try:
                await member.kick(reason="Має тільки роль @everyone")
                deleted_count += 1
            except Exception as e:
                print(f"Не вдалося видалити {member}: {e}")
    
    await interaction.followup.send(f"Видалено {deleted_count} користувачів, які мали тільки роль @everyone.", ephemeral=True)

@bot.tree.command(name="remove_by_role", description="Видаляє всіх користувачів з обраною роллю")
@app_commands.describe(role="Роль для видалення")
async def remove_by_role(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    if role == interaction.guild.default_role:
        await interaction.response.send_message("Не можна видаляти всіх користувачів сервера.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    deleted_count = 0
    
    for member in role.members:
        if not member.bot:
            try:
                await member.kick(reason=f"Видалення користувачів ролі {role.name}")
                deleted_count += 1
            except Exception as e:
                print(f"Не вдалося видалити {member}: {e}")
    
    await interaction.followup.send(f"Видалено {deleted_count} користувачів з роллю {role.name}.", ephemeral=True)

@bot.tree.command(name="list_no_roles", description="Виводить список користувачів без жодних ролей (крім @everyone)")
async def list_no_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    members_without_roles = []
    
    for member in interaction.guild.members:
        if not member.bot and len(member.roles) == 1:
            members_without_roles.append(f"{member.display_name} ({member.id})")
    
    if not members_without_roles:
        await interaction.followup.send("На сервері немає користувачів без ролей.", ephemeral=True)
        return
    
    chunks = [members_without_roles[i:i + 20] for i in range(0, len(members_without_roles), 20)]
    
    for i, chunk in enumerate(chunks):
        message = f"Користувачі без ролей (частина {i+1}):\n" + "\n".join(chunk)
        if i == 0:
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)

@bot.tree.command(name="show_role_users", description="Показує список користувачів з обраною роллю")
@app_commands.describe(role="Роль для перегляду")
async def show_role_users(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    
    members = [f"{member.mention} ({member.display_name})" 
               for member in role.members 
               if not member.bot]
    
    if not members:
        await interaction.followup.send(f"🔍 Немає користувачів з роллю **{role.name}**.", ephemeral=True)
        return
    
    chunk_size = 15
    for i in range(0, len(members), chunk_size):
        chunk = members[i:i + chunk_size]
        embed = discord.Embed(
            title=f"👥 Користувачі з роллю {role.name} ({len(members)} всього)",
            description="\n".join(chunk),
            color=role.color
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

# Запуск бота
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Не знайдено змінну середовища DISCORD_TOKEN")

bot.run(TOKEN)
