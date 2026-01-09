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


class Ban(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = "tempbans.json"
        self.tempbans: Dict[str, Dict[str, Dict[str, Any]]] = self._load()
        self.unban_task.start()

    def cog_unload(self) -> None:
        self.unban_task.cancel()

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

    def _save(self) -> None:
        try:
            with open(self.data_file, "w", encoding="utf-8") as f:
                json.dump(self.tempbans, f, indent=2)
        except Exception:
            logging.exception("Failed to save tempbans.json")

    def _get_me_member(self, guild: discord.Guild) -> Optional[discord.Member]:
        if self.bot.user is None:
            return None
        return guild.me or guild.get_member(self.bot.user.id)

    def _can_ban(
        self, ctx: commands.Context, member: discord.Member
    ) -> Tuple[bool, str]:
        guild = ctx.guild
        if guild is None:
            return False, "This command only works inside a server (not DMs)."

        me = self._get_me_member(guild)
        if me is None:
            return False, "Bot member object not available yet. Try again in a moment."

        if not me.guild_permissions.ban_members:
            return False, "I don't have **Ban Members** permission."

        if self.bot.user is not None and member.id == self.bot.user.id:
            return False, "Nope. I’m not banning myself."
        if member.id == ctx.author.id:
            return False, "You can’t ban yourself. Be serious."

        if isinstance(ctx.author, discord.Member):
            if (
                member.top_role >= ctx.author.top_role
                and ctx.author.id != guild.owner_id
            ):
                return (
                    False,
                    "You can’t ban someone with an equal/higher role than you.",
                )

        if member.top_role >= me.top_role:
            return False, "My role isn’t high enough to ban that user. Raise my role."

        return True, ""

    @tasks.loop(seconds=30)
    async def unban_task(self) -> None:
        await self.bot.wait_until_ready()

        now = int(time.time())
        changed = False

        for guild_id, users in list(self.tempbans.items()):
            guild = self.bot.get_guild(int(guild_id))
            if guild is None:
                continue

            for user_id, entry in list(users.items()):
                unban_at = entry.get("unban_at")
                if not isinstance(unban_at, int):
                    users.pop(user_id, None)
                    changed = True
                    continue

                if now >= unban_at:
                    try:
                        await guild.unban(
                            discord.Object(id=int(user_id)), reason="Tempban expired"
                        )
                        logging.info(
                            f"Auto-unbanned user {user_id} in guild {guild_id}"
                        )
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        logging.warning(
                            f"Missing permission to unban in guild {guild_id}"
                        )
                    except Exception:
                        logging.exception("Failed auto-unban")

                    users.pop(user_id, None)
                    changed = True

            if not users:
                self.tempbans.pop(guild_id, None)
                changed = True

        if changed:
            self._save()

    @commands.command(
        name="ban",
        help="Ban permanently OR temporarily.\nUsage: !ban @user [duration] [reason]\nExample: !ban @user 2d spamming",
    )
    @commands.guild_only()
    @commands.has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        duration: Optional[str] = None,
        *,
        reason: Optional[str] = None,
    ):
        guild = ctx.guild
        if guild is None:
            # makes Pylance happy even though guild_only exists
            return await ctx.send("This command only works inside a server (not DMs).")

        ok, msg = self._can_ban(ctx, member)
        if not ok:
            return await ctx.send(msg)

        seconds: Optional[int] = None
        if duration:
            seconds = parse_duration(duration)

            if seconds is None and reason is None:
                reason = duration
                duration = None

            if duration is not None and seconds is None and reason is not None:
                return await ctx.send(
                    "Invalid duration. Use: `30s`, `10m`, `2h`, `3d`, `1w`"
                )

        ban_reason = reason or "No reason provided."

        try:
            if seconds is not None and duration is not None:
                unban_at = int(time.time()) + seconds
                gkey = str(guild.id)  # <- FIX: uses local guild var (not Optional)
                ukey = str(member.id)

                self.tempbans.setdefault(gkey, {})
                self.tempbans[gkey][ukey] = {"unban_at": unban_at, "reason": ban_reason}
                self._save()

                await member.ban(
                    reason=f"[TEMPBAN {duration}] {ban_reason}", delete_message_days=0
                )
                return await ctx.send(
                    f"✅ Tempbanned {member.mention} for **{duration}**. Reason: {ban_reason}"
                )

            await member.ban(reason=ban_reason, delete_message_days=0)
            await ctx.send(f"✅ Banned {member.mention}. Reason: {ban_reason}")

        except discord.Forbidden:
            await ctx.send("❌ I can’t ban that user (permissions/role hierarchy).")
        except discord.HTTPException as e:
            await ctx.send(f"❌ Discord API error while banning: `{e}`")
        except Exception as e:
            logging.exception("Unexpected ban error")
            await ctx.send(f"❌ Unexpected error: `{type(e).__name__}: {e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Ban(bot))
