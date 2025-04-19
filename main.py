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

### Нова команда: показ користувачів з роллю ###
@bot.tree.command(name="show_role_users", description="Показує список користувачів з обраною роллю")
@app_commands.describe(role="Роль для перегляду")
async def show_role_users(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    
    # Фільтруємо ботів і готуємо список
    members = [f"{member.mention} ({member.display_name})" 
               for member in role.members 
               if not member.bot]
    
    if not members:
        await interaction.followup.send(f"🔍 Немає користувачів з роллю **{role.name}**.", ephemeral=True)
        return
    
    # Форматуємо вивід (розбиваємо на частини, якщо список великий)
    chunk_size = 15  # Кількість користувачів на повідомлення
    for i in range(0, len(members), chunk_size):
        chunk = members[i:i + chunk_size]
        embed = discord.Embed(
            title=f"👥 Користувачі з роллю {role.name} ({len(members)} всього)",
            description="\n".join(chunk),
            color=role.color  # Беремо колір ролі для красивого відображення
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

### Попередні команди (без змін) ###
@bot.tree.command(name="remove_default_only", description="Видаляє користувачів, які мають тільки роль @everyone")
async def remove_default_only(interaction: discord.Interaction):
    # ... (ваш існуючий код) ...

@bot.tree.command(name="remove_by_role", description="Видаляє всіх користувачів з обраною роллю")
@app_commands.describe(role="Роль для видалення")
async def remove_by_role(interaction: discord.Interaction, role: discord.Role):
    # ... (ваш існуючий код) ...

@bot.tree.command(name="list_no_roles", description="Виводить список користувачів без ролей (крім @everyone)")
async def list_no_roles(interaction: discord.Interaction):
    # ... (ваш існуючий код) ...

bot.run(os.getenv('DISCORD_TOKEN'))
