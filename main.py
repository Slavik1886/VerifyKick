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

# Команда для показу користувачів з певною роллю
@bot.tree.command(name="show_role_users", description="Показує список користувачів з обраною роллю")
@app_commands.describe(role="Роль для перегляду користувачів")
async def show_role_users(interaction: discord.Interaction, role: discord.Role):
    await interaction.response.defer(ephemeral=True)
    
    members = [member for member in role.members if not member.bot]
    
    if not members:
        await interaction.followup.send(f"Немає користувачів з роллю {role.name}.", ephemeral=True)
        return
    
    members_list = "\n".join([f"{member.mention} ({member.display_name})" for member in members])
    
    # Розділяємо повідомлення, якщо воно занадто довге
    chunks = [members_list[i:i+1500] for i in range(0, len(members_list), 1500)]
    
    for i, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=f"Користувачі з роллю {role.name} ({len(members)})",
            description=chunk,
            color=role.color
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

# Інші команди (remove_default_only, remove_by_role, list_no_roles) залишаються без змін
# ... (тут ваш існуючий код інших команд) ...

bot.run(os.getenv('DISCORD_TOKEN'))
