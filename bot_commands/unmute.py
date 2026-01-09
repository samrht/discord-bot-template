from __future__ import annotations

import json
import os
import logging
from typing import Dict, Any, Optional

import discord
from discord.ext import commands


class Unmute(commands.Cog):
    """🔊 Unmute members (removes Muted role + cancels temp mute)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = "tempmutes.json"

    # ---------- persistence helpers ----------
    def _load(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                logging.exception("Failed to load tempmutes.json")
        return {}

    def _save(self, data: Dict[str, Dict[str, Dict[str, Any]]]) -> None:
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            logging.exception("Failed to save tempmutes.json")

    def _clear_schedule(self, guild_id: int, user_id: int) -> bool:
        """
        Remove a user's scheduled unmute if present.
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
        name="unmute",
        help="Unmute a member. Usage: !unmute @user [reason]",
        usage="!unmute @user [reason]",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def unmute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: Optional[str] = None,
    ):
        guild = ctx.guild
        if guild is None:
            return await ctx.send("This command only works in servers (not DMs).")

        # bot permission check
        me = guild.me
        if me is None and self.bot.user is not None:
            me = guild.get_member(self.bot.user.id)
        if me is None:
            return await ctx.send("Bot member not cached yet. Try again.")

        if not me.guild_permissions.manage_roles:
            return await ctx.send("I need **Manage Roles** permission.")

        muted_role = discord.utils.get(guild.roles, name="Muted")
        if muted_role is None:
            return await ctx.send("There is no **Muted** role in this server.")

        if muted_role not in member.roles:
            # Still clear any schedule if it exists (clean-up)
            removed = self._clear_schedule(guild.id, member.id)
            if removed:
                return await ctx.send(
                    f"{member.mention} isn’t muted anymore, but I cleared a leftover timer."
                )
            return await ctx.send(f"{member.mention} is not muted.")

        unmute_reason = reason or "Unmuted."

        try:
            await member.remove_roles(muted_role, reason=unmute_reason)
        except discord.Forbidden:
            return await ctx.send("❌ I can’t remove the Muted role (role hierarchy).")
        except Exception as e:
            logging.exception("Failed to unmute")
            return await ctx.send(f"❌ Failed to unmute: `{type(e).__name__}: {e}`")

        # cancel temp schedule if any
        removed = self._clear_schedule(guild.id, member.id)

        msg = f"✅ Unmuted {member.mention}. Reason: {unmute_reason}"
        if removed:
            msg += " (Cancelled the mute timer.)"
        await ctx.send(msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(Unmute(bot))
