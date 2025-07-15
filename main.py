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
from typing import Optional
import pytz
from discord.ui import View, Button, Modal, TextInput

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

# Система ролей за запрошеннями
invite_roles = {}
invite_cache = {}

# Система привітальних повідомлень
welcome_messages = {}

def load_invite_data():
    try:
        with open('invite_roles.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_invite_data():
    with open('invite_roles.json', 'w') as f:
        json.dump(invite_roles, f)

def load_welcome_data():
    try:
        with open('welcome_messages.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_welcome_data():
    with open('welcome_messages.json', 'w') as f:
        json.dump(welcome_messages, f)

invite_roles = load_invite_data()
welcome_messages = load_welcome_data()

async def get_wg_api_data(endpoint: str, params: dict) -> Optional[dict]:
    """Функція для взаємодії з Wargaming API"""
    # params['application_id'] = WG_API_KEY  # Якщо потрібно, раскоментуйте і додайте ключ
    # async with aiohttp.ClientSession() as session:
    #     try:
    #         async with session.get(f"{WG_API_URL}{endpoint}", params=params) as resp:
    #             if resp.status == 200:
    #                 data = await resp.json()
    #                 return data.get('data') if 'data' in data else data
    #             print(f"Помилка API: {resp.status} - {await resp.text()}")
    #     except Exception as e:
    #         print(f"Помилка запиту до API: {e}")
    return None

async def update_invite_cache(guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
    except discord.Forbidden:
        print(f"Немає дозволу на перегляд запрошень для сервера {guild.name}")
    except Exception as e:
        print(f"Помилка оновлення кешу запрошень: {e}")

async def delete_after(message, minutes):
    if minutes <= 0: return
    await asyncio.sleep(minutes * 60)
    try: await message.delete()
    except: pass

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
                    await member.send("⚠️ Ви в каналі для неактивних користувачів вже 10+ хвилин. ✅ Будьте активні, або Ви будете відєднані!")
                    warning_sent.add(member_key)
                except: pass
            
            if time_in_channel > timedelta(minutes=15):
                try:
                    await member.move_to(None)
                    msg = await log_channel.send(f"🔴 {member.mention} відключено за неактивність на сервері")
                    bot.loop.create_task(delete_after(msg, data["delete_after"]))
                    del voice_time_tracker[member_key]
                    warning_sent.discard(member_key)
                except: pass

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
                    title=f"Ласкаво просимо👋на сервер, {member.display_name}!",
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

# ========== КОМАНДИ ==========

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

# ========== ЗАЯВКА НА ПРИЄДНАННЯ ==========

MOD_CHANNEL_ID = 1234567890  # <-- Вкажіть ID каналу для модерації заявок
GUILD_INVITE_LINK = "https://discord.gg/yourinvite"  # <-- Вкажіть посилання на запрошення

class JoinRequestModal(Modal, title="Запит на приєднання"):
    reason = TextInput(label="Чому ви хочете приєднатися?", style=discord.TextStyle.paragraph, required=True, max_length=300)
    async def on_submit(self, interaction: discord.Interaction):
        mod_channel = interaction.client.get_channel(MOD_CHANNEL_ID)
        if not mod_channel:
            await interaction.response.send_message("Не знайдено канал для модерації заявок.", ephemeral=True)
            return
        embed = discord.Embed(
            title="Нова заявка на приєднання",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        embed.set_author(name=interaction.user, icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="Користувач", value=f"{interaction.user.mention} ({interaction.user.id})", inline=False)
        embed.add_field(name="Відповідь", value=self.reason.value, inline=False)
        view = JoinRequestView(user_id=interaction.user.id, reason=self.reason.value)
        await mod_channel.send(embed=embed, view=view)
        await interaction.response.send_message("Ваша заявка надіслана модераторам. Очікуйте рішення.", ephemeral=True)

class JoinRequestView(View):
    def __init__(self, user_id, reason):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.reason = reason
    @discord.ui.button(label="Схвалити", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: Button):
        user = interaction.client.get_user(self.user_id)
        if not user:
            await interaction.response.send_message("Користувача не знайдено.", ephemeral=True)
            return
        try:
            await user.send(f"Ваша заявка схвалена! Ось запрошення: {GUILD_INVITE_LINK}")
            await interaction.response.send_message("Користувача повідомлено про схвалення.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Не вдалося надіслати DM: {e}", ephemeral=True)
        self.disable_all_items()
        await interaction.message.edit(view=self)
    @discord.ui.button(label="Скасувати", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: Button):
        user = interaction.client.get_user(self.user_id)
        if user:
            try:
                await user.send("Ваша заявка на приєднання була відхилена.")
            except:
                pass
        await interaction.response.send_message("Заявку скасовано.", ephemeral=True)
        self.disable_all_items()
        await interaction.message.edit(view=self)

@bot.tree.command(name="request_join", description="Подати заявку на приєднання до сервера")
async def request_join(interaction: discord.Interaction):
    await interaction.response.send_modal(JoinRequestModal())

# ========== ДОДАТКОВІ АДМІН-КОМАНДИ ========== 

@bot.tree.command(name="purge", description="Видалити N останніх повідомлень у каналі")
@app_commands.describe(amount="Кількість повідомлень для видалення")
async def purge(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ Потрібні права на керування повідомленнями", ephemeral=True)
    if amount < 1 or amount > 100:
        return await interaction.response.send_message("❌ Вкажіть число від 1 до 100", ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.response.send_message(f"✅ Видалено {len(deleted)} повідомлень", ephemeral=True)

@bot.tree.command(name="mute", description="Видати мут користувачу")
@app_commands.describe(member="Користувач для мюту", reason="Причина", minutes="На скільки хвилин (0 = безстроково)")
async def mute(interaction: discord.Interaction, member: discord.Member, reason: str = "", minutes: int = 0):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Потрібні права модератора", ephemeral=True)
    try:
        until = None
        if minutes > 0:
            until = discord.utils.utcnow() + timedelta(minutes=minutes)
        await member.edit(timeout=until, reason=reason)
        await interaction.response.send_message(f"🔇 {member.mention} зам'ючено {'на ' + str(minutes) + ' хв.' if minutes else 'безстроково'}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="unmute", description="Зняти мут з користувача")
@app_commands.describe(member="Користувач для розм'юту")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Потрібні права модератора", ephemeral=True)
    try:
        await member.edit(timeout=None)
        await interaction.response.send_message(f"🔊 {member.mention} розм'ючено", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="ban", description="Забанити користувача")
@app_commands.describe(member="Користувач для бану", reason="Причина")
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = ""): 
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ Потрібні права на бан", ephemeral=True)
    try:
        await member.ban(reason=reason)
        await interaction.response.send_message(f"⛔ {member.mention} забанено", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="unban", description="Розбанити користувача за ID")
@app_commands.describe(user_id="ID користувача для розбану")
async def unban(interaction: discord.Interaction, user_id: int):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ Потрібні права на бан", ephemeral=True)
    try:
        user = await bot.fetch_user(user_id)
        await interaction.guild.unban(user)
        await interaction.response.send_message(f"✅ {user.mention} розбанено", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="slowmode", description="Встановити повільний режим у каналі")
@app_commands.describe(seconds="Інтервал у секундах")
async def slowmode(interaction: discord.Interaction, seconds: int):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ Потрібні права на керування каналами", ephemeral=True)
    try:
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(f"🐢 Slowmode: {seconds} сек.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="announce", description="Зробити оголошення у вказаному каналі")
@app_commands.describe(channel="Канал для оголошення", message="Текст оголошення")
async def announce(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    try:
        await channel.send(f"📢 {message}")
        await interaction.response.send_message(f"✅ Оголошення надіслано у {channel.mention}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="userinfo", description="Показати інформацію про користувача")
@app_commands.describe(member="Користувач")
async def userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"Інформація про {member}", color=member.color, timestamp=datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Ім'я", value=member.display_name, inline=True)
    embed.add_field(name="Згадка", value=member.mention, inline=True)
    embed.add_field(name="Ролі", value=", ".join([r.mention for r in member.roles if r != member.guild.default_role]), inline=False)
    embed.add_field(name="Дата приєднання", value=member.joined_at.strftime('%d.%m.%Y %H:%M'), inline=True)
    embed.add_field(name="Дата реєстрації", value=member.created_at.strftime('%d.%m.%Y %H:%M'), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="serverinfo", description="Показати інформацію про сервер")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=f"Сервер: {guild.name}", color=discord.Color.blue(), timestamp=datetime.utcnow())
    embed.set_thumbnail(url=guild.icon.url if guild.icon else discord.Embed.Empty)
    embed.add_field(name="ID", value=guild.id, inline=True)
    embed.add_field(name="Власник", value=guild.owner.mention, inline=True)
    embed.add_field(name="Учасників", value=guild.member_count, inline=True)
    embed.add_field(name="Каналів", value=len(guild.channels), inline=True)
    embed.add_field(name="Ролей", value=len(guild.roles), inline=True)
    embed.add_field(name="Створено", value=guild.created_at.strftime('%d.%m.%Y %H:%M'), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="roleinfo", description="Показати інформацію про роль")
@app_commands.describe(role="Роль")
async def roleinfo(interaction: discord.Interaction, role: discord.Role):
    embed = discord.Embed(title=f"Роль: {role.name}", color=role.color, timestamp=datetime.utcnow())
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Користувачів", value=len(role.members), inline=True)
    embed.add_field(name="Колір", value=str(role.color), inline=True)
    embed.add_field(name="Позиція", value=role.position, inline=True)
    embed.add_field(name="Згадка", value=role.mention, inline=True)
    embed.add_field(name="Створено", value=role.created_at.strftime('%d.%m.%Y %H:%M'), inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ========== ЗАПУСК ==========

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен Discord")

if __name__ == '__main__':
    print("Запуск бота...")
    bot.run(TOKEN) 
