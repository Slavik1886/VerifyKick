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
from typing import Optional, List, Union
import pytz

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True
intents.invites = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Системи відстеження
voice_time_tracker = {}
tracked_channels = {}
warning_sent = set()
voice_activity = defaultdict(timedelta)
last_activity_update = datetime.utcnow()
banned_users = {}  # {guild_id: {user_id: {"unlock_time": datetime, "reason": str}}}

# Система ролей за запрошеннями
invite_roles = {}
invite_cache = {}

# Система привітальних повідомлень
welcome_messages = {}

# Константи
WG_API_KEY = os.getenv('WG_API_KEY')
WG_API_URL = "https://api.worldoftanks.eu/wot/"

def load_invite_data():
    try:
        with open('invite_roles.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_invite_data():
    with open('invite_roles.json', 'w') as f:
        json.dump(invite_roles, f, indent=4)

def load_welcome_data():
    try:
        with open('welcome_messages.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_welcome_data():
    with open('welcome_messages.json', 'w') as f:
        json.dump(welcome_messages, f, indent=4)

def load_banned_users():
    try:
        with open('banned_users.json', 'r') as f:
            data = json.load(f)
            for guild_id, users in data.items():
                banned_users[int(guild_id)] = {}
                for user_id, ban_data in users.items():
                    banned_users[int(guild_id)][int(user_id)] = {
                        "unlock_time": datetime.fromisoformat(ban_data["unlock_time"]),
                        "reason": ban_data["reason"]
                    }
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_banned_users():
    data = {}
    for guild_id, users in banned_users.items():
        data[str(guild_id)] = {}
        for user_id, ban_data in users.items():
            data[str(guild_id)][str(user_id)] = {
                "unlock_time": ban_data["unlock_time"].isoformat(),
                "reason": ban_data["reason"]
            }
    with open('banned_users.json', 'w') as f:
        json.dump(data, f, indent=4)

# Ініціалізація даних
invite_roles = load_invite_data()
welcome_messages = load_welcome_data()
load_banned_users()

async def get_wg_api_data(endpoint: str, params: dict) -> Optional[dict]:
    """Функція для взаємодії з Wargaming API"""
    params['application_id'] = WG_API_KEY
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{WG_API_URL}{endpoint}", params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('data') if 'data' in data else data
                print(f"Помилка API: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"Помилка запиту до API: {e}")
    return None

async def update_invite_cache(guild):
    """Оновлюємо кеш запрошень для сервера"""
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
    except discord.Forbidden:
        print(f"Немає дозволу на перегляд запрошень для сервера {guild.name}")
    except Exception as e:
        print(f"Помилка оновлення кешу запрошень: {e}")

async def delete_after(message, minutes):
    """Видаляє повідомлення через вказану кількість хвилин"""
    if minutes <= 0: 
        return
    await asyncio.sleep(minutes * 60)
    try: 
        await message.delete()
    except: 
        pass

async def create_ban_embed(member: discord.Member, duration: str, reason: str, unlock_time: datetime) -> discord.Embed:
    """Створює embed для повідомлення про блокування"""
    kyiv_tz = pytz.timezone('Europe/Kiev')
    current_time = datetime.now(kyiv_tz)
    time_left = unlock_time - current_time
    
    embed = discord.Embed(
        title="⛔ Користувача заблоковано",
        color=discord.Color.red(),
        timestamp=current_time
    )
    
    embed.set_thumbnail(url=member.display_avatar.url)
    
    embed.add_field(
        name="Користувач",
        value=f"{member.mention}\n{member.display_name}",
        inline=True
    )
    
    embed.add_field(
        name="Тривалість",
        value=duration,
        inline=True
    )
    
    embed.add_field(
        name="Причина",
        value=reason or "Не вказано",
        inline=False
    )
    
    embed.add_field(
        name="Розблокується о",
        value=f"<t:{int(unlock_time.timestamp())}:R>",
        inline=False
    )
    
    embed.set_footer(
        text=f"Час до розблокування: {str(time_left).split('.')[0]}"
    )
    
    return embed

async def check_time_locks():
    """Перевіряє час блокувань і знімає їх при закінченні"""
    while True:
        await asyncio.sleep(60)
        current_time = datetime.utcnow()
        
        for guild_id, users in list(banned_users.items()):
            guild = bot.get_guild(guild_id)
            if not guild:
                continue
                
            for user_id, ban_data in list(users.items()):
                if current_time >= ban_data["unlock_time"]:
                    member = guild.get_member(user_id)
                    if member:
                        try:
                            mute_role = discord.utils.get(guild.roles, name="Muted")
                            if mute_role and mute_role in member.roles:
                                await member.remove_roles(mute_role)
                            
                            banned_users[guild_id].pop(user_id)
                            save_banned_users()
                            
                            try:
                                await member.send(f"🔓 Ваш обмежений доступ на сервері {guild.name} було знято.")
                            except:
                                pass
                                
                        except discord.Forbidden:
                            print(f"Не вдалося зняти обмеження для {member.display_name} на сервері {guild.name}")
                    else:
                        banned_users[guild_id].pop(user_id)
                        save_banned_users()

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
            
            if time_in_channel > timedelta(minutes=10) and member_key not in warning_sent:
                try:
                    await member.send("⚠️ Ви в каналі для неактивних користувачів вже 10+ хвилин. ✅ Будьте активні, або Ви будете відєднані!")
                    warning_sent.add(member_key)
                except: 
                    pass
            
            if time_in_channel > timedelta(minutes=15):
                try:
                    await member.move_to(None)
                    msg = await log_channel.send(f"🔴 {member.mention} відключено за неактивність на сервері")
                    bot.loop.create_task(delete_after(msg, data["delete_after"]))
                    del voice_time_tracker[member_key]
                    warning_sent.discard(member_key)
                except: 
                    pass

@bot.event
async def on_voice_state_update(member, before, after):
    # Перевіряємо чи користувач не заблокований
    if member.id in banned_users.get(member.guild.id, {}):
        if after.channel and not before.channel:
            await member.move_to(None)
            try:
                await member.send("⛔ Вам заборонено приєднуватися до голосових каналів під час блокування")
            except:
                pass
            return
    
    if before.channel and before.channel.id in [data["voice_channel"] for data in tracked_channels.values()]:
        member_key = f"{member.guild.id}_{member.id}"
        if member_key in voice_time_tracker:
            del voice_time_tracker[member_key]
            warning_sent.discard(member_key)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Перевіряємо чи користувач не заблокований
    if message.author.id in banned_users.get(message.guild.id, {}):
        await message.delete()
        try:
            await message.author.send("⛔ Вам заборонено писати повідомлення під час блокування")
        except:
            pass
        return
    
    await bot.process_commands(message)

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    
    guild = member.guild
    assigned_role = None
    
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
                        assigned_role = role
                        print(f"Надано роль {role.name} користувачу {member} за запрошення {used_invite.code}")
                    except discord.Forbidden:
                        print(f"Немає дозволу надавати роль {role.name}")
                    except Exception as e:
                        print(f"Помилка надання ролі: {e}")
    except Exception as e:
        print(f"Помилка обробки нового учасника: {e}")
    
    # Обробка привітальних повідомлень
    if str(guild.id) in welcome_messages:
        channel_id = welcome_messages[str(guild.id)]["channel_id"]
        channel = guild.get_channel(channel_id)
        if channel:
            try:
                inviter = "Невідомо"
                if used_invite and used_invite.inviter:
                    inviter = used_invite.inviter.mention
                
                role_info = "Не призначено"
                if assigned_role:
                    role_info = assigned_role.mention
                
                kyiv_time = datetime.now(pytz.timezone('Europe/Kiev'))
                embed = discord.Embed(
                    title=f"Ласкаво просимо на сервер, {member.display_name}!",
                    color=discord.Color.green(),
                    timestamp=kyiv_time
                )
                
                embed.set_thumbnail(url=member.display_avatar.url)
                
                embed.add_field(
                    name="Користувач",
                    value=f"{member.mention}\n{member.display_name}",
                    inline=True
                )
                
                embed.add_field(
                    name="Запросив",
                    value=inviter,
                    inline=True
                )
                
                embed.add_field(
                    name="Призначена роль",
                    value=role_info,
                    inline=False
                )
                
                embed.add_field(
                    name="Дата реєстрації в Discord",
                    value=member.created_at.strftime("%d.%m.%Y"),
                    inline=False
                )
                
                embed.set_footer(
                    text=f"{guild.name} | Приєднався: {kyiv_time.strftime('%d.%m.%Y о %H:%M')}",
                    icon_url=guild.icon.url if guild.icon else None
                )
                
                await channel.send(embed=embed)
            except Exception as e:
                print(f"Помилка при відправці привітання: {e}")

@bot.event
async def on_invite_create(invite):
    await update_invite_cache(invite.guild)

@bot.event
async def on_invite_delete(invite):
    await update_invite_cache(invite.guild)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} онлайн!')
    
    kyiv_tz = pytz.timezone('Europe/Kiev')
    now = datetime.now(kyiv_tz)
    print(f"Поточний час (Київ): {now}")
    
    for guild in bot.guilds:
        await update_invite_cache(guild)
    
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації: {e}")
    
    check_voice_activity.start()
    update_voice_activity.start()
    bot.loop.create_task(check_time_locks())

# ========== НОВІ КОМАНДИ ==========

@bot.tree.command(name="time_lock", description="Тимчасово блокує користувача")
@app_commands.describe(
    user="Користувач для блокування",
    duration="Тривалість блокування (напр. 30m, 2h, 1d)",
    reason="Причина блокування",
    channel="Канал для повідомлення (необов'язково)"
)
async def time_lock(
    interaction: discord.Interaction,
    user: discord.Member,
    duration: str,
    reason: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    if user.bot:
        return await interaction.response.send_message("❌ Не можна блокувати ботів", ephemeral=True)
    
    if user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Не можна блокувати адміністраторів", ephemeral=True)
    
    try:
        time_amount = int(duration[:-1])
        time_unit = duration[-1].lower()
        
        if time_unit == 'm':
            delta = timedelta(minutes=time_amount)
        elif time_unit == 'h':
            delta = timedelta(hours=time_amount)
        elif time_unit == 'd':
            delta = timedelta(days=time_amount)
        else:
            return await interaction.response.send_message(
                "❌ Невірний формат часу. Використовуйте m (хвилини), h (години) або d (дні)",
                ephemeral=True
            )
    except (ValueError, IndexError):
        return await interaction.response.send_message(
            "❌ Невірний формат часу. Приклад: 30m, 2h, 1d",
            ephemeral=True
        )
    
    unlock_time = datetime.utcnow() + delta
    
    if interaction.guild.id not in banned_users:
        banned_users[interaction.guild.id] = {}
    
    banned_users[interaction.guild.id][user.id] = {
        "unlock_time": unlock_time,
        "reason": reason or "Не вказано"
    }
    save_banned_users()
    
    mute_role = discord.utils.get(interaction.guild.roles, name="Muted")
    if not mute_role:
        try:
            mute_role = await interaction.guild.create_role(name="Muted", color=discord.Color.dark_grey())
            
            for channel in interaction.guild.channels:
                try:
                    await channel.set_permissions(
                        mute_role,
                        send_messages=False,
                        speak=False,
                        add_reactions=False
                    )
                except:
                    continue
        except discord.Forbidden:
            return await interaction.response.send_message(
                "❌ Бот не має дозволів для створення ролі Muted",
                ephemeral=True
            )
    
    try:
        await user.add_roles(mute_role)
    except discord.Forbidden:
        return await interaction.response.send_message(
            "❌ Бот не має дозволів для додавання ролей",
            ephemeral=True
        )
    
    duration_text = ""
    if time_unit == 'm':
        duration_text = f"{time_amount} хвилин"
    elif time_unit == 'h':
        duration_text = f"{time_amount} годин"
    elif time_unit == 'd':
        duration_text = f"{time_amount} днів"
    
    embed = await create_ban_embed(user, duration_text, reason, unlock_time)
    
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.response.send_message(
            f"✅ Користувача {user.mention} успішно заблоковано",
            ephemeral=True
        )
        
        try:
            await user.send(embed=embed)
        except:
            pass
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Не вдалося надіслати повідомлення про блокування",
            ephemeral=True
        )

@bot.tree.command(name="add_role", description="Додає роль одному або декільком користувачам")
@app_commands.describe(
    users="Користувачі (через пробіл або @)",
    role="Роль для додавання",
    reason="Причина (необов'язково)",
    channel="Канал для повідомлення (необов'язково)"
)
async def add_role(
    interaction: discord.Interaction,
    users: str,
    role: discord.Role,
    reason: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_ids = [int(id.strip()) for id in users.split() if id.strip().isdigit()]
        members = []
        for user_id in user_ids:
            member = interaction.guild.get_member(user_id)
            if member:
                members.append(member)
        
        if not members:
            members = []
            for mention in users.split():
                if mention.startswith('<@') and mention.endswith('>'):
                    user_id = mention[2:-1]
                    if user_id.startswith('!'):
                        user_id = user_id[1:]
                    if user_id.isdigit():
                        member = interaction.guild.get_member(int(user_id))
                        if member:
                            members.append(member)
    except Exception as e:
        return await interaction.followup.send(
            f"❌ Помилка при обробці користувачів: {str(e)}",
            ephemeral=True
        )
    
    if not members:
        return await interaction.followup.send(
            "❌ Не знайдено жодного валідного користувача",
            ephemeral=True
        )
    
    success_count = 0
    failed_members = []
    
    for member in members:
        try:
            await member.add_roles(role)
            success_count += 1
        except:
            failed_members.append(member.display_name)
    
    kyiv_tz = pytz.timezone('Europe/Kiev')
    current_time = datetime.now(kyiv_tz)
    
    embed = discord.Embed(
        title=f"🔹 Роль {role.name} додана",
        color=role.color,
        timestamp=current_time
    )
    
    embed.add_field(
        name="Кількість користувачів",
        value=str(success_count),
        inline=True
    )
    
    if reason:
        embed.add_field(
            name="Причина",
            value=reason,
            inline=True
        )
    
    if failed_members:
        embed.add_field(
            name="Не вдалося додати роль",
            value=", ".join(failed_members),
            inline=False
        )
    
    embed.set_footer(
        text=f"Виконав: {interaction.user.display_name}",
        icon_url=interaction.user.display_avatar.url
    )
    
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.followup.send(
            f"✅ Роль {role.mention} успішно додано {success_count} користувачам",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Не вдалося надіслати повідомлення про додавання ролі",
            ephemeral=True
        )

@bot.tree.command(name="rem_role", description="Видаляє роль у одного або декількох користувачів")
@app_commands.describe(
    users="Користувачі (через пробіл або @)",
    role="Роль для видалення",
    reason="Причина (необов'язково)",
    channel="Канал для повідомлення (необов'язково)"
)
async def rem_role(
    interaction: discord.Interaction,
    users: str,
    role: discord.Role,
    reason: Optional[str] = None,
    channel: Optional[discord.TextChannel] = None
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        user_ids = [int(id.strip()) for id in users.split() if id.strip().isdigit()]
        members = []
        for user_id in user_ids:
            member = interaction.guild.get_member(user_id)
            if member:
                members.append(member)
        
        if not members:
            members = []
            for mention in users.split():
                if mention.startswith('<@') and mention.endswith('>'):
                    user_id = mention[2:-1]
                    if user_id.startswith('!'):
                        user_id = user_id[1:]
                    if user_id.isdigit():
                        member = interaction.guild.get_member(int(user_id))
                        if member:
                            members.append(member)
    except Exception as e:
        return await interaction.followup.send(
            f"❌ Помилка при обробці користувачів: {str(e)}",
            ephemeral=True
        )
    
    if not members:
        return await interaction.followup.send(
            "❌ Не знайдено жодного валідного користувача",
            ephemeral=True
        )
    
    success_count = 0
    failed_members = []
    
    for member in members:
        try:
            await member.remove_roles(role)
            success_count += 1
        except:
            failed_members.append(member.display_name)
    
    kyiv_tz = pytz.timezone('Europe/Kiev')
    current_time = datetime.now(kyiv_tz)
    
    embed = discord.Embed(
        title=f"🔹 Роль {role.name} видалена",
        color=discord.Color.orange(),
        timestamp=current_time
    )
    
    embed.add_field(
        name="Кількість користувачів",
        value=str(success_count),
        inline=True
    )
    
    if reason:
        embed.add_field(
            name="Причина",
            value=reason,
            inline=True
        )
    
    if failed_members:
        embed.add_field(
            name="Не вдалося видалити роль",
            value=", ".join(failed_members),
            inline=False
        )
    
    embed.set_footer(
        text=f"Виконав: {interaction.user.display_name}",
        icon_url=interaction.user.display_avatar.url
    )
    
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.followup.send(
            f"✅ Роль {role.mention} успішно видалено у {success_count} користувачів",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Не вдалося надіслати повідомлення про видалення ролі",
            ephemeral=True
        )

@bot.tree.command(name="online_members", description="Показує список онлайн-користувачів")
@app_commands.describe(
    channel="Канал для відправки списку (необов'язково)"
)
async def online_members(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None
):
    await interaction.response.defer(ephemeral=True)
    
    online_members = []
    idle_members = []
    dnd_members = []
    offline_members = []
    
    for member in interaction.guild.members:
        if member.bot:
            continue
            
        if member.status == discord.Status.online:
            online_members.append(member)
        elif member.status == discord.Status.idle:
            idle_members.append(member)
        elif member.status == discord.Status.dnd:
            dnd_members.append(member)
        else:
            offline_members.append(member)
    
    kyiv_tz = pytz.timezone('Europe/Kiev')
    current_time = datetime.now(kyiv_tz)
    
    embed = discord.Embed(
        title=f"👥 Статистика активності на сервері {interaction.guild.name}",
        color=discord.Color.blurple(),
        timestamp=current_time
    )
    
    embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
    
    if online_members:
        embed.add_field(
            name=f"🟢 Онлайн ({len(online_members)})",
            value="\n".join([f"{member.mention} - {member.display_name}" for member in online_members[:20]]),
            inline=True
        )
        if len(online_members) > 20:
            embed.add_field(
                name="...",
                value=f"І ще {len(online_members) - 20} користувачів",
                inline=True
            )
    
    if idle_members:
        embed.add_field(
            name=f"🌙 Відійшли ({len(idle_members)})",
            value="\n".join([f"{member.mention} - {member.display_name}" for member in idle_members[:10]]),
            inline=True
        )
        if len(idle_members) > 10:
            embed.add_field(
                name="...",
                value=f"І ще {len(idle_members) - 10} користувачів",
                inline=True
            )
    
    if dnd_members:
        embed.add_field(
            name=f"⛔ Не турбувати ({len(dnd_members)})",
            value="\n".join([f"{member.mention} - {member.display_name}" for member in dnd_members[:10]]),
            inline=True
        )
        if len(dnd_members) > 10:
            embed.add_field(
                name="...",
                value=f"І ще {len(dnd_members) - 10} користувачів",
                inline=True
            )
    
    embed.add_field(
        name="📊 Загальна статистика",
        value=(
            f"• Усього учасників: {len(interaction.guild.members)}\n"
            f"• Ботів: {len([m for m in interaction.guild.members if m.bot])}\n"
            f"• Онлайн: {len(online_members)} ({len(online_members)/len(interaction.guild.members)*100:.1f}%)\n"
            f"• Офлайн: {len(offline_members)} ({len(offline_members)/len(interaction.guild.members)*100:.1f}%)"
        ),
        inline=False
    )
    
    embed.set_footer(
        text=f"Станом на {current_time.strftime('%d.%m.%Y о %H:%M')} (Київ)"
    )
    
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.followup.send(
            "✅ Список онлайн-користувачів успішно відправлено",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "❌ Не вдалося надіслати список онлайн-користувачів",
            ephemeral=True
        )

# ========== ПОПЕРЕДНІ КОМАНДИ ==========

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
async def track_voice(
    interaction: discord.Interaction, 
    voice_channel: discord.VoiceChannel, 
    log_channel: discord.TextChannel,
    delete_after: int = 5
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
    
    tracked_channels[interaction.guild.id] = {
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

@bot.tree.command(name="remove_default_only", description="Видаляє користувачів тільки з @everyone")
async def remove_default_only(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    deleted = 0
    
    for member in interaction.guild.members:
        if not member.bot and len(member.roles) == 1:
            try:
                await member.kick(reason="Тільки @everyone")
                deleted += 1
            except: 
                pass
    
    await interaction.followup.send(f"Видалено {deleted} користувачів", ephemeral=True)

@bot.tree.command(name="remove_by_role", description="Видаляє користувачів з роллю")
@app_commands.describe(role="Роль для видалення")
async def remove_by_role(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
    
    if role == interaction.guild.default_role:
        return await interaction.response.send_message("Не можна видаляти всіх", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    deleted = 0
    
    for member in role.members:
        if not member.bot:
            try:
                await member.kick(reason=f"Видалення ролі {role.name}")
                deleted += 1
            except: 
                pass
    
    await interaction.followup.send(
        f"Видалено {deleted} користувачів з роллю {role.name}", 
        ephemeral=True
    )

@bot.tree.command(name="list_no_roles", description="Список користувачів без ролей")
async def list_no_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
    
    await interaction.response.defer(ephemeral=True)
    members = [f"{m.display_name} ({m.id})" for m in interaction.guild.members 
               if not m.bot and len(m.roles) == 1]
    
    if not members:
        return await interaction.followup.send("Немає таких користувачів", ephemeral=True)
    
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
        return await interaction.followup.send(
            f"Немає користувачів з роллю {role.name}", 
            ephemeral=True
        )
    
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
    thumbnail: Optional[discord.Attachment] = None,
    image: Optional[discord.Attachment] = None
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Ця команда доступна лише адміністраторам", 
            ephemeral=True
        )
    
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
    
    embed = discord.Embed(
        title=title,
        description=description.replace('\\n', '\n'),
        color=selected_color,
        timestamp=datetime.utcnow()
    )
    
    if thumbnail and thumbnail.content_type.startswith('image/'):
        embed.set_thumbnail(url=thumbnail.url)
    
    if image and image.content_type.startswith('image/'):
        embed.set_image(url=image.url)
   
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

@bot.tree.command(name="setup_welcome", description="Налаштувати канал для привітальних повідомлень")
@app_commands.describe(
    channel="Канал для привітальних повідомлень"
)
async def setup_welcome(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    welcome_messages[str(interaction.guild.id)] = {
        "channel_id": channel.id
    }
    save_welcome_data()
    
    await interaction.response.send_message(
        f"✅ Привітальні повідомлення будуть надсилатися у канал {channel.mention}\n"
        f"Тепер при вході нового учасника буде показано:\n"
        f"- Аватар користувача\n"
        f"- Ім'я та мітку\n"
        f"- Хто запросив\n"
        f"- Призначену роль\n"
        f"- Дату реєстрації в Discord\n"
        f"- Час приєднання до сервера",
        ephemeral=True
    )

@bot.tree.command(name="disable_welcome", description="Вимкнути привітальні повідомлення")
async def disable_welcome(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    if str(interaction.guild.id) in welcome_messages:
        welcome_messages.pop(str(interaction.guild.id))
        save_welcome_data()
    
    await interaction.response.send_message(
        "✅ Привітальні повідомлення вимкнено",
        ephemeral=True
    )

# Запуск бота
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен Discord")

if __name__ == '__main__':
    print("Запуск бота...")
    bot.run(TOKEN)