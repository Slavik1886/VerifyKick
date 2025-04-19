import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Словники для відстеження активності
voice_time_tracker = {}
tracked_channels = {}

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
    for guild_id, channel_id in tracked_channels.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
            
        channel = guild.get_channel(channel_id)
        if not channel:
            continue
            
        for member in channel.members:
            if member.bot:
                continue
                
            if member.id not in voice_time_tracker:
                voice_time_tracker[member.id] = current_time
            else:
                time_in_channel = current_time - voice_time_tracker[member.id]
                if time_in_channel > timedelta(minutes=30):
                    try:
                        await member.send(
                            f"🔔 Ви знаходитесь у голосовому каналі {channel.name} вже більше 30 хвилин. "
                            "Будь ласка, зробіть перерву, щоб не перевантажувати сервер."
                        )
                        voice_time_tracker[member.id] = current_time
                    except Exception as e:
                        print(f"Не вдалося надіслати повідомлення {member}: {e}")

@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.id in tracked_channels.values():
        if member.id in voice_time_tracker:
            del voice_time_tracker[member.id]

@bot.tree.command(name="track_voice", description="Відстежувати перебування у голосовому каналі")
@app_commands.describe(channel="Голосовий канал для відстеження")
async def track_voice(interaction: discord.Interaction, channel: discord.VoiceChannel):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    tracked_channels[interaction.guild_id] = channel.id
    await interaction.response.send_message(
        f"🔊 Відстежування голосового каналу {channel.mention} активовано. "
        "Користувачі отримуватимуть сповіщення після 30 хвилин безперервного перебування.",
        ephemeral=True
    )

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

bot.run(os.getenv('DISCORD_TOKEN'))
