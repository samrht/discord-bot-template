from __future__ import annotations

import json
import os
import time
import logging
from typing import Optional, Dict, Any, Tuple

import discord
from discord.ext import commands, tasks

DURATION_UNITS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}


def parse_duration(raw: str) -> Optional[int]:
    """
    Parse duration like: 30s, 10m, 2h, 3d, 1w
    Returns seconds or None if invalid.
    """
    raw = raw.strip().lower()
    if not raw:
        return None

    num = ""
    unit = ""
    for ch in raw:
        if ch.isdigit():
            num += ch
        else:
            unit += ch

    if not num or not unit or unit not in DURATION_UNITS:
        return None

    try:
        value = int(num)
        if value <= 0:
            return None
    except ValueError:
        return None

    return value * DURATION_UNITS[unit]


class Mute(commands.Cog):
    """🔇 Mute members with a Muted role (temp/permanent)"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = "tempmutes.json"
        self.tempmutes: Dict[str, Dict[str, Dict[str, Any]]] = self._load()
        self.unmute_task.start()

    def cog_unload(self) -> None:
        self.unmute_task.cancel()

    # ---------- persistence ----------
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

    def _save(self) -> None:
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.tempmutes, f, indent=2)
        except Exception:
            logging.exception("Failed to save tempmutes.json")

    # ---------- utils ----------
    def _get_me_member(self, guild: discord.Guild) -> Optional[discord.Member]:
        if self.bot.user is None:
            return None
        return guild.me or guild.get_member(self.bot.user.id)

    async def _ensure_muted_role(self, guild: discord.Guild) -> discord.Role:
        # Find existing
        role = discord.utils.get(guild.roles, name="Muted")
        if role:
            return role

        # Create role
        role = await guild.create_role(
            name="Muted",
            reason="Created Muted role for mute system",
            permissions=discord.Permissions.none(),
        )

        # Apply overwrites to all channels
        for channel in guild.channels:
            try:
                overwrite = channel.overwrites_for(role)
                overwrite.send_messages = False
                overwrite.add_reactions = False
                overwrite.speak = False
                overwrite.connect = False
                overwrite.send_messages_in_threads = False
                await channel.set_permissions(role, overwrite=overwrite)
            except Exception:
                # Don't die because one channel is weird
                continue

        return role

    def _can_mute(
        self, ctx: commands.Context, member: discord.Member
    ) -> Tuple[bool, str]:
        guild = ctx.guild
        if guild is None:
            return False, "This command only works in servers (not DMs)."

        me = self._get_me_member(guild)
        if me is None:
            return False, "Bot member not cached yet. Try again."

        if not me.guild_permissions.manage_roles:
            return False, "I need **Manage Roles** permission."

        if self.bot.user is not None and member.id == self.bot.user.id:
            return False, "I can't mute myself"
        if member.id == ctx.author.id:
            return False, "You can’t mute yourself. Stop."

        # Author hierarchy
        if isinstance(ctx.author, discord.Member):
            if (
                member.top_role >= ctx.author.top_role
                and ctx.author.id != guild.owner_id
            ):
                return (
                    False,
                    "You can’t mute someone with an equal/higher role than you.",
                )

        # Bot hierarchy
        if member.top_role >= me.top_role:
            return False, "My role isn’t high enough to mute that user."

        return True, ""

    async def _schedule_unmute(
        self, guild_id: int, user_id: int, unmute_at: int, reason: str
    ):
        gkey = str(guild_id)
        ukey = str(user_id)
        self.tempmutes.setdefault(gkey, {})
        self.tempmutes[gkey][ukey] = {"unmute_at": unmute_at, "reason": reason}
        self._save()

    async def _clear_schedule(self, guild_id: int, user_id: int):
        gkey = str(guild_id)
        ukey = str(user_id)
        if gkey in self.tempmutes and ukey in self.tempmutes[gkey]:
            self.tempmutes[gkey].pop(ukey, None)
            if not self.tempmutes[gkey]:
                self.tempmutes.pop(gkey, None)
            self._save()

    # ---------- background unmute ----------
    @tasks.loop(seconds=20)
    async def unmute_task(self):
        await self.bot.wait_until_ready()

        now = int(time.time())
        changed = False

        for guild_id, users in list(self.tempmutes.items()):
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue

            muted_role = discord.utils.get(guild.roles, name="Muted")
            if muted_role is None:
                # role removed manually -> drop schedules
                self.tempmutes.pop(guild_id, None)
                changed = True
                continue

            for user_id, entry in list(users.items()):
                unmute_at = entry.get("unmute_at")
                if not isinstance(unmute_at, int):
                    users.pop(user_id, None)
                    changed = True
                    continue

                if now >= unmute_at:
                    member = guild.get_member(int(user_id))
                    # if they left, just clear schedule
                    if member is not None:
                        try:
                            await member.remove_roles(muted_role, reason="Mute expired")
                        except Exception:
                            logging.exception("Failed to auto-unmute")

                    users.pop(user_id, None)
                    changed = True

            if not users:
                self.tempmutes.pop(guild_id, None)
                changed = True

        if changed:
            self._save()

    # ---------- command ----------
    @commands.command(
        name="mute",
        help="Mute permanently or temporarily. Usage: !mute @user [duration] [reason]",
        usage="!mute @user [duration] [reason]",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def mute(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: Optional[str] = None,
        *,
        reason: Optional[str] = None,
    ):
        guild = ctx.guild
        if guild is None:
            return await ctx.send("This command only works in servers (not DMs).")

        ok, msg = self._can_mute(ctx, member)
        if not ok:
            return await ctx.send(msg)

        # interpret duration vs reason
        seconds: Optional[int] = None
        if duration:
            seconds = parse_duration(duration)

            # If duration invalid and no reason, treat duration as reason (perma mute)
            if seconds is None and reason is None:
                reason = duration
                duration = None

            # If duration invalid but reason exists -> reject
            if duration is not None and seconds is None and reason is not None:
                return await ctx.send(
                    "Invalid duration. Use: `30s`, `10m`, `2h`, `3d`, `1w`"
                )

        mute_reason = reason or "No reason provided."

        # Get/create muted role
        try:
            muted_role = await self._ensure_muted_role(guild)
        except discord.Forbidden:
            return await ctx.send(
                "❌ I can’t create/manage the Muted role. Check my permissions/role position."
            )
        except Exception as e:
            logging.exception("Failed creating Muted role")
            return await ctx.send(
                f"❌ Failed to set up Muted role: `{type(e).__name__}: {e}`"
            )

        # Already muted?
        if muted_role in member.roles:
            return await ctx.send(f"{member.mention} is already muted.")

        # Apply role
        try:
            await member.add_roles(muted_role, reason=mute_reason)
        except discord.Forbidden:
            return await ctx.send("❌ I can’t assign the Muted role (role hierarchy).")
        except Exception as e:
            logging.exception("Failed to mute")
            return await ctx.send(f"❌ Failed to mute: `{type(e).__name__}: {e}`")

        # If timed, schedule unmute
        if seconds is not None and duration is not None:
            unmute_at = int(time.time()) + seconds
            await self._schedule_unmute(guild.id, member.id, unmute_at, mute_reason)
            return await ctx.send(
                f"✅ Muted {member.mention} for **{duration}**. Reason: {mute_reason}"
            )

        # Permanent mute: clear any existing schedule just in case
        await self._clear_schedule(guild.id, member.id)
        await ctx.send(f"✅ Muted {member.mention} permanently. Reason: {mute_reason}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Mute(bot))
