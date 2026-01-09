import os
import logging
import discord
from discord.ext import commands

# ===== CONFIG =====
TOKEN = os.getenv("DISCORD_TOKEN")  # DO NOT hardcode your token
PREFIX = "!"

INTENTS = discord.Intents.default()
INTENTS.message_content = True
INTENTS.members = True

logging.basicConfig(level=logging.INFO)

# ===== COGS TO LOAD =====
EXTENSIONS = [
    # core / admin
    "bot_commands.restart",
    "bot_commands.paginated_help",
    # utilities
    "bot_commands.ping",
    "bot_commands.roll",
    # info
    "bot_commands.userinfo",
    "bot_commands.serverinfo",
    # moderation
    "bot_commands.clear",
    "bot_commands.kick",
    "bot_commands.ban",
    "bot_commands.unban",
    "bot_commands.mute",
    "bot_commands.unmute",
    # heavy hitters (unchanged)
    "bot_commands.music",
    "bot_commands.blackjack",
]


class MyBot(commands.Bot):
    async def setup_hook(self):
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                logging.info(f"Loaded: {ext}")
            except Exception as e:
                logging.exception(f"Failed to load {ext}: {e}")


bot = MyBot(command_prefix=PREFIX, intents=INTENTS)


@bot.event
async def on_ready():
    print("woot is online🌳🎵")

    user = bot.user
    if user is None:
        # Super rare, but satisfies type-checkers like Pylance
        logging.info("Logged in (bot user not available yet).")
        return

    logging.info(f"Logged in as {user} (ID: {user.id})")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        return await ctx.send("You don’t have permission for that.")
    if isinstance(error, commands.BotMissingPermissions):
        return await ctx.send("I’m missing permissions to carry out this action.")
    if isinstance(error, commands.MissingRequiredArgument):
        return await ctx.send(f"Missing argument: `{error.param.name}`")
    if isinstance(error, commands.BadArgument):
        return await ctx.send("Wrong Argument, Not found.")

    logging.exception("Unhandled error:", exc_info=error)
    await ctx.send(f"❌ `{type(error).__name__}`")


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable is not set.")

bot.run(TOKEN)
