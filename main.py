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
from typing import Optional, List
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
time_locks = {}  # {user_id: (unlock_time, reason)}
role_changes = {}  # Історія змін ролей

# Система ролей за запрошеннями
invite_roles = {}
invite_cache = {}

# Система привітальних повідомлень
welcome_messages = {}

def load_data():
    global invite_roles, welcome_messages
    try:
        with open('data/invite_roles.json', 'r') as f:
            invite_roles = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        invite_roles = {}
    
    try:
        with open('data/welcome_messages.json', 'r') as f:
            welcome_messages = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        welcome_messages = {}

def save_data():
    os.makedirs('data', exist_ok=True)
    with open('data/invite_roles.json', 'w') as f:
        json.dump(invite_roles, f)
    with open('data/welcome_messages.json', 'w') as f:
        json.dump(welcome_messages, f)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно підключений!')
    load_data()
    
    # Ініціалізація кешу запрошень
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
        except:
            pass
    
    # Запуск фонових задач
    check_voice_activity.start()
    update_voice_activity.start()
    check_time_locks.start()
    
    # Синхронізація команд
    try:
        await bot.tree.sync()
        print("Команди успішно синхронізовані")
    except Exception as e:
        print(f"Помилка синхронізації команд: {e}")

# ========== КОМАНДИ АДМІНІСТРАЦІЇ ==========

@bot.tree.command(name="time_lock", description="Тимчасово заблокувати користувача")
@app_commands.describe(
    user="Користувач для блокування",
    duration="Час блокування у хвилинах",
    reason="Причина блокування",
    notify_channel="Канал для сповіщення (необов'язково)"
)
async def time_lock(
    interaction: discord.Interaction,
    user: discord.Member,
    duration: int,
    reason: str,
    notify_channel: Optional[discord.TextChannel] = None
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Недостатньо прав!", ephemeral=True)
    
    if user == interaction.user:
        return await interaction.response.send_message("❌ Не можна заблокувати себе!", ephemeral=True)
    
    if user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Не можна заблокувати адміністратора!", ephemeral=True)
    
    unlock_time = datetime.utcnow() + timedelta(minutes=duration)
    time_locks[user.id] = (unlock_time, reason)
    
    # Створення красивого повідомлення
    embed = discord.Embed(
        title="⛔ Користувача заблоковано",
        color=discord.Color.red(),
        timestamp=datetime.now(pytz.timezone('Europe/Kiev'))
    
    embed.set_thumbnail(url=user.display_avatar.url)
    
    embed.add_field(name="Користувач", value=f"{user.mention}\n{user.display_name}", inline=True)
    embed.add_field(name="Тривалість", value=f"{duration} хвилин", inline=True)
    embed.add_field(name="Причина", value=reason, inline=False)
    
    remaining = unlock_time - datetime.utcnow()
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    embed.set_footer(
        text=f"⏳ Розблокується через {hours} год {minutes} хв | {unlock_time.strftime('%d.%m.%Y %H:%M')}",
        icon_url=interaction.user.display_avatar.url
    )
    
    # Відправка повідомлення
    target_channel = notify_channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.response.send_message(
            f"✅ {user.mention} був успішно заблокований на {duration} хвилин",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Помилка: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="add_role", description="Додати роль користувачам")
@app_commands.describe(
    users="Користувачі для додавання ролі",
    role="Роль для додавання",
    reason="Причина (необов'язково)",
    notify_channel="Канал для сповіщення (необов'язково)"
)
async def add_role(
    interaction: discord.Interaction,
    users: List[discord.Member],
    role: discord.Role,
    reason: Optional[str] = "Не вказано",
    notify_channel: Optional[discord.TextChannel] = None
):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ Недостатньо прав!", ephemeral=True)
    
    if role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ Ця роль вище за мою!", ephemeral=True)
    
    success = []
    failed = []
    
    for user in users:
        try:
            await user.add_roles(role, reason=reason)
            success.append(user)
            # Записуємо зміну в історію
            if user.id not in role_changes:
                role_changes[user.id] = []
            role_changes[user.id].append({
                "type": "add",
                "role": role.id,
                "by": interaction.user.id,
                "timestamp": datetime.utcnow().isoformat(),
                "reason": reason
            })
        except Exception as e:
            failed.append((user, str(e)))
    
    # Формуємо звіт
    embed = discord.Embed(
        title=f"➕ Додано роль {role.name}",
        color=role.color,
        timestamp=datetime.now(pytz.timezone('Europe/Kiev'))
    )
    
    if success:
        embed.add_field(
            name="Успішно",
            value="\n".join([f"{user.mention} ({user.display_name})" for user in success]),
            inline=False
        )
    
    if failed:
        embed.add_field(
            name="Не вдалося",
            value="\n".join([f"{user[0].mention} ({user[0].display_name}): {user[1]}" for user in failed]),
            inline=False
        )
    
    embed.add_field(name="Виконав", value=interaction.user.mention, inline=True)
    embed.add_field(name="Причина", value=reason, inline=True)
    
    # Відправляємо результат
    target_channel = notify_channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.response.send_message(
            f"✅ Роль додана для {len(success)} користувачів",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Помилка: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="remove_role", description="Видалити роль у користувачів")
@app_commands.describe(
    users="Користувачі для видалення ролі",
    role="Роль для видалення",
    reason="Причина (необов'язково)",
    notify_channel="Канал для сповіщення (необов'язково)"
)
async def remove_role(
    interaction: discord.Interaction,
    users: List[discord.Member],
    role: discord.Role,
    reason: Optional[str] = "Не вказано",
    notify_channel: Optional[discord.TextChannel] = None
):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ Недостатньо прав!", ephemeral=True)
    
    if role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ Ця роль вище за мою!", ephemeral=True)
    
    success = []
    failed = []
    
    for user in users:
        try:
            await user.remove_roles(role, reason=reason)
            success.append(user)
            # Записуємо зміну в історію
            if user.id not in role_changes:
                role_changes[user.id] = []
            role_changes[user.id].append({
                "type": "remove",
                "role": role.id,
                "by": interaction.user.id,
                "timestamp": datetime.utcnow().isoformat(),
                "reason": reason
            })
        except Exception as e:
            failed.append((user, str(e)))
    
    # Формуємо звіт
    embed = discord.Embed(
        title=f"➖ Видалено роль {role.name}",
        color=discord.Color.red(),
        timestamp=datetime.now(pytz.timezone('Europe/Kiev'))
    )
    
    if success:
        embed.add_field(
            name="Успішно",
            value="\n".join([f"{user.mention} ({user.display_name})" for user in success]),
            inline=False
        )
    
    if failed:
        embed.add_field(
            name="Не вдалося",
            value="\n".join([f"{user[0].mention} ({user[0].display_name}): {user[1]}" for user in failed]),
            inline=False
        )
    
    embed.add_field(name="Виконав", value=interaction.user.mention, inline=True)
    embed.add_field(name="Причина", value=reason, inline=True)
    
    # Відправляємо результат
    target_channel = notify_channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.response.send_message(
            f"✅ Роль видалена у {len(success)} користувачів",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(
            f"❌ Помилка: {str(e)}",
            ephemeral=True
        )

@bot.tree.command(name="online_list", description="Показати список онлайн користувачів")
@app_commands.describe(
    channel="Канал для відправки (необов'язково)",
    show_all="Показати всіх, включаючи офлайн (за замовчуванням False)"
)
async def online_list(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    show_all: bool = False
):
    await interaction.response.defer(ephemeral=True)
    
    members = interaction.guild.members
    if not show_all:
        members = [m for m in members if m.status != discord.Status.offline and not m.bot]
    
    # Сортування за статусом
    status_order = {
        discord.Status.online: 0,
        discord.Status.idle: 1,
        discord.Status.dnd: 2,
        discord.Status.offline: 3
    }
    members.sort(key=lambda m: (status_order.get(m.status, 3), m.display_name))
    
    # Розділяємо на сторінки
    chunks = [members[i:i+15] for i in range(0, len(members), 15)]
    
    # Створюємо embed
    embed = discord.Embed(
        title=f"📊 Список користувачів ({len(members)})",
        color=discord.Color.blue(),
        timestamp=datetime.now(pytz.timezone('Europe/Kiev'))
    )
    
    status_emojis = {
        discord.Status.online: "🟢",
        discord.Status.idle: "🌙",
        discord.Status.dnd: "⛔",
        discord.Status.offline: "⚫"
    }
    
    for i, chunk in enumerate(chunks):
        member_list = []
        for member in chunk:
            emoji = status_emojis.get(member.status, "⚫")
            member_list.append(f"{emoji} {member.mention} ({member.display_name})")
        
        embed.add_field(
            name=f"Сторінка {i+1}",
            value="\n".join(member_list) or "Немає користувачів",
            inline=False
        )
    
    embed.set_footer(text=f"Запит від {interaction.user.display_name}")
    
    # Відправляємо
    target_channel = channel or interaction.channel
    try:
        await target_channel.send(embed=embed)
        await interaction.followup.send(
            "✅ Список успішно відправлено",
            ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(
            f"❌ Помилка: {str(e)}",
            ephemeral=True
        )

# ========== ФУНКЦІОНАЛ ТАЙМ-ЛОКУ ==========

@tasks.loop(minutes=1)
async def check_time_locks():
    now = datetime.utcnow()
    to_remove = []
    
    for user_id, (unlock_time, reason) in time_locks.items():
        if now >= unlock_time:
            to_remove.append(user_id)
    
    for user_id in to_remove:
        time_locks.pop(user_id, None)

@bot.event
async def on_message(message):
    if message.author.id in time_locks:
        unlock_time, reason = time_locks[message.author.id]
        if datetime.utcnow() < unlock_time:
            try:
                await message.delete()
                remaining = unlock_time - datetime.utcnow()
                hours, remainder = divmod(remaining.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                await message.author.send(
                    f"🔒 Ви заблоковані до {unlock_time.strftime('%d.%m.%Y %H:%M')}\n"
                    f"📌 Причина: {reason}\n"
                    f"⏳ Залишилось: {hours} год {minutes} хв"
                )
            except:
                pass
    
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id in time_locks:
        unlock_time, _ = time_locks[member.id]
        if datetime.utcnow() < unlock_time and after.channel:
            try:
                await member.move_to(None)
                await member.send("🔒 Ви заблоковані і не можете заходити в голосові канали")
            except:
                pass

# ========== ЗАПУСК БОТА ==========

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен Discord")

if __name__ == '__main__':
    print("Запуск бота...")
    bot.run(TOKEN)