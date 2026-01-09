from __future__ import annotations

import json
import os
import logging
from typing import Dict, Any, Optional

import discord
from discord.ext import commands


class Unban(commands.Cog):
    """🔓 Unban users (also clears temp-ban timers)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = "tempbans.json"

    # ---------- persistence ----------
    def _load(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                logging.exception("Failed to load tempbans.json")
        return {}

    def _save(self, data: Dict[str, Dict[str, Dict[str, Any]]]) -> None:
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            logging.exception("Failed to save tempbans.json")

    def _clear_schedule(self, guild_id: int, user_id: int) -> bool:
        """
        Remove scheduled unban if present.
        Returns True if something was removed.
        """
        data = self._load()
        gkey = str(guild_id)
        ukey = str(user_id)

        if gkey in data and ukey in data[gkey]:
            data[gkey].pop(ukey, None)
            if not data[gkey]:
                data.pop(gkey, None)
            self._save(data)
            return True
        return False

    # ---------- command ----------
    @commands.command(
        name="unban",
        help="Unban a user. Best usage: !unban <user_id> [reason]",
        usage="!unban <user_id|name#discrim> [reason]",
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def unban(
        self, ctx: commands.Context, user: str, *, reason: Optional[str] = None
    ):
        guild = ctx.guild
        if guild is None:
            return await ctx.send("This command only works in servers (not DMs).")

        # Bot permission check
        me = guild.me
        if me is None and self.bot.user is not None:
            me = guild.get_member(self.bot.user.id)
        if me is None:
            return await ctx.send("Bot member not cached yet. Try again.")
        if not me.guild_permissions.ban_members:
            return await ctx.send("I need **Ban Members** permission to unban.")

        unban_reason = reason or "Unbanned."

        # 1) If it's an ID, unban directly
        target_id: Optional[int] = None
        if user.isdigit():
            target_id = int(user)

        if target_id is not None:
            try:
                await guild.unban(discord.Object(id=target_id), reason=unban_reason)
                cleared = self._clear_schedule(guild.id, target_id)
                msg = f"✅ Unbanned `<{target_id}>`. Reason: {unban_reason}"
                if cleared:
                    msg += " (Cleared temp-ban timer.)"
                return await ctx.send(msg)
            except discord.NotFound:
                cleared = self._clear_schedule(guild.id, target_id)
                if cleared:
                    return await ctx.send(
                        "They weren’t banned, but I cleared a leftover temp-ban timer."
                    )
                return await ctx.send("That user ID is not currently banned.")
            except discord.Forbidden:
                return await ctx.send("❌ I can’t unban here (permissions).")
            except discord.HTTPException as e:
                return await ctx.send(f"❌ Discord API error: `{e}`")

        # 2) Otherwise, search ban list for a matching name
        try:
            bans = [entry async for entry in guild.bans(limit=2000)]
        except discord.Forbidden:
            return await ctx.send("❌ I can’t fetch ban list here (permissions).")
        except discord.HTTPException as e:
            return await ctx.send(f"❌ Discord API error: `{e}`")

        user_lower = user.lower()
        match: Optional[discord.User] = None

        for entry in bans:
            banned_user = entry.user
            tag = f"{banned_user.name}#{banned_user.discriminator}".lower()
            if user_lower == tag or user_lower == banned_user.name.lower():
                match = banned_user
                break

        if match is None:
            return await ctx.send(
                "Couldn’t find that user in the ban list. Use a **user ID** for reliability."
            )

        try:
            await guild.unban(match, reason=unban_reason)
            cleared = self._clear_schedule(guild.id, match.id)
            msg = f"✅ Unbanned **{match}**. Reason: {unban_reason}"
            if cleared:
                msg += " (Cleared temp-ban timer.)"
            await ctx.send(msg)
        except discord.NotFound:
            await ctx.send("They’re not banned anymore.")
        except discord.Forbidden:
            await ctx.send("❌ I can’t unban here (permissions).")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Discord API error: `{e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Unban(bot))
