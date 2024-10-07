# bot.py
import os

import discord
from paceping import PacePingBot
from dotenv import load_dotenv

intents = discord.Intents.default()
intents.message_content = True
statusMessage = ""

load_dotenv()

bot = PacePingBot(command_prefix='/',intents=intents)

bot.run(os.getenv('DISCORD_TOKEN'))