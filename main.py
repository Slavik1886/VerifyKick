import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta
import asyncio
from collections import defaultdict

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Системи відстеження
voice_time_tracker = {}
tracked_channels = {}  # Для відстеження неактивності
warning_sent = set()
voice_activity = defaultdict(timedelta)  # Для статистики активності
active_stats_tracking = {}  # Для автоматичної статистики
last_activity_update = datetime.utcnow()

# Допоміжні функції
async def delete_after(message, minutes):
    if minutes <= 0: return
    await asyncio.sleep(minutes * 60)
    try: await message.delete()
    except: pass

# Фонові задачі
@tasks.loop(minutes=1)
async def update_voice_activity():
    global last_activity_update
    now = datetime.utcnow()
    time_elapsed = now - last_activity_update
    last_activity_update = now
    
    for guild in bot.guilds:
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if not member.bot:
                    voice_activity[member.id] += time_elapsed

@tasks.loop(hours=12)
async def send_voice_activity_stats():
    for guild_id, data in active_stats_tracking.items():
        guild = bot.get_guild(guild_id)
        if not guild: continue
            
        channel = guild.get_channel(data["channel_id"])
        if not channel: continue
            
        sorted_users = sorted(voice_activity.items(), key=lambda x: x[1], reverse=True)[:data["count"]]
        if not sorted_users: continue
            
        embed = discord.Embed(
            title=f"🏆 Топ-{data['count']} активних у голосових каналах (12 годин)",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        
        for i, (user_id, time_spent) in enumerate(sorted_users, 1):
            member = guild.get_member(user_id)
            if member:
                hours, remainder = divmod(time_spent.total_seconds(), 3600)
                minutes = remainder // 60
                embed.add_field(
                    name=f"{i}. {member.display_name}",
                    value=f"{int(hours)} год. {int(minutes)} хв.",
                    inline=False
                )
        
        try: 
            await channel.send(embed=embed)
            voice_activity.clear()  # Очищаємо статистику після відправки
        except: pass

@tasks.loop(minutes=1)
async def check_voice_activity():
    current_time = datetime.utcnow()
    for guild_id, data in tracked_channels.items():
        guild = bot.get_guild(guild_id)
        if not guild: continue
            
        voice_channel = guild.get_channel(data["voice_channel"])
        log_channel = guild.get_channel(data["log_channel"])
        if not voice_channel or not log_channel: continue
            
        for member in voice_channel.members:
            if member.bot: continue
                
            member_key = f"{guild_id}_{member.id}"
            
            if member_key not in voice_time_tracker:
                voice_time_tracker[member_key] = current_time
                warning_sent.discard(member_key)
                continue
                
            time_in_channel = current_time - voice_time_tracker[member_key]
            
            # Попередження через 10 хвилин
            if time_in_channel > timedelta(minutes=10) and member_key not in warning_sent:
                try:
                    await member.send("⚠️ Ви в голосовому каналі вже 10+ хвилин. Будьте активні!")
                    warning_sent.add(member_key)
                except: pass
            
            # Відключення через 15 хвилин
            if time_in_channel > timedelta(minutes=15):
                try:
                    await member.move_to(None)
                    msg = await log_channel.send(f"🔴 {member.mention} відключено за неактивність")
                    bot.loop.create_task(delete_after(msg, data["delete_after"]))
                    del voice_time_tracker[member_key]
                    warning_sent.discard(member_key)
                except: pass

# Події
@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.id in [data["voice_channel"] for data in tracked_channels.values()]:
        member_key = f"{member.guild.id}_{member.id}"
        if member_key in voice_time_tracker:
            del voice_time_tracker[member_key]
            warning_sent.discard(member_key)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} онлайн!')
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації: {e}")
    
    check_voice_activity.start()
    update_voice_activity.start()
    if active_stats_tracking:
        send_voice_activity_stats.start()

# ========== КОМАНДИ ==========

@bot.tree.command(name="track_voice", description="Налаштувати відстеження неактивності у голосових каналах")
@app_commands.describe(
    voice_channel="Голосовий канал для відстеження",
    log_channel="Канал для повідомлень про відключення",
    delete_after="Через скільки хвилин видаляти повідомлення (0 - не видаляти)"
)
async def track_voice(interaction: discord.Interaction, 
                     voice_channel: discord.VoiceChannel, 
                     log_channel: discord.TextChannel,
                     delete_after: int = 5):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    
    tracked_channels[interaction.guild_id] = {
        "voice_channel": voice_channel.id,
        "log_channel": log_channel.id,
        "delete_after": delete_after
    }
    
    await interaction.response.send_message(
        f"🔊 Відстежування {voice_channel.mention} активовано\n"
        f"📝 Логування у {log_channel.mention}\n"
        f"⏳ Автовидалення через {delete_after} хв\n"
        "🔔 Попередження через 10 хв, відключення через 15 хв",
        ephemeral=True
    )

@bot.tree.command(name="voice_stats", description="Автоматична статистика активності у голосових каналах")
@app_commands.describe(
    channel="Канал для статистики",
    count="Кількість користувачів у топі (1-25)",
    enable="Увімкнути/вимкнути"
)
async def voice_stats(interaction: discord.Interaction,
                    channel: discord.TextChannel,
                    count: app_commands.Range[int, 1, 25] = 10,
                    enable: bool = True):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    
    if enable:
        active_stats_tracking[interaction.guild_id] = {
            "channel_id": channel.id,
            "count": count
        }
        if not send_voice_activity_stats.is_running():
            send_voice_activity_stats.start()
        await interaction.response.send_message(
            f"📊 Статистика увімкнена для {channel.mention}\n"
            f"👥 Топ {count} користувачів\n"
            f"⏱ Оновлення кожні 12 годин",
            ephemeral=True
        )
    else:
        active_stats_tracking.pop(interaction.guild_id, None)
        await interaction.response.send_message("📊 Статистика вимкнена", ephemeral=True)

@bot.tree.command(name="remove_default_only", description="Видаляє користувачів тільки з @everyone")
async def remove_default_only(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    deleted = 0
    
    for member in interaction.guild.members:
        if not member.bot and len(member.roles) == 1:
            try:
                await member.kick(reason="Тільки @everyone")
                deleted += 1
            except: pass
    
    await interaction.followup.send(f"Видалено {deleted} користувачів", ephemeral=True)

@bot.tree.command(name="remove_by_role", description="Видаляє користувачів з роллю")
@app_commands.describe(role="Роль для видалення")
async def remove_by_role(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    
    if role == interaction.guild.default_role:
        await interaction.response.send_message("Не можна видаляти всіх", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    deleted = 0
    
    for member in role.members:
        if not member.bot:
            try:
                await member.kick(reason=f"Видалення ролі {role.name}")
                deleted += 1
            except: pass
    
    await interaction.followup.send(f"Видалено {deleted} користувачів з роллю {role.name}", ephemeral=True)

@bot.tree.command(name="list_no_roles", description="Список користувачів без ролей")
async def list_no_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    members = [f"{m.display_name} ({m.id})" for m in interaction.guild.members 
               if not m.bot and len(m.roles) == 1]
    
    if not members:
        await interaction.followup.send("Немає таких користувачів", ephemeral=True)
        return
    
    chunks = [members[i:i+20] for i in range(0, len(members), 20)]
    for i, chunk in enumerate(chunks):
        msg = f"Користувачі без ролей (частина {i+1}):\n" + "\n".join(chunk)
        await interaction.followup.send(msg, ephemeral=True)

@bot.tree.command(name="show_role_users", description="Показати користувачів з роллю")
@app_commands.describe(role="Роль для перегляду")
async def show_role_users(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    members = [f"{m.mention} ({m.display_name})" for m in role.members if not m.bot]
    
    if not members:
        await interaction.followup.send(f"Немає користувачів з роллю {role.name}", ephemeral=True)
        return
    
    chunks = [members[i:i+15] for i in range(0, len(members), 15)]
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=f"Користувачі з роллю {role.name} ({len(members)})",
            description="\n".join(chunk),
            color=role.color
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

# Запуск бота
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен")
bot.run(TOKEN)
