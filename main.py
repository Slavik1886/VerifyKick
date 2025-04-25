import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
from datetime import datetime, timedelta, time
import asyncio
import aiohttp
import pytz

# Конфігурація
intents = discord.Intents.default()
intents.members = True
intents.guilds = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Налаштування Wargaming API
WG_API_KEY = "180fc971b4111ed71923f2135aa73b74"  # Отримайте на https://developers.wargaming.net/
REGION = "eu"  # Змінюйте на ваш регіон: "eu", "na", "asia" тощо

# ================= Нові функції для роботи з API ====================

async def get_player_info(session, player_name):
    """Отримати загальну статистику гравця."""
    url = f"https://api.worldoftanks.{REGION}/wot/account/list/"
    params = {
        "application_id": WG_API_KEY,
        "search": player_name,
        "fields": "account_id,nickname",
        "type": "exact"
    }
    async with session.get(url, params=params) as response:
        data = await response.json()
        if data['status'] == 'ok' and data['data']:
            return data['data'][0]  # account_id і nickname
    return None

async def get_tank_stats(session, account_id):
    """Отримати статистику танків для гравця."""
    url = f"https://api.worldoftanks.{REGION}/wot/tanks/stats/"
    params = {
        "application_id": WG_API_KEY,
        "account_id": account_id,
        "fields": "tank_id,statistics.battles,statistics.wins"
    }
    async with session.get(url, params=params) as response:
        data = await response.json()
        if data['status'] == 'ok':
            return data['data'].get(str(account_id), [])
    return []

async def get_clan_global_map_stats(session, clan_id):
    """Отримати статистику клану на глобальній мапі."""
    url = f"https://api.worldoftanks.{REGION}/wot/globalmap/claninfo/"
    params = {
        "application_id": WG_API_KEY,
        "clan_id": clan_id,
        "fields": "provinces,ratings"
    }
    async with session.get(url, params=params) as response:
        data = await response.json()
        if data['status'] == 'ok':
            return data['data'].get(str(clan_id), {})
    return {}

# ================= Нові команди для Discord-бота ====================

@bot.tree.command(name="player_stats", description="Отримати загальну статистику гравця WoT")
@app_commands.describe(player_name="Ім'я гравця")
async def player_stats(interaction: discord.Interaction, player_name: str):
    async with aiohttp.ClientSession() as session:
        player_info = await get_player_info(session, player_name)
        if not player_info:
            await interaction.response.send_message(f"❌ Гравця з іменем `{player_name}` не знайдено!", ephemeral=True)
            return

        account_id = player_info['account_id']
        nickname = player_info['nickname']

        # Отримання детальної статистики
        url = f"https://api.worldoftanks.{REGION}/wot/account/info/"
        params = {
            "application_id": WG_API_KEY,
            "account_id": account_id,
            "fields": "statistics.all.battles,statistics.all.wins,global_rating"
        }
        async with session.get(url, params=params) as response:
            data = await response.json()
            if data['status'] != 'ok' or not data['data']:
                await interaction.response.send_message(f"❌ Помилка отримання статистики для `{nickname}`", ephemeral=True)
                return

            stats = data['data'][str(account_id)]['statistics']['all']
            battles = stats['battles']
            wins = stats['wins']
            global_rating = data['data'][str(account_id)].get('global_rating', 'Немає даних')

            win_rate = (wins / battles) * 100 if battles else 0

            embed = discord.Embed(
                title=f"📊 Статистика гравця: {nickname}",
                color=discord.Color.blue()
            )
            embed.add_field(name="⚔️ Бої", value=battles, inline=True)
            embed.add_field(name="🏆 Відсоток перемог", value=f"{win_rate:.2f}%", inline=True)
            embed.add_field(name="🌐 Глобальний рейтинг", value=global_rating, inline=True)

            await interaction.response.send_message(embed=embed)

@bot.tree.command(name="tank_stats", description="Отримати статистику танків для гравця")
@app_commands.describe(player_name="Ім'я гравця")
async def tank_stats(interaction: discord.Interaction, player_name: str):
    async with aiohttp.ClientSession() as session:
        player_info = await get_player_info(session, player_name)
        if not player_info:
            await interaction.response.send_message(f"❌ Гравця з іменем `{player_name}` не знайдено!", ephemeral=True)
            return

        account_id = player_info['account_id']
        tanks = await get_tank_stats(session, account_id)

        if not tanks:
            await interaction.response.send_message(f"❌ Не вдалося отримати статистику танків для `{player_name}`", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🚗 Статистика танків гравця: {player_name}",
            color=discord.Color.green()
        )
        for tank in tanks[:5]:  # Показати лише топ-5 танків
            battles = tank['statistics']['battles']
            wins = tank['statistics']['wins']
            win_rate = (wins / battles) * 100 if battles else 0
            embed.add_field(name=f"Танк ID: {tank['tank_id']}",
                            value=f"⚔️ Бої: {battles}, 🏆 Відсоток перемог: {win_rate:.2f}%",
                            inline=False)

        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="clan_global_map", description="Отримати статистику клану на глобальній мапі")
@app_commands.describe(clan_id="ID клану WoT")
async def clan_global_map(interaction: discord.Interaction, clan_id: int):
    async with aiohttp.ClientSession() as session:
        stats = await get_clan_global_map_stats(session, clan_id)
        if not stats:
            await interaction.response.send_message(f"❌ Не вдалося отримати статистику для клану з ID `{clan_id}`", ephemeral=True)
            return

        provinces = stats.get('provinces', [])
        ratings = stats.get('ratings', {})

        embed = discord.Embed(
            title=f"🌍 Глобальна мапа: Клан ID {clan_id}",
            color=discord.Color.orange()
        )
        embed.add_field(name="🏘️ Провінції", value=len(provinces), inline=True)
        embed.add_field(name="📊 Рейтинг", value=ratings.get('efficiency', 'Немає даних'), inline=True)

        await interaction.response.send_message(embed=embed)

# ==================== Запуск бота ====================
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Відсутній токен Discord")
bot.run(TOKEN)