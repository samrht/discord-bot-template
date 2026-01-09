from __future__ import annotations

import os
import sys
import logging
from typing import List

from discord.ext import commands


class Restart(commands.Cog):
    """♻️ Restart / reload bot (owner only)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="restart",
        aliases=["rs"],
        help="Restart bot or reload cogs. Usage: !restart [bot|all|<cogname>]",
        usage="!restart [bot|all|<cogname>]",
    )
    @commands.is_owner()
    async def restart(self, ctx: commands.Context, target: str = "bot"):
        target = (target or "bot").lower().strip()

        # ---------- Full process restart ----------
        if target in ("bot", "main"):
            await ctx.send("♻️ Restarting the bot...")
            logging.info("Restarting bot process...")

            # Close cleanly, then replace the process
            await self.bot.close()

            os.chdir(os.path.dirname(os.path.abspath(sys.argv[0])))
            os.execv(sys.executable, [sys.executable] + sys.argv)

        # ---------- Reload all currently loaded extensions ----------
        if target == "all":
            loaded_exts: List[str] = list(getattr(self.bot, "extensions", {}).keys())
            if not loaded_exts:
                return await ctx.send("No extensions are currently loaded.")

            ok, bad = 0, 0
            await ctx.send(f"🔄 Reloading {len(loaded_exts)} extensions...")

            for ext in loaded_exts:
                try:
                    await self.bot.reload_extension(ext)
                    ok += 1
                except Exception:
                    bad += 1
                    logging.exception(f"Failed to reload {ext}")

            return await ctx.send(f"✅ Reloaded: {ok} | ❌ Failed: {bad}")

        # ---------- Reload one extension ----------
        ext = target
        if not ext.startswith("bot_commands."):
            ext = f"bot_commands.{ext}"

        try:
            await self.bot.reload_extension(ext)
            await ctx.send(f"✅ Reloaded `{ext}`")
        except commands.ExtensionNotLoaded:
            await ctx.send(f"❌ Not loaded: `{ext}`")
        except commands.ExtensionNotFound:
            await ctx.send(f"❌ Not found: `{ext}`")
        except Exception as e:
            logging.exception("Reload failed")
            await ctx.send(f"❌ Reload failed: `{type(e).__name__}: {e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Restart(bot))
