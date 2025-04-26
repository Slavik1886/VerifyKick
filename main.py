import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta
import asyncio
from collections import defaultdict
import json
import random
import aiohttp
from typing import Optional, Dict, List
import pytz
import humanize

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True
intents.invites = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Налаштування
WG_API_KEY = os.getenv('WG_API_KEY')
WG_API_URL = "https://api.worldoftanks.eu/wot/"
CLAN_ID = int(os.getenv('CLAN_ID', 500310423))  # ID клану UADragons

# Системи відстеження
voice_time_tracker = {}
tracked_channels = {}
warning_sent = set()
voice_activity = defaultdict(timedelta)
last_activity_update = datetime.utcnow()
active_stats_tracking = {}
stronghold_stats_config = {}

# Система ролей за запрошеннями
invite_roles = {}
invite_cache = {}

def load_invite_data():
    try:
        with open('invite_roles.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_invite_data():
    with open('invite_roles.json', 'w') as f:
        json.dump(invite_roles, f)

invite_roles = load_invite_data()

# ========== ФУНКЦІЇ ДЛЯ РОБОТИ З API ==========

async def get_wg_api_data(endpoint: str, params: dict) -> Optional[dict]:
    """Функція для взаємодії з Wargaming API"""
    params['application_id'] = WG_API_KEY
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{WG_API_URL}{endpoint}", params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('data', data)
                print(f"Помилка API: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"Помилка запиту до API: {e}")
    return None

async def get_stronghold_data() -> Dict:
    """Отримання повної інформації про укріпрайон"""
    params = {
        'clan_id': CLAN_ID,
        'fields': ("clan_name,clan_tag,stronghold_level,stronghold_buildings_level,"
                  "skirmish_statistics,battles_for_strongholds_statistics,"
                  "building_slots.building_title,building_slots.building_level,"
                  "building_slots.reserve_title,command_center_arena_id")
    }
    return await get_wg_api_data("clans/info/", params)

def format_time(timestamp: int) -> str:
    """Форматує timestamp у читабельний час"""
    if not timestamp:
        return "Немає даних"
    return datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M')

# ========== ФОНОВІ ЗАДАЧІ ==========

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
            title=f"🏆 Топ-{data['count']} активних у голосових каналах",
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
            voice_activity.clear()
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
            
            if time_in_channel > timedelta(minutes=10) and member_key not in warning_sent:
                try:
                    await member.send("⚠️ Ви в голосовому каналі вже 10+ хвилин. Будьте активні!")
                    warning_sent.add(member_key)
                except: pass
            
            if time_in_channel > timedelta(minutes=15):
                try:
                    await member.move_to(None)
                    msg = await log_channel.send(f"🔴 {member.mention} відключено за неактивність")
                    bot.loop.create_task(delete_after(msg, data["delete_after"]))
                    del voice_time_tracker[member_key]
                    warning_sent.discard(member_key)
                except: pass

@tasks.loop(minutes=1)
async def stronghold_stats_task():
    """Фонова задача для автоматичного надсилання статистики укріпрайону"""
    if not WG_API_KEY or not CLAN_ID:
        return
        
    kyiv_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kyiv_tz)
    
    for guild_id, config in stronghold_stats_config.items():
        if now.hour == config["hour"] and now.minute == config["minute"]:
            guild = bot.get_guild(guild_id)
            channel = guild.get_channel(config["channel_id"]) if guild else None
            
            if not channel:
                continue
                
            await send_detailed_stronghold_report(channel)

async def send_detailed_stronghold_report(channel):
    """Надсилання деталізованого звіту про укріпрайон"""
    try:
        data = await get_stronghold_data()
        if not data or str(CLAN_ID) not in data:
            return
            
        clan_data = data[str(CLAN_ID)]
        skirmish_stats = clan_data.get('skirmish_statistics', {})
        battles_stats = clan_data.get('battles_for_strongholds_statistics', {})
        buildings = clan_data.get('building_slots', [])
        
        # Основна статистика
        embed = discord.Embed(
            title=f"Щоденний звіт укріпрайону [{clan_data['clan_tag']}]",
            color=discord.Color.green(),
            description=f"Рівень укріпрайону: {clan_data['stronghold_level']}"
        )
        
        # Статистика боїв
        embed.add_field(
            name="⚔️ Статистика боїв (28 днів)",
            value=(
                f"```\n"
                f"Скріміші: {skirmish_stats.get('total_10_in_28d', 0)} боїв | {skirmish_stats.get('win_10_in_28d', 0)} перемог\n"
                f"Оборона: {battles_stats.get('total_10_in_28d', 0)} боїв | {battles_stats.get('win_10_in_28d', 0)} перемог\n"
                f"```"
            ),
            inline=False
        )
        
        # Останні бої
        last_battles = [
            f"Скріміші: {format_time(skirmish_stats.get('last_time_10'))}",
            f"Оборона: {format_time(battles_stats.get('last_time_10'))}"
        ]
        embed.add_field(
            name="🕒 Останні бої",
            value="\n".join(last_battles),
            inline=True
        )
        
        # Будівлі (тільки 4 основні)
        main_buildings = buildings[:4]
        buildings_info = "\n".join(
            f"{b['building_title']} (рівень {b['building_level']})"
            for b in main_buildings
        )
        embed.add_field(
            name="🏗️ Основні будівлі",
            value=buildings_info,
            inline=True
        )
        
        # Додаткові дані
        embed.set_footer(text=f"Оновлено: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        await channel.send(embed=embed)
        
    except Exception as e:
        print(f"Помилка при створенні звіту: {e}")

# ========== КОМАНДИ ДИСКОРД ==========

@bot.tree.command(name="assign_role_to_invite", description="Призначити роль для конкретного запрошення")
@app_commands.describe(
    invite="Код запрошення (без discord.gg/)",
    role="Роль для надання"
)
async def assign_role_to_invite(interaction: discord.Interaction, invite: str, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    try:
        invites = await interaction.guild.invites()
        if not any(inv.code == invite for inv in invites):
            return await interaction.response.send_message("❌ Запрошення не знайдено", ephemeral=True)
        
        guild_id = str(interaction.guild.id)
        if guild_id not in invite_roles:
            invite_roles[guild_id] = {}
        
        invite_roles[guild_id][invite] = role.id
        save_invite_data()
        await update_invite_cache(interaction.guild)
        
        await interaction.response.send_message(
            f"✅ Користувачі, які прийдуть через запрошення `{invite}`, отримуватимуть роль {role.mention}",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Помилка: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="track_voice", description="Налаштувати відстеження неактивності у голосових каналах")
@app_commands.describe(
    voice_channel="Голосовий канал для відстеження",
    log_channel="Канал для повідомлень",
    delete_after="Через скільки хвилин видаляти повідомлення"
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
        f"⏳ Автовидалення через {delete_after} хв",
        ephemeral=True
    )

@bot.tree.command(name="voice_stats", description="Автоматична статистика активності")
@app_commands.describe(
    channel="Канал для статистики",
    count="Кількість користувачів у топі",
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
            f"📊 Статистика увімкнена для {channel.mention}",
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

@bot.tree.command(name="send_embed", description="Надіслати embed-повідомлення у вказаний канал")
@app_commands.describe(
    channel="Текстовий канал для надсилання",
    title="Заголовок повідомлення",
    description="Основний текст повідомлення (використовуйте \\n для нового рядка)",
    color="Колір рамки (оберіть зі списку)",
    thumbnail="Зображення для колонтитулу (необов'язково)",
    image="Зображення для прикріплення (необов'язково)"
)
@app_commands.choices(color=[
    app_commands.Choice(name="🔵 Синій", value="blue"),
    app_commands.Choice(name="🟢 Зелений", value="green"),
    app_commands.Choice(name="🔴 Червоний", value="red"),
    app_commands.Choice(name="🟡 Жовтий", value="yellow"),
    app_commands.Choice(name="🟣 Фіолетовий", value="purple"),
    app_commands.Choice(name="🟠 Помаранчевий", value="orange"),
    app_commands.Choice(name="🌈 Випадковий", value="random")
])
async def send_embed(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    description: str,
    color: app_commands.Choice[str],
    thumbnail: discord.Attachment = None,
    image: discord.Attachment = None
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Ця команда доступна лише адміністраторам", ephemeral=True)
    
    # Визначаємо колір
    color_map = {
        "blue": discord.Color.blue(),
        "green": discord.Color.green(),
        "red": discord.Color.red(),
        "yellow": discord.Color.gold(),
        "purple": discord.Color.purple(),
        "orange": discord.Color.orange(),
        "random": discord.Color.random()
    }
    selected_color = color_map.get(color.value, discord.Color.blue())
    
    # Створюємо embed
    embed = discord.Embed(
        title=title,
        description=description.replace('\\n', '\n'),
        color=selected_color,
        timestamp=datetime.utcnow()
    )
    
    # Додаємо колонтитул
    if thumbnail and thumbnail.content_type.startswith('image/'):
        embed.set_thumbnail(url=thumbnail.url)
    
    # Додаємо основне зображення
    if image and image.content_type.startswith('image/'):
        embed.set_image(url=image.url)
   
    # Відправляємо
    try:
        await channel.send(embed=embed)
        await interaction.response.send_message(
            f"✅ Повідомлення успішно надіслано до {channel.mention}",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Бот не має прав для надсилання повідомлень у цей канал",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Сталася помилка: {str(e)}",
            ephemeral=True
        )

# ========== НОВІ КОМАНДИ ДЛЯ УКРІПРАЙОНУ ==========

@bot.tree.command(name="stronghold", description="Показати поточну статистику укріпрайону")
async def stronghold(interaction: discord.Interaction):
    await interaction.response.defer()
    await send_detailed_stronghold_report(interaction.channel)

@bot.tree.command(name="stronghold_setup", description="Налаштувати автоматичне оновлення статистики")
@app_commands.describe(
    channel="Канал для надсилання",
    hour="Година (0-23, Київський час)",
    minute="Хвилина (0-59)"
)
async def stronghold_setup(interaction: discord.Interaction, 
                         channel: discord.TextChannel,
                         hour: app_commands.Range[int, 0, 23] = 18,
                         minute: app_commands.Range[int, 0, 59] = 0):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    stronghold_stats_config[interaction.guild_id] = {
        "channel_id": channel.id,
        "hour": hour,
        "minute": minute
    }
    
    if not stronghold_stats_task.is_running():
        stronghold_stats_task.start()
    
    kyiv_time = datetime.now(pytz.timezone('Europe/Kiev'))
    await interaction.response.send_message(
        f"✅ Статистика буде надсилатися щодня о {hour:02d}:{minute:02d} (Київ) у {channel.mention}\n"
        f"Поточний час: {kyiv_time.strftime('%H:%M')}",
        ephemeral=True
    )

@bot.tree.command(name="stronghold_buildings", description="Показати стан будівель укріпрайону")
async def stronghold_buildings(interaction: discord.Interaction):
    await interaction.response.defer()
    
    data = await get_stronghold_data()
    if not data or str(CLAN_ID) not in data:
        return await interaction.followup.send("❌ Не вдалося отримати дані", ephemeral=True)
    
    clan_data = data[str(CLAN_ID)]
    buildings = clan_data.get('building_slots', [])
    
    if not buildings:
        return await interaction.followup.send("❌ Немає даних про будівлі", ephemeral=True)
    
    embed = discord.Embed(
        title=f"Будівлі укріпрайону [{clan_data['clan_tag']}]",
        color=discord.Color.blue(),
        description=f"Загальний рівень будівель: {clan_data['stronghold_buildings_level']}"
    )
    
    for building in buildings:
        embed.add_field(
            name=f"{building['building_title']} (рівень {building['building_level']})",
            value=f"Резерв: {building.get('reserve_title', 'Немає')}",
            inline=False
        )
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="stronghold_reserves", description="Показати активні резерви укріпрайону")
async def stronghold_reserves(interaction: discord.Interaction):
    await interaction.response.defer()
    
    data = await get_stronghold_data()
    if not data or str(CLAN_ID) not in data:
        return await interaction.followup.send("❌ Не вдалося отримати дані", ephemeral=True)
    
    clan_data = data[str(CLAN_ID)]
    buildings = clan_data.get('building_slots', [])
    active_reserves = [b for b in buildings if b.get('reserve_title')]
    
    if not active_reserves:
        return await interaction.followup.send("🔶 Немає активних резервів", ephemeral=True)
    
    embed = discord.Embed(
        title=f"Активні резерви [{clan_data['clan_tag']}]",
        color=discord.Color.gold()
    )
    
    for reserve in active_reserves:
        embed.add_field(
            name=reserve['building_title'],
            value=f"Резерв: {reserve['reserve_title']}",
            inline=False
        )
    
    await interaction.followup.send(embed=embed)

# ========== ПОДІЇ БОТА ==========

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.id in [data["voice_channel"] for data in tracked_channels.values()]:
        member_key = f"{member.guild.id}_{member.id}"
        if member_key in voice_time_tracker:
            del voice_time_tracker[member_key]
            warning_sent.discard(member_key)

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    
    guild = member.guild
    try:
        current_invites = await guild.invites()
        used_invite = None
        for invite in current_invites:
            cached_uses = invite_cache.get(guild.id, {}).get(invite.code, 0)
            if invite.uses > cached_uses:
                used_invite = invite
                break
        
        if used_invite:
            await update_invite_cache(guild)
            guild_roles = invite_roles.get(str(guild.id), {})
            role_id = guild_roles.get(used_invite.code)
            
            if role_id:
                role = guild.get_role(role_id)
                if role:
                    try:
                        await member.add_roles(role)
                        print(f"Надано роль {role.name} користувачу {member} за запрошення {used_invite.code}")
                    except discord.Forbidden:
                        print(f"Немає дозволу надавати роль {role.name}")
                    except Exception as e:
                        print(f"Помилка надання ролі: {e}")
    except Exception as e:
        print(f"Помилка обробки нового учасника: {e}")

@bot.event
async def on_invite_create(invite):
    await update_invite_cache(invite.guild)

@bot.event
async def on_invite_delete(invite):
    await update_invite_cache(invite.guild)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} онлайн!')
    
    # Встановлюємо київський час для логування
    kyiv_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kyiv_tz)
    print(f"Поточний час (Київ): {now}")
    print(f"WG_API_KEY: {'встановлено' if WG_API_KEY else 'не встановлено'}")
    print(f"CLAN_ID: {'встановлено' if CLAN_ID else 'не встановлено'}")
    
    for guild in bot.guilds:
        await update_invite_cache(guild)
    
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації: {e}")
    
    check_voice_activity.start()
    update_voice_activity.start()
    if active_stats_tracking:
        send_voice_activity_stats.start()
    if stronghold_stats_config and not stronghold_stats_task.is_running():
        stronghold_stats_task.start()
        print("Фонову задачу stronghold_stats_task запущено!")

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен Discord")

if __name__ == '__main__':
    print("Запуск бота...")
    bot.run(TOKEN)
