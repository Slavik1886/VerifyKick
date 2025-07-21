import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta, timezone
import asyncio
from collections import defaultdict
import json
import random
import aiohttp
from typing import Optional
import pytz
from discord.ui import View, Button, Modal, TextInput, Select
import feedparser
import re
from html import unescape
import requests
import io

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

# Вказуємо папку для постійного зберігання даних
DATA_DIR = "/data"

# Створюємо папку при старті
print(f"[DEBUG] Creating data directory at {DATA_DIR}")
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"[DEBUG] Data directory created/exists at {DATA_DIR}")
except Exception as e:
    print(f"[ERROR] Failed to create data directory: {e}")

def load_invite_data():
    try:
        with open(os.path.join(DATA_DIR, 'invite_roles.json'), 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_invite_data():
    with open(os.path.join(DATA_DIR, 'invite_roles.json'), 'w') as f:
        json.dump(invite_roles, f)

def load_welcome_data():
    try:
        with open(os.path.join(DATA_DIR, 'welcome_messages.json'), 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_welcome_data():
    with open(os.path.join(DATA_DIR, 'welcome_messages.json'), 'w') as f:
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

MODERATION_INVITE_CODE = "habzhGR74r"  # Код запрошення, яке потребує модерації
MODERATOR_ROLE_ID = 1359443269846700083  # ID ролі модератора

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    
    guild = member.guild
    assigned_role = None
    
    try:
        # Знаходимо запрошення, за яким зайшов користувач
        current_invites = await guild.invites()
        used_invite = None
        for invite in current_invites:
            cached_uses = invite_cache.get(guild.id, {}).get(invite.code, 0)
            if invite.uses > cached_uses:
                used_invite = invite
                break
        # Зберігаємо код інвайту для цього користувача
        if used_invite:
            pending_invites[str(member.id)] = used_invite.code
        
        if used_invite:
            await update_invite_cache(guild)
            guild_roles = invite_roles.get(str(guild.id), {})
            role_id = guild_roles.get(used_invite.code)
            
            # Якщо це запрошення потребує модерації
            if used_invite.code == MODERATION_INVITE_CODE:
                mod_channel_id = mod_channel.get(str(guild.id))
                mod_channel_obj = bot.get_channel(mod_channel_id) if mod_channel_id else None
                if not mod_channel_obj:
                    print(f"[ERROR] Не знайдено канал для модерації {mod_channel_id}")
                    return

                # Створюємо форму для введення ніку
                class NicknameModal(Modal, title="Вкажіть свій нікнейм"):
                    nickname = TextInput(label="Ігровий нік (WoT)", required=True, max_length=32)
                    
                    async def on_submit(self, interaction: discord.Interaction):
                        nickname_value = self.nickname.value.strip()
                        pending_nicknames[str(member.id)] = nickname_value
                        save_pending_nicknames()
                        
                        embed = discord.Embed(
                            title="Нова заявка на приєднання",
                            color=discord.Color.blurple(),
                            timestamp=datetime.utcnow()
                        )
                        embed.set_author(name=member.name, icon_url=member.display_avatar.url)
                        embed.add_field(name="Користувач", value=f"{member.mention} ({member.id})", inline=False)
                        embed.add_field(name="Бажаний нік", value=nickname_value, inline=False)
                        embed.add_field(name="Дата реєстрації", value=member.created_at.strftime("%d.%m.%Y"), inline=False)
                        
                        # Створюємо кнопки для модерації
                        class JoinRequestView(View):
                            def __init__(self):
                                super().__init__(timeout=None)

                            def disable_buttons(self):
                                for item in self.children:
                                    item.disabled = True

                            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                                try:
                                    # Перевіряємо, чи є користувач власником сервера або має роль модератора
                                    is_owner = interaction.user.id == interaction.guild.owner_id
                                    mod_role = interaction.guild.get_role(MODERATOR_ROLE_ID)
                                    has_mod_role = mod_role in interaction.user.roles if mod_role else False
                                    
                                    print(f"[DEBUG] Перевірка прав модерації:")
                                    print(f"[DEBUG] User ID: {interaction.user.id}")
                                    print(f"[DEBUG] Is Owner: {is_owner}")
                                    print(f"[DEBUG] Has Mod Role: {has_mod_role}")
                                    print(f"[DEBUG] User Roles: {[role.id for role in interaction.user.roles]}")
                                    
                                    if is_owner or has_mod_role:
                                        return True
                                        
                                    await interaction.response.send_message("❌ У вас немає прав на модерацію заявок", ephemeral=True)
                                    return False
                                except Exception as e:
                                    print(f"[ERROR] Помилка перевірки прав: {e}")
                                    return False

                            @discord.ui.button(label="Схвалити", style=discord.ButtonStyle.success)
                            async def approve(self, button_interaction: discord.Interaction, button: Button):
                                print(f"[DEBUG] Кнопку 'Схвалити' натиснув: {button_interaction.user}")
                                try:
                                    guild = button_interaction.guild
                                    print(f"[DEBUG] Guild ID: {guild.id}")
                                    # Беремо код інвайту з pending_invites
                                    invite_code = pending_invites.get(str(member.id))
                                    print(f"[DEBUG] Код інвайту для користувача: {invite_code}")
                                    if not invite_code:
                                        await button_interaction.response.send_message("❌ Не вдалося визначити інвайт користувача", ephemeral=True)
                                        return
                                    guild_roles = invite_roles.get(str(guild.id), {})
                                    print(f"[DEBUG] Ролі для запрошень: {guild_roles}")
                                    role_id = guild_roles.get(invite_code)
                                    print(f"[DEBUG] ID ролі для запрошення {invite_code}: {role_id}")
            if role_id:
                role = guild.get_role(role_id)
                                        print(f"[DEBUG] Знайдена роль: {role}")
                if role:
                                            print(f"[DEBUG] Додаємо роль {role.name} користувачу {member}")
                        await member.add_roles(role)
                                            # Змінюємо нік після схвалення
                                            saved_nick = pending_nicknames.pop(str(member.id), None)
                                            print(f"[DEBUG] Збережений нік: {saved_nick}")
                                            if saved_nick:
                                                try:
                                                    print(f"[DEBUG] Змінюємо нік на: {saved_nick}")
                                                    await member.edit(nick=saved_nick)
                                                    save_pending_nicknames()
                                                    await button_interaction.response.send_message(
                                                        f"✅ Користувача схвалено\nНадано роль {role.mention}\nВстановлено нік: {saved_nick}",
                                                        ephemeral=True
                                                    )
                                                except Exception as e:
                                                    print(f"[ERROR] Помилка зміни ніку: {e}")
                                                    await button_interaction.response.send_message(
                                                        f"✅ Користувача схвалено\nНадано роль {role.mention}\n❌ Помилка зміни ніку: {e}",
                                                        ephemeral=True
                                                    )
                                            else:
                                                await button_interaction.response.send_message(
                                                    f"✅ Користувача схвалено\nНадано роль {role.mention}",
                                                    ephemeral=True
                                                )
                                        else:
                                            print(f"[ERROR] Роль {role_id} не знайдена на сервері")
                                    else:
                                        print(f"[ERROR] Не знайдено роль для запрошення {invite_code}")
            except Exception as e:
                                    print(f"[ERROR] Помилка при схваленні: {str(e)}")
                                    print(f"[ERROR] Тип помилки: {type(e)}")
                                    import traceback
                                    print(f"[ERROR] Traceback: {traceback.format_exc()}")
                                    await button_interaction.response.send_message(f"❌ Помилка при схваленні: {str(e)}", ephemeral=True)
                                    return
                                
                                try:
                                    # Деактивуємо кнопки
                                    self.disable_buttons()
                                    await button_interaction.message.edit(view=self)
                                except Exception as e:
                                    print(f"[ERROR] Помилка при деактивації кнопок: {e}")
                            
                            @discord.ui.button(label="Відхилити", style=discord.ButtonStyle.danger)
                            async def deny(self, button_interaction: discord.Interaction, button: Button):
                                try:
                                    # Видаляємо збережений нік при відхиленні
                                    if str(member.id) in pending_nicknames:
                                        del pending_nicknames[str(member.id)]
                                        save_pending_nicknames()
                                    
                                    await member.kick(reason="Заявку відхилено")
                                    await button_interaction.response.send_message("❌ Користувача відхилено та вилучено з сервера", ephemeral=True)
                                except Exception as e:
                                    await button_interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)
                                
                                # Деактивуємо кнопки
                                self.disable_buttons()
                                await button_interaction.message.edit(view=self)
                        
                        view = JoinRequestView()
                        await mod_channel_obj.send(embed=embed, view=view)
                        await interaction.response.send_message("✅ Ваш нікнейм збережено. Очікуйте схвалення модератором.", ephemeral=True)

                # Створюємо кнопку для введення ніку
                class SetNicknameView(View):
                    def __init__(self):
                        super().__init__(timeout=None)

                    @discord.ui.button(label="Вказати нікнейм", style=discord.ButtonStyle.primary)
                    async def set_nickname(self, interaction: discord.Interaction, button: Button):
                        await interaction.response.send_modal(NicknameModal())

                # Надсилаємо повідомлення з кнопкою
                try:
                    await member.send("Будь ласка, вкажіть свій ігровий нікнейм:", view=SetNicknameView())
                except Exception as e:
                    print(f"[ERROR] Не вдалося надіслати повідомлення користувачу {member}: {e}")

            # Якщо це звичайне запрошення - просто видаємо роль
            else:
                if role_id:
                    role = guild.get_role(role_id)
                    if role:
                        await member.add_roles(role)
                        print(f"Надано роль {role.name} користувачу {member} за запрошення {used_invite.code}")

    except Exception as e:
        print(f"[ERROR] Помилка обробки нового учасника: {e}")

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
        guild_id = str(guild.id)
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації: {e}")
    check_voice_activity.start()
    update_voice_activity.start()
    # telegram_wotclue_news_task.start()
    # telegram_wotua_news_task.start()
    # telegram_wotclue_eu_news_task.start()
    telegram_channels_autopost.start()

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
    try:
        await interaction.response.defer(ephemeral=True)
        deleted = 0
        for member in interaction.guild.members:
            if not member.bot and len(member.roles) == 1:
                try:
                    await member.kick(reason="Тільки @everyone")
                    deleted += 1
                except: pass
        await interaction.followup.send(f"Видалено {deleted} користувачів", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Помилка: {str(e)}", ephemeral=True)

@bot.tree.command(name="remove_by_role", description="Видаляє користувачів з роллю")
@app_commands.describe(role="Роль для видалення")
async def remove_by_role(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    if role == interaction.guild.default_role:
        await interaction.response.send_message("Не можна видаляти всіх", ephemeral=True)
        return
    try:
        await interaction.response.defer(ephemeral=True)
        deleted = 0
        for member in role.members:
            if not member.bot:
                try:
                    await member.kick(reason=f"Видалення ролі {role.name}")
                    deleted += 1
                except: pass
        await interaction.followup.send(f"Видалено {deleted} користувачів з роллю {role.name}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Помилка: {str(e)}", ephemeral=True)

@bot.tree.command(name="list_no_roles", description="Список користувачів без ролей")
async def list_no_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    try:
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
    except Exception as e:
        await interaction.followup.send(f"❌ Помилка: {str(e)}", ephemeral=True)

@bot.tree.command(name="show_role_users", description="Показати користувачів з роллю")
@app_commands.describe(role="Роль для перегляду")
async def show_role_users(interaction: discord.Interaction, role: discord.Role):
    try:
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
    except Exception as e:
        await interaction.followup.send(f"❌ Помилка: {str(e)}", ephemeral=True)

# ========== НОВИЙ /send_embed з додаванням зображень з пристрою ========== 
from discord.ui import View, Select, Modal, TextInput, Button

class SendEmbedData:
    def __init__(self, channel_id=None, title=None, description=None, thumbnail_url=None, image_url=None, footer=None):
        self.channel_id = channel_id
        self.title = title
        self.description = description
        self.thumbnail_url = thumbnail_url
        self.image_url = image_url
        self.footer = footer

send_embed_cache = {}

class SendEmbedChannelView(View):
    def __init__(self, user, text_channels):
        super().__init__(timeout=60)
        self.user = user
        options = [
            discord.SelectOption(label=ch.name, value=str(ch.id)) for ch in text_channels[:25]
        ]
        self.add_item(SendEmbedChannelDropdown(options, self))
    async def interaction_check(self, interaction):
        return interaction.user.id == self.user.id

class SendEmbedChannelDropdown(Select):
    def __init__(self, options, parent_view):
        super().__init__(placeholder="Оберіть канал для embed-повідомлення", min_values=1, max_values=1, options=options)
        self.parent_view = parent_view
    async def callback(self, interaction):
        channel_id = int(self.values[0])
        send_embed_cache[interaction.user.id] = SendEmbedData(channel_id=channel_id)
        if interaction.response.is_done():
            await interaction.followup.send("Виникла помилка: interaction вже оброблено.", ephemeral=True)
            return
        await interaction.response.send_modal(SendEmbedTitleModal())
        self.parent_view.stop()

class SendEmbedTitleModal(Modal, title="Введіть заголовок"):
    title = TextInput(label="Заголовок", required=True, max_length=256)
    async def on_submit(self, interaction):
        data = send_embed_cache.get(interaction.user.id)
        if not data:
            await interaction.response.send_message("❌ Внутрішня помилка (немає стану)", ephemeral=True)
            return
        data.title = self.title.value
        await interaction.response.send_modal(SendEmbedDescriptionModal())

class SendEmbedDescriptionModal(Modal, title="Введіть текст повідомлення"):
    description = TextInput(label="Текст повідомлення", style=discord.TextStyle.paragraph, required=True, max_length=2000)
    async def on_submit(self, interaction):
        data = send_embed_cache.get(interaction.user.id)
        if not data:
            await interaction.response.send_message("❌ Внутрішня помилка (немає стану)", ephemeral=True)
            return
        data.description = self.description.value
        # Після тексту — запит на thumbnail
        await interaction.response.send_message(
            "Бажаєте додати зображення-колонтитул (thumbnail)? Завантажте файл або натисніть 'Пропустити'.",
            view=SendEmbedImageUploadView(interaction.user.id, 'thumbnail'),
            ephemeral=True
        )

class SendEmbedImageUploadView(View):
    def __init__(self, user_id, image_type):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.image_type = image_type  # 'thumbnail' або 'image'
        self.add_item(SendEmbedSkipButton(self))
    @discord.ui.button(label="Завантажити зображення", style=discord.ButtonStyle.primary)
    async def upload(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            f"Будь ласка, надішліть зображення для {'thumbnail' if self.image_type == 'thumbnail' else 'image'} у відповідь на це повідомлення.",
            ephemeral=True
        )
    async def interaction_check(self, interaction):
        return interaction.user.id == self.user_id

class SendEmbedSkipButton(Button):
    def __init__(self, parent_view):
        super().__init__(label="Пропустити", style=discord.ButtonStyle.secondary)
        self.parent_view = parent_view
    async def callback(self, interaction):
        # Якщо це thumbnail — переходимо до image
        if self.parent_view.image_type == 'thumbnail':
            await interaction.response.send_message(
                "Бажаєте додати зображення внизу embed? Завантажте файл або натисніть 'Пропустити'.",
                view=SendEmbedImageUploadView(self.parent_view.user_id, 'image'),
                ephemeral=True
            )
        else:
            # Далі — підпис
            await interaction.response.send_modal(SendEmbedFooterModal())

# Обробка вкладень (attachments) для thumbnail та image
@bot.event
async def on_message(message):
    # Не реагувати на власні повідомлення бота
    if message.author.bot:
        return
    # Перевіряємо, чи користувач у процесі створення embed
    data = send_embed_cache.get(message.author.id)
    if not data:
        return
    # Якщо користувач надіслав вкладення після запиту
    if message.attachments:
        # Визначаємо, яке зображення очікується
        if not data.thumbnail_url:
            data.thumbnail_url = message.attachments[0].url
            # Запитати про image
            await message.channel.send(
                "Бажаєте додати зображення внизу embed? Завантажте файл або натисніть 'Пропустити'.",
                view=SendEmbedImageUploadView(message.author.id, 'image'),
                delete_after=60
            )
        elif not data.image_url:
            data.image_url = message.attachments[0].url
            # Далі — підпис
            await message.channel.send(
                "Введіть підпис (footer) для embed (або залиште порожнім):",
                view=None
            )
            await message.author.send_modal(SendEmbedFooterModal())
        await message.delete(delay=1)

class SendEmbedFooterModal(Modal, title="Додати підпис (footer, опціонально)"):
    footer = TextInput(label="Підпис (footer)", required=False, max_length=256)
    async def on_submit(self, interaction):
        data = send_embed_cache.pop(interaction.user.id, None)
        if not data:
            await interaction.response.send_message("❌ Внутрішня помилка (немає стану)", ephemeral=True)
            return
        data.footer = self.footer.value.strip() if self.footer.value else None
        channel = interaction.guild.get_channel(data.channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("❌ Канал не знайдено або не є текстовим!", ephemeral=True)
            return
        embed = discord.Embed(title=data.title, description=data.description, color=discord.Color.blurple(), timestamp=datetime.utcnow())
        if data.thumbnail_url:
            embed.set_thumbnail(url=data.thumbnail_url)
        if data.image_url:
            embed.set_image(url=data.image_url)
        if data.footer:
            embed.set_footer(text=data.footer)
        try:
            await channel.send(embed=embed)
            await interaction.response.send_message(f"✅ Embed-повідомлення надіслано у {channel.mention}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Помилка надсилання: {e}", ephemeral=True)

# Заміна старої команди send_embed
@bot.tree.command(name="send_embed", description="Зручно створити embed-повідомлення через діалог")
async def send_embed(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Ця команда доступна лише адміністраторам", ephemeral=True)
        return
    text_channels = [ch for ch in interaction.guild.text_channels if ch.permissions_for(interaction.user).send_messages]
    if not text_channels:
        await interaction.response.send_message("❌ Немає доступних текстових каналів", ephemeral=True)
        return
    view = SendEmbedChannelView(interaction.user, text_channels)
    await interaction.response.send_message("Оберіть канал для embed-повідомлення:", view=view, ephemeral=True)

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

MOD_CHANNEL_ID = 1318890524643557406  # <-- ID каналу для модерації заявок
GUILD_INVITE_LINK = "https://discord.gg/yourinvite"  # <-- Вкажіть посилання на запрошення

# === ДОДАТКОВІ СТРУКТУРИ ДЛЯ ЗМІНИ НІКУ ===
NICK_NOTIFY_CHANNEL_FILE = os.path.join(DATA_DIR, 'nick_notify_channel.json')
nick_notify_channel = {}  # {guild_id: channel_id}

def load_nick_notify_channel():
    try:
        with open(NICK_NOTIFY_CHANNEL_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_nick_notify_channel():
    with open(NICK_NOTIFY_CHANNEL_FILE, 'w', encoding='utf-8') as f:
        json.dump(nick_notify_channel, f, ensure_ascii=False, indent=2)

nick_notify_channel = load_nick_notify_channel()

# Тимчасове зберігання ігрових ніків для заявок
PENDING_NICKNAMES_FILE = os.path.join(DATA_DIR, 'pending_nicknames.json')
pending_nicknames = {}  # {user_id: nickname}

def load_pending_nicknames():
    try:
        # Перевіряємо наявність папки перед читанням файлу
        if not os.path.exists(DATA_DIR):
            print(f"[ERROR] Data directory does not exist at {DATA_DIR}")
            return {}
            
        with open(PENDING_NICKNAMES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f"[DEBUG] Loaded pending_nicknames from file: {data}")
            return data
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[DEBUG] Failed to load pending_nicknames: {e}")
        return {}

def save_pending_nicknames():
    try:
        # Перевіряємо наявність папки перед збереженням
        if not os.path.exists(DATA_DIR):
            print(f"[ERROR] Data directory does not exist at {DATA_DIR}")
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                print(f"[DEBUG] Created data directory at {DATA_DIR}")
        except Exception as e:
                print(f"[ERROR] Failed to create data directory: {e}")
                return
                
        with open(PENDING_NICKNAMES_FILE, 'w', encoding='utf-8') as f:
            json.dump(pending_nicknames, f, ensure_ascii=False, indent=2)
        print(f"[DEBUG] Saved pending_nicknames to file: {pending_nicknames}")
    except Exception as e:
        print(f"[ERROR] Failed to save pending_nicknames: {e}")

pending_nicknames = load_pending_nicknames()
print(f"[DEBUG] Initial pending_nicknames: {pending_nicknames}")

@bot.tree.command(name="purge", description="Видалити N останніх повідомлень у каналі")
@app_commands.describe(amount="Кількість повідомлень для видалення")
async def purge(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ Потрібні права на керування повідомленнями", ephemeral=True)
    if amount < 1 or amount > 100:
        return await interaction.response.send_message("❌ Вкажіть число від 1 до 100", ephemeral=True)
    try:
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"✅ Видалено {len(deleted)} повідомлень", ephemeral=True)
    except discord.errors.DiscordServerError:
        await interaction.followup.send("❌ Discord тимчасово недоступний. Спробуйте ще раз пізніше.", ephemeral=True)
    except discord.errors.NotFound:
        await interaction.followup.send("❌ Взаємодію прострочено або не знайдено.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Помилка: {str(e)}", ephemeral=True)

@bot.tree.command(name="mute", description="Видати мут користувачу")
@app_commands.describe(
    member="Користувач тимчасово заблокований",
    reason="Причина",
    days="На скільки днів (0 = не враховувати)",
    hours="На скільки годин (0 = не враховувати)",
    minutes="На скільки хвилин (0 = не враховувати)"
)
async def mute(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "",
    days: int = 0,
    hours: int = 0,
    minutes: int = 0
):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Потрібні права модератора", ephemeral=True)
    BLOCKED_ROLE_ID = 1342610482623811664
    NORMAL_ROLE_ID = 1331255972303470603
    import pytz
    kyiv_tz = pytz.timezone('Europe/Kiev')
    try:
        until = None
        total_delta = timedelta(days=days, hours=hours, minutes=minutes)
        if total_delta.total_seconds() > 0:
            until = discord.utils.utcnow() + total_delta
        await member.edit(timed_out_until=until, reason=reason)
        # Формуємо строку тривалості
        duration_parts = []
        if days:
            duration_parts.append(f"{days} дн.")
        if hours:
            duration_parts.append(f"{hours} год.")
        if minutes:
            duration_parts.append(f"{minutes} хв.")
        duration_str = " ".join(duration_parts) if duration_parts else "безстроково"
        # Якщо безстроково, змінюємо ролі
        if total_delta.total_seconds() == 0:
            normal_role = interaction.guild.get_role(NORMAL_ROLE_ID)
            blocked_role = interaction.guild.get_role(BLOCKED_ROLE_ID)
            if normal_role and normal_role in member.roles:
                await member.remove_roles(normal_role, reason="Безстрокове блокування")
            if blocked_role and blocked_role not in member.roles:
                await member.add_roles(blocked_role, reason="Безстрокове блокування")
        await interaction.response.send_message(
            f"🔇 {member.mention} тимчасово заблоковано {duration_str}",
            ephemeral=True
        )
        # Надсилання приватного повідомлення користувачу
        try:
            # Формуємо повідомлення для користувача
            moderator = interaction.user.mention
            server_name = interaction.guild.name
            if not reason:
                reason = "Порушення правил користування сервером UADRG"
            lines = [
                f"👮‍♂️ *Вас заблокував:* ControlBot",
                f"📝 *Причина блокування:* {reason}",
            ]
            if total_delta.total_seconds() == 0:
                lines.append(f"⛔ *Акаунт заблоковано без можливості розблокування (назавжди)*")
            else:
                lines.append(f"⏳ *Тривалість блокування:* {duration_str}")
                if until:
                    kyiv_time = until.astimezone(kyiv_tz)
                    lines.append(f"📅 *Час розблокування:* {kyiv_time.strftime('%d.%m.%Y %H:%M')} (Київ)")
            lines.append(f"🌐 *Сервер:* {server_name}")
            msg = "\n".join(lines)
            await member.send(msg)
        except Exception:
            pass  # Якщо не вдалося надіслати DM, ігноруємо
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="unmute", description="Зняти мут з користувача")
@app_commands.describe(member="Користувач для розм'юту")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Потрібні права модератора", ephemeral=True)
    BLOCKED_ROLE_ID = 1342610482623811664
    NORMAL_ROLE_ID = 1331255972303470603
    try:
        await member.edit(timed_out_until=None)
        # Якщо у користувача є роль блокування, знімаємо її і повертаємо звичайну роль
        blocked_role = interaction.guild.get_role(BLOCKED_ROLE_ID)
        normal_role = interaction.guild.get_role(NORMAL_ROLE_ID)
        if blocked_role and blocked_role in member.roles:
            await member.remove_roles(blocked_role, reason="Зняття безстрокового блокування")
        if normal_role and normal_role not in member.roles:
            await member.add_roles(normal_role, reason="Зняття безстрокового блокування")
        await interaction.response.send_message(f"🔊 {member.mention} розблоковано", ephemeral=True)
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

@bot.tree.command(name="add_role", description="Видати роль користувачу")
@app_commands.describe(member="Користувач", role="Роль")
async def add_role(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    try:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ {role.mention} видано {member.mention}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="remove_role", description="Зняти роль з користувача")
@app_commands.describe(member="Користувач", role="Роль")
async def remove_role(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    try:
        await member.remove_roles(role)
        await interaction.response.send_message(f"✅ {role.mention} знято з {member.mention}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="lock_channel", description="Заблокувати канал для @everyone")
async def lock_channel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ Потрібні права на керування каналами", ephemeral=True)
    try:
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = False
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message("🔒 Канал заблоковано для @everyone", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="unlock_channel", description="Розблокувати канал для @everyone")
async def unlock_channel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ Потрібні права на керування каналами", ephemeral=True)
    try:
        overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
        overwrite.send_messages = True
        await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
        await interaction.response.send_message("🔓 Канал розблоковано для @everyone", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="clear_reactions", description="Очистити всі реакції з повідомлення")
@app_commands.describe(message_id="ID повідомлення")
async def clear_reactions(interaction: discord.Interaction, message_id: int):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ Потрібні права на керування повідомленнями", ephemeral=True)
    try:
        msg = await interaction.channel.fetch_message(message_id)
        await msg.clear_reactions()
        await interaction.response.send_message("✅ Реакції очищено", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="list_mutes", description="Показати зам'ючених користувачів")
async def list_mutes(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ Потрібні права модератора", ephemeral=True)
    import pytz
    kyiv_tz = pytz.timezone('Europe/Kiev')
    muted = [m for m in interaction.guild.members if m.timed_out_until and m.timed_out_until > datetime.now(timezone.utc)]
    if not muted:
        await interaction.response.send_message("Немає зам'ючених користувачів", ephemeral=True)
        return
    msg = "\n".join([
        f"{m.mention} до {m.timed_out_until.astimezone(kyiv_tz).strftime('%d.%m.%Y %H:%M')} (Київ)"
        for m in muted
    ])
    await interaction.response.send_message(f"Зам'ючені користувачі:\n{msg}", ephemeral=True)

@bot.tree.command(name="list_bans", description="Показати забанених користувачів")
async def list_bans(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ Потрібні права на бан", ephemeral=True)
    bans = [ban async for ban in interaction.guild.bans()]
    if not bans:
        await interaction.response.send_message("Немає забанених користувачів", ephemeral=True)
        return
    msg = "\n".join([f"{ban.user} ({ban.user.id})" for ban in bans])
    await interaction.response.send_message(f"Забановані користувачі:\n{msg}", ephemeral=True)

@bot.tree.command(name="change_nick", description="Змінити нікнейм користувача")
@app_commands.describe(member="Користувач", nickname="Новий нікнейм")
async def change_nick(interaction: discord.Interaction, member: discord.Member, nickname: str):
    if not interaction.user.guild_permissions.manage_nicknames:
        return await interaction.response.send_message("❌ Потрібні права на зміну ніків", ephemeral=True)
    try:
        await member.edit(nick=nickname)
        await interaction.response.send_message(f"✅ Нікнейм {member.mention} змінено на {nickname}", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Помилка: {e}", ephemeral=True)

@bot.tree.command(name="purge_user", description="Видалити N останніх повідомлень користувача у цьому каналі")
@app_commands.describe(member="Користувач", amount="Кількість повідомлень")
async def purge_user(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ Потрібні права на керування повідомленнями", ephemeral=True)
    if amount < 1 or amount > 100:
        return await interaction.response.send_message("❌ Вкажіть число від 1 до 100", ephemeral=True)
    try:
        await interaction.response.defer(ephemeral=True)
        def is_user(m):
            return m.author.id == member.id
        deleted = await interaction.channel.purge(limit=amount, check=is_user)
        await interaction.followup.send(f"✅ Видалено {len(deleted)} повідомлень від {member.mention}", ephemeral=True)
    except discord.errors.DiscordServerError:
        await interaction.followup.send("❌ Discord тимчасово недоступний. Спробуйте ще раз пізніше.", ephemeral=True)
    except discord.errors.NotFound:
        await interaction.followup.send("❌ Взаємодію прострочено або не знайдено.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Помилка: {str(e)}", ephemeral=True)

# ========== ЗАПУСК ==========

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен Discord")

# Для зберігання останніх новин зі сторонніх джерел
wot_external_news_last = {}  # guild_id: set(url)
external_news_queue = []  # [{'guild_id':..., 'entry':...}]

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q=World+of+Tanks&hl=uk&gl=UA&ceid=UA:uk"
YOUTUBE_WOT_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id=UCh554z2-7vIA-Mf9qAameoA"  # Офіційний WoT Official
WOT_UA_TELEGRAM_RSS = "https://rsshub.app/telegram/channel/worldoftanksua_official"
WOTCLUE_EU_TELEGRAM_RSS = "https://rsshub.app/telegram/channel/Wotclue_eu"
wotclue_eu_news_last_url = {}  # guild_id: last_news_url

async def fetch_rss_news(url):
    print(f"[DEBUG] GET {url}")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    print(f"[DEBUG] Status: {resp.status_code}")
    print(f"[DEBUG] Content: {resp.text[:500]}")  # Показати перші 500 символів
    feed = feedparser.parse(resp.content)
    news = []
    for entry in feed.entries:
        # Спроба взяти картинку з media_content
        image = entry.media_content[0]['url'] if 'media_content' in entry and entry.media_content else None
        # Якщо немає, спробувати дістати з <img src=...> у summary/description
        if not image:
            html = entry.summary if 'summary' in entry else entry.get('description', '')
            image = extract_first_img_src(html)
        news.append({
            'title': entry.title,
            'link': entry.link,
            'summary': entry.summary if 'summary' in entry else '',
            'published': entry.published if 'published' in entry else '',
            'image': image
        })
    return news

# Додаю функцію для отримання новин з Telegram Wotclue
WOTCLUE_TELEGRAM_RSS = "https://rsshub.app/telegram/channel/Wotclue"

async def fetch_telegram_wotclue_news():
    return await fetch_rss_news(WOTCLUE_TELEGRAM_RSS)
    
def clean_html(raw_html):
    clean_text = re.sub('<.*?>', '', raw_html)
    return unescape(clean_text).strip()

def extract_links(html):
    # Пошук усіх <a href="...">текст</a>
    return re.findall(r'<a\s+href=[\'\"](.*?)[\'\"].*?>(.*?)<\/a>', html)

def extract_first_img_src(html):
    match = re.search(r'<img[^>]+src=["\']([^"\']+)', html or "")
    return match.group(1) if match else None

# === ДОДАТКОВІ СТРУКТУРИ ДЛЯ TELEGRAM-КАНАЛІВ ===
TELEGRAM_CHANNELS_FILE = os.path.join(DATA_DIR, 'telegram_channels.json')
telegram_channels = {}  # {guild_id: [{telegram: str, discord_channel: int, last_url: str}]}

def load_telegram_channels():
    try:
        with open(TELEGRAM_CHANNELS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_telegram_channels():
    with open(TELEGRAM_CHANNELS_FILE, 'w', encoding='utf-8') as f:
        json.dump(telegram_channels, f, ensure_ascii=False, indent=2)

telegram_channels = load_telegram_channels()

# === КОМАНДА ДЛЯ ДОДАВАННЯ TELEGRAM-КАНАЛУ ===
@bot.tree.command(name="track_telegram", description="Відстежувати Telegram-канал і постити новини у Discord-канал")
@app_commands.describe(telegram="Username або посилання на Telegram-канал (без @)", channel="Канал для постингу новин")
async def track_telegram(interaction: discord.Interaction, telegram: str, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    guild_id = str(interaction.guild.id)
    # Формуємо RSS-лінк
    telegram = telegram.strip()
    # Вирізаємо https://, http://, t.me/, @
    telegram = re.sub(r'^(https?:\/\/)?(t\.me\/)?@?', '', telegram, flags=re.IGNORECASE)
    # Якщо це інвайт-код (починається з + або joinchat)
    if telegram.startswith('+') or telegram.lower().startswith('joinchat/'):
        # Для t.me/+xxxx або t.me/joinchat/xxxx
        telegram = telegram.replace('joinchat/', '+')
        rss_url = f"https://rsshub.app/telegram/channel/{telegram}"
    else:
        # Для username
        rss_url = f"https://rsshub.app/telegram/channel/{telegram}"
    if guild_id not in telegram_channels:
        telegram_channels[guild_id] = []
    # Перевірка на дубль
    for entry in telegram_channels[guild_id]:
        if entry['telegram'].lower() == telegram.lower():
            return await interaction.response.send_message(f"❌ Цей канал вже відстежується у цьому сервері!", ephemeral=True)
    telegram_channels[guild_id].append({
        'telegram': telegram,
        'rss_url': rss_url,
        'discord_channel': channel.id,
        'last_url': ''
    })
    save_telegram_channels()
    await interaction.response.send_message(f"✅ Додано відстеження Telegram-каналу: `{telegram}`. Новини будуть поститись у {channel.mention}", ephemeral=True)

# === ТАСК ДЛЯ ПЕРЕВІРКИ ВСІХ TELEGRAM-КАНАЛІВ ===
@tasks.loop(minutes=60)
async def telegram_channels_autopost():
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in telegram_channels:
            continue
        for entry in telegram_channels[guild_id]:
            try:
                news = await fetch_rss_news(entry['rss_url'])
                print(f"[DEBUG] Checking {entry['telegram']} ({entry['rss_url']})")
                print(f"[DEBUG] Fetched {len(news)} news items")
                if not news:
                    continue
                last_url = entry.get('last_url')
                print(f"[DEBUG] last_url: {last_url}")
                if not last_url:
                    entry['last_url'] = news[0]['link']
                    channel = guild.get_channel(entry['discord_channel'])
                    if channel:
                        # Витягуємо текст поста
                        post_text = news[0]['summary'] or news[0]['description'] or ''
                        post_text = clean_html(post_text).strip()
                        if not post_text:
                            post_text = news[0]['title']

                        embed = discord.Embed(
                            title=news[0]['title'],
                            url=news[0]['link'],
                            description=post_text,
                            color=discord.Color.teal(),
                            timestamp=datetime.utcnow()
                        )
                        embed.set_footer(text=f"Telegram | @{entry['telegram']}")
                        if news[0].get('image'):
                            embed.set_image(url=news[0]['image'])
                        await channel.send(embed=embed)
                        print(f"[DEBUG] Sent first news for {entry['telegram']} to Discord.")
                    save_telegram_channels()
                    continue
                new_entries = []
                for n in news:
                    if n['link'] == last_url:
                        break
                    new_entries.append(n)
                if not new_entries:
                    continue
                channel = guild.get_channel(entry['discord_channel'])
                if not channel:
                    continue
                for n in reversed(new_entries):
                    # Витягуємо текст поста
                    post_text = n.get('summary') or n.get('description') or ''
                    post_text = clean_html(post_text).strip()
                    if not post_text:
                        post_text = n.get('title', '')

                    embed = discord.Embed(
                        title=n['title'],
                        url=n['link'],
                        description=post_text,
                        color=discord.Color.teal(),
                        timestamp=datetime.utcnow()
                    )
                    embed.set_footer(text=f"Telegram | @{entry['telegram']}")
                    if n.get('image'):
                        embed.set_image(url=n['image'])
                    await channel.send(embed=embed)
                    entry['last_url'] = n['link']
                save_telegram_channels()
            except Exception as e:
                print(f"[Telegram Autopost] Error for {entry['telegram']}: {e}")

@bot.tree.command(name="untrack_telegram", description="Видалити Telegram-канал з автопосту для цього сервера")
@app_commands.describe(telegram="Username або посилання на Telegram-канал (без @)")
async def untrack_telegram(interaction: discord.Interaction, telegram: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    guild_id = str(interaction.guild.id)
    telegram = telegram.strip()
    # Вирізаємо https://, http://, t.me/, @
    telegram = re.sub(r'^(https?:\/\/)?(t\.me\/)?@?', '', telegram, flags=re.IGNORECASE)
    # Якщо це інвайт-код (починається з + або joinchat)
    if telegram.startswith('+') or telegram.lower().startswith('joinchat/'):
        # Для t.me/+xxxx або t.me/joinchat/xxxx
        telegram = telegram.replace('joinchat/', '+')
        rss_url = f"https://rsshub.app/telegram/channel/{telegram}"
    else:
        # Для username
        rss_url = f"https://rsshub.app/telegram/channel/{telegram}"
    if guild_id not in telegram_channels or not telegram_channels[guild_id]:
        return await interaction.response.send_message("❌ Для цього сервера не відстежується жодного Telegram-каналу", ephemeral=True)
    before = len(telegram_channels[guild_id])
    telegram_channels[guild_id] = [entry for entry in telegram_channels[guild_id] if entry['telegram'].lower() != telegram.lower()]
    after = len(telegram_channels[guild_id])
    if before == after:
        return await interaction.response.send_message(f"❌ Канал `{telegram}` не знайдено серед відстежуваних", ephemeral=True)
    save_telegram_channels()
    await interaction.response.send_message(f"✅ Telegram-канал `{telegram}` видалено з автопосту", ephemeral=True)

@bot.tree.command(name="list_tracked_telegram", description="Список Telegram-каналів, які відстежуються на цьому сервері")
async def list_tracked_telegram(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    guild_id = str(interaction.guild.id)
    if guild_id not in telegram_channels or not telegram_channels[guild_id]:
        return await interaction.response.send_message("ℹ️ На цьому сервері не відстежується жодного Telegram-каналу.", ephemeral=True)
    lines = []
    for entry in telegram_channels[guild_id]:
        channel = interaction.guild.get_channel(entry['discord_channel'])
        channel_mention = channel.mention if channel else f"ID: {entry['discord_channel']}"
        lines.append(f"• @{entry['telegram']} → {channel_mention}")
    msg = "**Відстежувані Telegram-канали:**\n" + "\n".join(lines)
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="set_nick_notify_channel", description="Встановити канал для повідомлень про зміну ніку")
@app_commands.describe(channel="Канал для повідомлень")
async def set_nick_notify_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    guild_id = str(interaction.guild.id)
    nick_notify_channel[guild_id] = channel.id
    save_nick_notify_channel()
    await interaction.response.send_message(f"✅ Канал для повідомлень про зміну ніку встановлено: {channel.mention}", ephemeral=True)

# Для збереження коду інвайту для кожного нового учасника
pending_invites = {}  # {user_id: invite_code}

MOD_CHANNEL_FILE = os.path.join(DATA_DIR, 'mod_channel.json')

def load_mod_channel():
    try:
        with open(MOD_CHANNEL_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_mod_channel():
    with open(MOD_CHANNEL_FILE, 'w', encoding='utf-8') as f:
        json.dump(mod_channel, f, ensure_ascii=False, indent=2)

mod_channel = load_mod_channel()  # {guild_id: channel_id}

@bot.tree.command(name="set_mod_channel", description="Встановити канал для заявок на модерацію")
@app_commands.describe(channel="Канал для заявок")
async def set_mod_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    guild_id = str(interaction.guild.id)
    mod_channel[guild_id] = channel.id
    save_mod_channel()
    await interaction.response.send_message(f"✅ Канал для заявок встановлено: {channel.mention}", ephemeral=True)

if __name__ == '__main__':
    print("Запуск бота...")
    bot.run(TOKEN) 
