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

# ========== WoT NEWS AUTOPOST ========== 
wot_news_settings = {}  # guild_id: channel_id
wot_news_last_url = {}  # guild_id: last_news_url

WOT_RSS_URL = "https://worldoftanks.eu/uk/rss/news/"

@bot.tree.command(name="setup_wot_news", description="Вказати канал для автоматичних новин World of Tanks")
@app_commands.describe(channel="Канал для новин")
async def setup_wot_news(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Тільки для адміністраторів", ephemeral=True)
        return
    wot_news_settings[str(interaction.guild.id)] = channel.id
    await interaction.response.send_message(f"✅ Канал для WoT новин встановлено: {channel.mention}", ephemeral=True)

async def fetch_wot_news():
    feed = feedparser.parse(WOT_RSS_URL)
    news = []
    for entry in feed.entries:
        news.append({
            'title': entry.title,
            'link': entry.link,
            'summary': entry.summary,
            'published': entry.published if 'published' in entry else '',
            'image': entry.media_content[0]['url'] if 'media_content' in entry and entry.media_content else None
        })
    return news

# WoT офіційні новини
@tasks.loop(minutes=2)
async def wot_official_news_task():
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in wot_news_settings:
            continue
        channel = guild.get_channel(wot_news_settings[guild_id])
        if not channel:
            continue
        try:
            news = await fetch_wot_news()
            if not news:
                continue
            last_url = wot_news_last_url.get(guild_id)
            if not last_url:
                wot_news_last_url[guild_id] = news[0]['link']
                continue
            new_entries = []
            for entry in news:
                if entry['link'] == last_url:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.orange()
                )
                embed.set_footer(text="World of Tanks | Офіційна новина")
                await channel.send(embed=embed)
                wot_news_last_url[guild_id] = entry['link']
        except Exception as e:
            print(f"[WoT Official News] Error for guild {guild_id}: {e}")

# WoT автопост
@tasks.loop(minutes=60)
async def wot_news_autopost():
    await asyncio.sleep(random.randint(0, 7200))
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in wot_news_settings:
            continue
        channel = guild.get_channel(wot_news_settings[guild_id])
        if not channel:
            continue
        try:
            news = await fetch_wot_news()
            if not news:
                continue
            last_url = wot_news_last_url.get(guild_id)
            if not last_url:
                wot_news_last_url[guild_id] = news[0]['link']
                continue
            new_entries = []
            for entry in news:
                if entry['link'] == last_url:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.orange()
                )
                embed.set_footer(text="World of Tanks | Автооновлення новин")
                await channel.send(embed=embed)
                wot_news_last_url[guild_id] = entry['link']
                break  # Надсилаємо лише одну новину за цикл
        except Exception as e:
            print(f"[WoT News] Error for guild {guild_id}: {e}")

# WoT зовнішні новини (Google News, YouTube, WoT Express)
@tasks.loop(minutes=15)
async def wot_external_news_task():
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in wot_news_settings:
            continue
        channel = guild.get_channel(wot_news_settings[guild_id])
        if not channel:
            continue
        # Google News
        try:
            news = await fetch_rss_news(GOOGLE_NEWS_RSS)
            last_urls = wot_external_news_last.setdefault(guild_id, set())
            if not news:
                continue
            if not last_urls:
                last_urls.add(news[0]['link'])
                continue
            new_entries = []
            for entry in news:
                if entry['link'] in last_urls:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="World of Tanks | Новина з інтернету")
                await channel.send(embed=embed)
                last_urls.add(entry['link'])
        except Exception as e:
            print(f"[Google News] Error: {e}")
        # YouTube
        try:
            news = await fetch_rss_news(YOUTUBE_WOT_RSS)
            last_urls = wot_external_news_last.setdefault(guild_id, set())
            if not news:
                continue
            if not last_urls:
                last_urls.add(news[0]['link'])
                continue
            new_entries = []
            for entry in news:
                if entry['link'] in last_urls:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="World of Tanks | Новина з інтернету")
                await channel.send(embed=embed)
                last_urls.add(entry['link'])
        except Exception as e:
            print(f"[YouTube] Error: {e}")
        # WoT Express
        try:
            news = await fetch_rss_news(WOTEXPRESS_RSS)
            last_urls = wot_external_news_last.setdefault(guild_id, set())
            if not news:
                continue
            if not last_urls:
                last_urls.add(news[0]['link'])
                continue
            new_entries = []
            for entry in news:
                if entry['link'] in last_urls:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="World of Tanks | Новина з інтернету")
                await channel.send(embed=embed)
                last_urls.add(entry['link'])
        except Exception as e:
            print(f"[WoT Express] Error: {e}")

# Telegram Wotclue новини
@tasks.loop(minutes=10)
async def telegram_wotclue_news_task():
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in wot_news_settings:
            continue
        channel = guild.get_channel(wot_news_settings[guild_id])
        if not channel:
            continue
        try:
            news = await fetch_telegram_wotclue_news()
            if not news:
                continue
            last_url = wotclue_news_last_url.get(guild_id)
            if not last_url:
                wotclue_news_last_url[guild_id] = news[0]['link']
                continue
            new_entries = []
            for entry in news:
                if entry['link'] == last_url:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.teal(),
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="Wotclue EU | Telegram")
                await channel.send(embed=embed)
                wotclue_news_last_url[guild_id] = entry['link']
        except Exception as e:
            print(f"[Telegram Wotclue News] Error: {e}")

# Telegram WoT UA новини
@tasks.loop(minutes=10)
async def telegram_wotua_news_task():
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in wot_news_settings:
            continue
        channel = guild.get_channel(wot_news_settings[guild_id])
        if not channel:
            continue
        try:
            news = await fetch_rss_news(WOT_UA_TELEGRAM_RSS)
            if not news:
                continue
            last_url = wotua_news_last_url.get(guild_id)
            if not last_url:
                wotua_news_last_url[guild_id] = news[0]['link']
                continue
            new_entries = []
            for entry in news:
                if entry['link'] == last_url:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.teal(),
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="World of Tanks Ukraine | Telegram")
                await channel.send(embed=embed)
                wotua_news_last_url[guild_id] = entry['link']
        except Exception as e:
            print(f"[Telegram WoT UA News] Error: {e}")

# Telegram WOTCLUE EU новини
@tasks.loop(minutes=10)
async def telegram_wotclue_eu_news_task():
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in wot_news_settings:
            continue
        channel = guild.get_channel(wot_news_settings[guild_id])
        if not channel:
            continue
        try:
            news = await fetch_rss_news(WOTCLUE_EU_TELEGRAM_RSS)
            if not news:
                continue
            last_url = wotclue_eu_news_last_url.get(guild_id)
            if not last_url:
                wotclue_eu_news_last_url[guild_id] = news[0]['link']
                continue
            new_entries = []
            for entry in news:
                if entry['link'] == last_url:
                    break
                new_entries.append(entry)
            for entry in reversed(new_entries):
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.teal(),
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="WOTCLUE EU | Telegram")
                await channel.send(embed=embed)
                wotclue_eu_news_last_url[guild_id] = entry['link']
        except Exception as e:
            print(f"[Telegram WOTCLUE EU News] Error: {e}")

# ========== ДОДАТКОВІ АДМІН-КОМАНДИ ========== 

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
WOTEXPRESS_RSS = "https://wotexpress.info/rss/news/"
WOT_UA_TELEGRAM_RSS = "https://rsshub.app/telegram/channel/worldoftanksua_official"
WOTCLUE_EU_TELEGRAM_RSS = "https://rsshub.app/telegram/channel/Wotclue_eu"
wotclue_eu_news_last_url = {}  # guild_id: last_news_url

async def fetch_rss_news(url):
    feed = feedparser.parse(url)
    news = []
    for entry in feed.entries:
        news.append({
            'title': entry.title,
            'link': entry.link,
            'summary': entry.summary if 'summary' in entry else '',
            'published': entry.published if 'published' in entry else '',
            'image': entry.media_content[0]['url'] if 'media_content' in entry and entry.media_content else None
        })
    return news

# Публікація новин з черги з рандомною затримкою
@tasks.loop(minutes=10)
async def wot_external_news_publisher():
    if not external_news_queue:
        return
    item = external_news_queue.pop(0)
    guild_id = item['guild_id']
    entry = item['entry']
    channel_id = wot_news_settings.get(guild_id)
    if not channel_id:
        return
    for guild in bot.guilds:
        if str(guild.id) == guild_id:
            channel = guild.get_channel(channel_id)
            if channel:
                embed = discord.Embed(
                    title=entry['title'],
                    url=entry['link'],
                    description=clean_html(entry['summary']),
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                # НЕ додаємо поля з посиланнями
                embed.set_footer(text="World of Tanks | Новина з інтернету")
                # Рандомна затримка перед публікацією
                delay = random.randint(3600, 10800)  # 1-3 години
                bot.loop.create_task(asyncio.sleep(delay))
                bot.loop.create_task(channel.send(embed=embed))
            break

def clean_html(raw_html):
    clean_text = re.sub('<.*?>', '', raw_html)
    return unescape(clean_text).strip()

def extract_links(html):
    # Пошук усіх <a href="...">текст</a>
    return re.findall(r'<a\s+href=[\'\"](.*?)[\'\"].*?>(.*?)<\/a>', html)

WG_API_KEY = "180fc971b4111ed71923f2135aa73b74"
CLAN_ID = 500310423
CLAN_ROLE_ID = 1331255972303470603

async def check_nickname_in_clan(nickname: str) -> bool:
    async with aiohttp.ClientSession() as session:
        # 1. Знайти account_id по нікнейму
        url = f"https://api.worldoftanks.eu/wot/account/list/?application_id={WG_API_KEY}&search={nickname}"
        async with session.get(url) as resp:
            data = await resp.json()
            if not data['data']:
                return False
            account_id = data['data'][0]['account_id']
        # 2. Перевірити клан
        url = f"https://api.worldoftanks.eu/wot/clans/accountinfo/?application_id={WG_API_KEY}&account_id={account_id}"
        async with session.get(url) as resp:
            data = await resp.json()
            clan = data['data'][str(account_id)]['clan_id']
            return clan == CLAN_ID

from discord.ui import Modal, TextInput

class NicknameModal(Modal, title="Введіть свій WoT нікнейм"):
    nickname = TextInput(label="Ігровий нікнейм", required=True, max_length=24)

    def __init__(self, member, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.member = member

    async def on_submit(self, interaction):
        nickname = self.nickname.value.strip()
        in_clan = await check_nickname_in_clan(nickname)
        if in_clan:
            role = interaction.guild.get_role(CLAN_ROLE_ID)
            if role:
                await self.member.add_roles(role)
                await interaction.response.send_message("✅ Ви додані до клану!", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Роль не знайдена!", ephemeral=True)
        else:
            try:
                await self.member.send("❌ Ви не перебуваєте у клані. Зверніться до офіцерів клану.")
            except:
                pass
            await interaction.response.send_message("❌ Ви не перебуваєте у клані.", ephemeral=True)

# --- Додаємо виклик модального вікна після приєднання через спеціальне запрошення ---

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
        guild_id = str(guild.id)
        # WoT офіційні новини
        if guild_id in wot_news_settings:
            news = await fetch_wot_news()
            if news:
                wot_news_last_url[guild_id] = news[0]['link']
        # Telegram Wotclue
        if guild_id in wot_news_settings:
            news = await fetch_telegram_wotclue_news()
            if news:
                wotclue_news_last_url[guild_id] = news[0]['link']
        # Google News
        if guild_id in wot_news_settings:
            news = await fetch_rss_news(GOOGLE_NEWS_RSS)
            if news:
                wot_external_news_last.setdefault(guild_id, set()).add(news[0]['link'])
        # YouTube
        if guild_id in wot_news_settings:
            news = await fetch_rss_news(YOUTUBE_WOT_RSS)
            if news:
                wot_external_news_last.setdefault(guild_id, set()).add(news[0]['link'])
        # WoT Express
        if guild_id in wot_news_settings:
            news = await fetch_rss_news(WOTEXPRESS_RSS)
            if news:
                wot_external_news_last.setdefault(guild_id, set()).add(news[0]['link'])
        # Telegram WoT UA
        if guild_id in wot_news_settings:
            news = await fetch_rss_news(WOT_UA_TELEGRAM_RSS)
            if news:
                wotua_news_last_url[guild_id] = news[0]['link']
        # Telegram WOTCLUE EU
        if guild_id in wot_news_settings:
            news = await fetch_rss_news(WOTCLUE_EU_TELEGRAM_RSS)
            if news:
                wotclue_eu_news_last_url[guild_id] = news[0]['link']
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації: {e}")
    check_voice_activity.start()
    update_voice_activity.start()
    wot_news_autopost.start()
    wot_official_news_task.start()
    wot_external_news_task.start()
    wot_external_news_publisher.start()
    telegram_wotclue_news_task.start()
    telegram_wotua_news_task.start()
    telegram_wotclue_eu_news_task.start()

if __name__ == '__main__':
    print("Запуск бота...")
    bot.run(TOKEN) 
