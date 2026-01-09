from __future__ import annotations

import logging
from typing import Optional, Tuple

import discord
from discord.ext import commands


class Kick(commands.Cog):
    """🥾 Kick members (with sanity checks)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_me_member(self, guild: discord.Guild) -> Optional[discord.Member]:
        # Pylance-safe way to get the bot's Member in the guild
        if self.bot.user is None:
            return None
        return guild.me or guild.get_member(self.bot.user.id)

    def _can_kick(
        self, ctx: commands.Context, member: discord.Member
    ) -> Tuple[bool, str]:
        guild = ctx.guild
        if guild is None:
            return False, "This command only works in servers (not DMs)."

        me = self._get_me_member(guild)
        if me is None:
            return False, "Bot member object not available yet. Try again."

        # bot permissions
        if not me.guild_permissions.kick_members:
            return False, "I don't have **Kick Members** permission."

        # sanity checks
        if self.bot.user is not None and member.id == self.bot.user.id:
            return False, "I’m not kicking myself. Nice try."
        if member.id == ctx.author.id:
            return False, "You can’t kick yourself. Be serious."

        # author hierarchy
        if isinstance(ctx.author, discord.Member):
            if (
                member.top_role >= ctx.author.top_role
                and ctx.author.id != guild.owner_id
            ):
                return (
                    False,
                    "You can’t kick someone with an equal/higher role than you.",
                )

        # bot hierarchy
        if member.top_role >= me.top_role:
            return False, "My role isn’t high enough to kick that user. Raise my role."

        return True, ""

    @commands.command(
        name="kick",
        help="Kick a member. Usage: !kick @user [reason]",
        usage="!kick @user [reason]",
    )
    @commands.guild_only()
    @commands.has_permissions(kick_members=True)
    async def kick(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        reason: Optional[str] = None,
    ):
        ok, msg = self._can_kick(ctx, member)
        if not ok:
            return await ctx.send(msg)

        kick_reason = reason or "No reason provided."

        # Try DM (optional, fails silently)
        try:
            await member.send(f"You were kicked from **{ctx.guild.name}**. Reason: {kick_reason}")  # type: ignore[union-attr]
        except Exception:
            pass

        try:
            await member.kick(reason=kick_reason)
            await ctx.send(f"✅ Kicked {member.mention}. Reason: {kick_reason}")
        except discord.Forbidden:
            await ctx.send("❌ I can’t kick that user (permissions/role hierarchy).")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Discord API error while kicking: `{e}`")
        except Exception as e:
            logging.exception("Unexpected kick error")
            await ctx.send(f"❌ Unexpected error: `{type(e).__name__}: {e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Kick(bot))
