import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta
import asyncio
from collections import defaultdict
import json
import random
import aiohttp
from typing import Optional, Literal
import pytz

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True
intents.invites = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Налаштування
application_settings = {}  # Зберігає канал для заявок
pending_applications = {}  # Тимчасовий кеш заявок (user_id: guild_id)

def load_application_settings():
    try:
        with open('application_settings.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_application_settings():
    with open('application_settings.json', 'w') as f:
        json.dump(application_settings, f)

application_settings = load_application_settings()

# Кнопки для модерації заявок
class ApplicationReviewView(ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @ui.button(label="✅ Прийняти", style=discord.ButtonStyle.green, custom_id="accept_application")
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        try:
            # Знаходимо користувача, який подав заявку
            member = await guild.fetch_member(self.user_id)
            
            # Видаляємо повідомлення з кнопками
            await interaction.message.delete()
            
            # Відправляємо підтвердження
            await interaction.response.send_message(
                f"✅ Заявку користувача {member.mention} прийнято!",
                ephemeral=True
            )
            
            # Логуємо дію
            log_channel_id = application_settings.get(str(guild.id), {}).get("log_channel")
            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    embed = discord.Embed(
                        title="✅ Заявку прийнято",
                        description=f"Користувач {member.mention} був прийнятий на сервер",
                        color=discord.Color.green()
                    )
                    embed.add_field(name="Модератор", value=interaction.user.mention)
                    await log_channel.send(embed=embed)
            
        except discord.NotFound:
            await interaction.response.send_message(
                "❌ Користувач не знайдений. Можливо, він покинув сервер.",
                ephemeral=True
            )

    @ui.button(label="❌ Відхилити", style=discord.ButtonStyle.red, custom_id="reject_application")
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        guild = interaction.guild
        try:
            # Знаходимо користувача
            member = await guild.fetch_member(self.user_id)
            
            # Видаляємо повідомлення з кнопками
            await interaction.message.delete()
            
            # Виганяємо користувача
            await member.kick(reason=f"Заявку відхилено модератором {interaction.user}")
            
            # Підтвердження
            await interaction.response.send_message(
                f"❌ Заявку користувача {member.mention} відхилено та його вигнано.",
                ephemeral=True
            )
            
            # Логування
            log_channel_id = application_settings.get(str(guild.id), {}).get("log_channel")
            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel:
                    embed = discord.Embed(
                        title="❌ Заявку відхилено",
                        description=f"Користувач {member.mention} був відхилений",
                        color=discord.Color.red()
                    )
                    embed.add_field(name="Модератор", value=interaction.user.mention)
                    await log_channel.send(embed=embed)
                    
        except discord.NotFound:
            await interaction.response.send_message(
                "❌ Користувач не знайдений. Можливо, він вже покинув сервер.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ У бота недостатньо прав для вигнання користувача.",
                ephemeral=True
            )

# Команда для налаштування каналу заявок
@bot.tree.command(name="setup_application_channel", description="Налаштувати канал для модерації заявок")
@app_commands.describe(
    channel="Канал, куди будуть надходити заявки",
    log_channel="Канал для логування (необов'язково)"
)
async def setup_application_channel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    log_channel: Optional[discord.TextChannel] = None
):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Потрібні права адміністратора!",
            ephemeral=True
        )
    
    # Зберігаємо налаштування
    application_settings[str(interaction.guild.id)] = {
        "channel_id": channel.id,
        "log_channel": log_channel.id if log_channel else None
    }
    save_application_settings()
    
    await interaction.response.send_message(
        f"✅ Канал для заявок налаштовано: {channel.mention}\n"
        f"📝 Канал для логування: {log_channel.mention if log_channel else 'не вказано'}",
        ephemeral=True
    )

# Обробник події "очікує верифікації"
@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    
    guild_id = str(member.guild.id)
    
    # Перевіряємо, чи налаштовано систему заявок для цього сервера
    if guild_id not in application_settings:
        return  # Якщо ні - користувач проходить автоматично
    
    # Отримуємо канал для заявок
    channel_id = application_settings[guild_id]["channel_id"]
    channel = member.guild.get_channel(channel_id)
    if not channel:
        return
    
    # Створюємо embed з інформацією про користувача
    embed = discord.Embed(
        title="📝 Нова заявка на вступ",
        description=f"Користувач {member.mention} хоче приєднатися до сервера.",
        color=discord.Color.orange()
    )
    embed.add_field(name="Ім'я", value=member.display_name, inline=True)
    embed.add_field(name="ID", value=member.id, inline=True)
    embed.add_field(name="Дата реєстрації", value=member.created_at.strftime("%d.%m.%Y"), inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    
    # Відправляємо повідомлення з кнопками
    view = ApplicationReviewView(user_id=member.id)
    await channel.send(embed=embed, view=view)
    
    # Додаємо до тимчасового кешу
    pending_applications[member.id] = guild_id

# Обробник події "прийнято/відхилено"
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # Якщо користувач пройшов верифікацію (отримав роль)
    if len(before.roles) < len(after.roles):
        guild_id = str(after.guild.id)
        if after.id in pending_applications and pending_applications[after.id] == guild_id:
            pending_applications.pop(after.id)  # Видаляємо з кешу

@bot.event
async def on_ready():
    print(f"Бот {bot.user} готовий до роботи!")
    await bot.tree.sync()
    bot.add_view(ApplicationReviewView(user_id=0))  # Для персистентних кнопок

TOKEN = os.getenv('DISCORD_TOKEN')
bot.run(TOKEN)