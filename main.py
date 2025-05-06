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

# Система запитів на приєднання
request_channels = {}
pending_approvals = {}

class ApprovalView(ui.View):
    def __init__(self, member: discord.Member, request_data: dict):
        super().__init__(timeout=None)
        self.member = member
        self.request_data = request_data
    
    @ui.button(label="✅ Схвалити", style=discord.ButtonStyle.green, custom_id="approve_member")
    async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Тільки адміністратори можуть схвалювати запити.", ephemeral=True)
            return
        
        guild = interaction.guild
        role_id = self.request_data.get("default_role_id")
        
        if role_id:
            role = guild.get_role(role_id)
            if role:
                try:
                    await self.member.add_roles(role)
                except:
                    pass
        
        # Видаляємо зі списку очікувальних
        if str(guild.id) in pending_approvals and self.member.id in pending_approvals[str(guild.id)]:
            pending_approvals[str(guild.id)].remove(self.member.id)
        
        await interaction.response.send_message(f"✅ {self.member.mention} було схвалено до сервера!", ephemeral=False)
        
        # Відправляємо повідомлення користувачу
        try:
            await self.member.send("🎉 Ваш запит на приєднання до сервера було схвалено!")
        except:
            pass
        
        # Видаляємо повідомлення з запитом
        try:
            await interaction.message.delete()
        except:
            pass
    
    @ui.button(label="❌ Відхилити", style=discord.ButtonStyle.red, custom_id="reject_member")
    async def reject_button(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Тільки адміністратори можуть відхиляти запити.", ephemeral=True)
            return
        
        guild = interaction.guild
        
        # Видаляємо зі списку очікувальних
        if str(guild.id) in pending_approvals and self.member.id in pending_approvals[str(guild.id)]:
            pending_approvals[str(guild.id)].remove(self.member.id)
        
        # Видаляємо користувача з сервера
        try:
            await self.member.kick(reason="Запит на приєднання відхилено адміністратором")
        except:
            pass
        
        await interaction.response.send_message(f"❌ Запит {self.member.mention} було відхилено.", ephemeral=False)
        
        # Видаляємо повідомлення з запитом
        try:
            await interaction.message.delete()
        except:
            pass

def load_data():
    global invite_roles, welcome_messages, request_channels, pending_approvals
    try:
        with open('data.json', 'r') as f:
            data = json.load(f)
            invite_roles = data.get('invite_roles', {})
            welcome_messages = data.get('welcome_messages', {})
            request_channels = data.get('request_channels', {})
            pending_approvals = data.get('pending_approvals', {})
    except (FileNotFoundError, json.JSONDecodeError):
        invite_roles = {}
        welcome_messages = {}
        request_channels = {}
        pending_approvals = {}

def save_data():
    data = {
        'invite_roles': invite_roles,
        'welcome_messages': welcome_messages,
        'request_channels': request_channels,
        'pending_approvals': pending_approvals
    }
    with open('data.json', 'w') as f:
        json.dump(data, f)

async def update_invite_cache(guild):
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
    except discord.Forbidden:
        print(f"Немає дозволу на перегляд запрошень для сервера {guild.name}")
    except Exception as e:
        print(f"Помилка оновлення кешу запрошень: {e}")

@bot.event
async def on_ready():
    print(f'Бот {bot.user} онлайн!')
    load_data()
    
    for guild in bot.guilds:
        await update_invite_cache(guild)
    
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації: {e}")
    
    check_voice_activity.start()
    update_voice_activity.start()

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    
    guild = member.guild
    
    # Перевіряємо чи налаштована система запитів для цього сервера
    if str(guild.id) not in request_channels:
        return
    
    # Додаємо користувача до списку очікувальних
    if str(guild.id) not in pending_approvals:
        pending_approvals[str(guild.id)] = []
    
    pending_approvals[str(guild.id)].append(member.id)
    save_data()
    
    # Отримуємо канал для запитів
    channel_id = request_channels[str(guild.id)]["channel_id"]
    channel = guild.get_channel(channel_id)
    
    if not channel:
        return
    
    # Створюємо embed з інформацією про користувача
    kyiv_time = datetime.now(pytz.timezone('Europe/Kiev'))
    embed = discord.Embed(
        title="🔔 Новий запит на приєднання",
        description=f"Користувач {member.mention} хоче приєднатися до сервера.",
        color=discord.Color.orange(),
        timestamp=kyiv_time
    )
    
    embed.set_author(name=f"{member.display_name} ({member.id})", icon_url=member.display_avatar.url)
    
    embed.add_field(
        name="📝 Інформація про користувача",
        value=f"• Створено: {member.created_at.strftime('%d.%m.%Y')}\n"
              f"• Приєднався: {kyiv_time.strftime('%d.%m.%Y о %H:%M')}",
        inline=False
    )
    
    embed.set_footer(text=f"ID: {member.id}")
    
    # Відправляємо повідомлення з кнопками
    view = ApprovalView(member, request_channels[str(guild.id)])
    await channel.send(embed=embed, view=view)

@bot.tree.command(name="setup_approval_channel", description="Налаштувати канал для запитів на приєднання")
@app_commands.describe(
    channel="Канал для запитів на приєднання",
    default_role="Роль за замовчуванням для схвалених користувачів (необов'язково)"
)
async def setup_approval_channel(interaction: discord.Interaction, 
                               channel: discord.TextChannel,
                               default_role: Optional[discord.Role] = None):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    request_channels[str(interaction.guild.id)] = {
        "channel_id": channel.id,
        "default_role_id": default_role.id if default_role else None
    }
    save_data()
    
    await interaction.response.send_message(
        f"✅ Канал {channel.mention} тепер буде використовуватися для запитів на приєднання.\n"
        f"Коли новий користувач приєднається до сервера, у цей канал буде надіслано повідомлення з кнопками для схвалення.\n"
        f"{f'Схвалені користувачі отримуватимуть роль {default_role.mention}' if default_role else ''}",
        ephemeral=True
    )

@bot.tree.command(name="disable_approval_system", description="Вимкнути систему схвалення нових учасників")
async def disable_approval_system(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Потрібні права адміністратора", ephemeral=True)
    
    if str(interaction.guild.id) in request_channels:
        request_channels.pop(str(interaction.guild.id))
        save_data()
    
    await interaction.response.send_message(
        "✅ Система схвалення нових учасників вимкнена. Нові користувачі зможуть приєднуватися без схвалення.",
        ephemeral=True
    )

# ... (інші команди залишаються незмінними) ...

TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен Discord")

if __name__ == '__main__':
    print("Запуск бота...")
    bot.run(TOKEN)