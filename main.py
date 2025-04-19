import discord
from discord import app_commands
from discord.ext import commands
import os
from keep_alive import keep_alive

intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'Бот {bot.user} успішно підключений до Discord!')
    try:
        synced = await bot.tree.sync()
        print(f"Синхронізовано {len(synced)} команд")
    except Exception as e:
        print(f"Помилка синхронізації команд: {e}")

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
        # Враховуємо тільки користувачів, які не є ботом
        if not member.bot and len(member.roles) == 1:
            try:
                await member.kick(reason="Має тільки роль @everyone")
                deleted_count += 1
            except Exception as e:
                print(f"Не вдалося видалити {member}: {e}")
    
    await interaction.followup.send(f"Видалено {deleted_count} користувачів, які мали тільки роль @everyone.", ephemeral=True)

# Команда для видалення користувачів з певною роллю
@bot.tree.command(name="remove_by_role", description="Видаляє всіх користувачів з обраною роллю")
@app_commands.describe(role="Роль, користувачів якої потрібно видалити")
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
        # Не видаляємо ботів
        if not member.bot:
            try:
                await member.kick(reason=f"Видалення користувачів ролі {role.name}")
                deleted_count += 1
            except Exception as e:
                print(f"Не вдалося видалити {member}: {e}")
    
    await interaction.followup.send(f"Видалено {deleted_count} користувачів з роллю {role.name}.", ephemeral=True)

# Нова команда для виведення списку користувачів без ролей
@bot.tree.command(name="list_no_roles", description="Виводить список користувачів без жодних ролей (крім @everyone)")
async def list_no_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Ця команда доступна тільки адміністраторам.", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    members_without_roles = []
    
    for member in interaction.guild.members:
        # Враховуємо тільки користувачів, які не є ботом і мають тільки роль @everyone
        if not member.bot and len(member.roles) == 1:
            members_without_roles.append(f"{member.display_name} ({member.id})")
    
    if not members_without_roles:
        await interaction.followup.send("На сервері немає користувачів без ролей.", ephemeral=True)
        return
    
    # Розділяємо список на частини, щоб уникнути перевищення ліміту Discord (2000 символів)
    chunks = [members_without_roles[i:i + 20] for i in range(0, len(members_without_roles), 20)]
    
    for i, chunk in enumerate(chunks):
        message = f"Користувачі без ролей (частина {i+1}):\n" + "\n".join(chunk)
        if i == 0:
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)

keep_alive()
bot.run(os.getenv('DISCORD_TOKEN'))
