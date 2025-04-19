import discord
from discord import app_commands
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно підключений до Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації команд: {e}")

# Нова команда: показ користувачів з роллю
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

# Команда для видалення користувачів тільки з роллю @everyone
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

# Команда для видалення користувачів з певною роллю
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

# Команда для виведення списку користувачів без ролей
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

bot.run(os.getenv('DISCORD_TOKEN'))
