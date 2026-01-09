from __future__ import annotations

from datetime import timedelta
from typing import Optional, List

import discord
from discord.ext import commands

MAX_CLEAR = 100
CONFIRM_DELETE_AFTER = 4  # seconds


def is_older_than_14_days(msg: discord.Message) -> bool:
    return (discord.utils.utcnow() - msg.created_at) >= timedelta(days=14)


class Clear(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="clear",
        help=(
            "Delete messages with filters (users, bots, keywords)"
            "Clear messages with filters.\n"
            "Usage:\n"
            "!clear <amount> [@user|bots|keyword <text>] [--dry]\n\n"
            "Examples:\n"
            "!clear 20\n"
            "!clear 50 @user\n"
            "!clear 50 bots\n"
            "!clear 100 keyword scam\n"
            "!clear 100 bots --dry"
        ),
        usage="!clear <amount> [@user|bots|keyword <text>] [--dry]",
    )
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def clear(
        self,
        ctx: commands.Context,
        amount: int,
        target: Optional[str] = None,
        *,
        rest: Optional[str] = None,
    ):
        # ---- hard guards for type checkers + reality ----
        if ctx.guild is None:
            return await ctx.send("This command only works in servers (not DMs).")

        # Only allow text channels / threads
        if not isinstance(ctx.channel, (discord.TextChannel, discord.Thread)):
            return await ctx.send("This command only works in text channels/threads.")

        guild: discord.Guild = ctx.guild
        channel: discord.abc.Messageable = ctx.channel

        # bot member (Pylance-safe)
        me = guild.me
        if me is None and self.bot.user is not None:
            me = guild.get_member(self.bot.user.id)
        if me is None:
            return await ctx.send("Bot member not cached yet. Try again.")

        if amount <= 0:
            return await ctx.send("Amount must be a positive number.")
        if amount > MAX_CLEAR:
            return await ctx.send(f"Limit is {MAX_CLEAR}.")

        # bot permission check
        if not ctx.channel.permissions_for(me).manage_messages:  # type: ignore[arg-type]
            return await ctx.send("I don't have **Manage Messages** permission here.")

        # ---- parse flags / mode ----
        dry_run = False
        keyword: Optional[str] = None
        mode: str = "normal"
        member: Optional[discord.Member] = None

        # Combine tail for flag parsing
        full_tail = ""
        if target:
            full_tail += target
        if rest:
            full_tail += " " + rest
        full_tail = full_tail.strip()

        if "--dry" in full_tail:
            dry_run = True
            full_tail = full_tail.replace("--dry", "").strip()

        # 1) Mention user (ensure Member, not User)
        if ctx.message.mentions:
            maybe = ctx.message.mentions[0]
            if isinstance(maybe, discord.Member):
                member = maybe
                mode = "user"
            else:
                # Mentioned a user not in guild cache; treat as invalid for this mode
                return await ctx.send(
                    "That user isn't a server member I can filter by. Mention a member."
                )

        # 2) bots
        elif target and target.lower() == "bots":
            mode = "bots"

        # 3) keyword <text>
        elif target and target.lower() == "keyword":
            if not rest:
                return await ctx.send("Use: `!clear <amount> keyword <text>`")
            keyword = rest.strip()
            if not keyword:
                return await ctx.send("Keyword cannot be empty.")
            mode = "keyword"

        else:
            mode = "normal"

        # ---- fetch messages ----
        fetch_limit = min(200, amount * 4)  # fetch extra to account for filtering
        messages: List[discord.Message] = []

        async for msg in ctx.channel.history(limit=fetch_limit):  # type: ignore[attr-defined]
            if msg.id == ctx.message.id:
                continue  # don't delete the command message automatically
            messages.append(msg)

        # ---- filter messages ----
        to_delete: List[discord.Message] = []
        skipped_old = 0

        for msg in messages:
            if len(to_delete) >= amount:
                break

            if is_older_than_14_days(msg):
                skipped_old += 1
                continue

            if mode == "normal":
                to_delete.append(msg)
            elif mode == "user":
                if member and msg.author.id == member.id:
                    to_delete.append(msg)
            elif mode == "bots":
                if msg.author.bot:
                    to_delete.append(msg)
            elif mode == "keyword":
                if keyword and keyword.lower() in (msg.content or "").lower():
                    to_delete.append(msg)

        # ---- dry run ----
        if dry_run:
            desc = f"Would delete **{len(to_delete)}** message(s)"
            if mode == "user" and member:
                desc += f" from **{member}**"
            elif mode == "bots":
                desc += " from **bots**"
            elif mode == "keyword" and keyword:
                desc += f' containing **"{keyword}"**'

            if skipped_old:
                desc += f"\nSkipped **{skipped_old}** old message(s) (>14 days)."

            return await ctx.send(f"🧪 Dry run: {desc}")

        if not to_delete:
            msg = "Nothing to delete."
            if skipped_old:
                msg += f" (Skipped {skipped_old} old message(s) >14 days.)"
            return await ctx.send(msg)

        # ---- delete ----
        try:
            await ctx.channel.delete_messages(to_delete)  # type: ignore[attr-defined]
        except discord.Forbidden:
            return await ctx.send("I don’t have permission to delete messages here.")
        except discord.HTTPException as e:
            return await ctx.send(f"Discord API error while deleting: `{e}`")

        # ---- confirmation ----
        summary = f"🧹 Deleted **{len(to_delete)}** message(s)"
        if mode == "user" and member:
            summary += f" from **{member}**"
        elif mode == "bots":
            summary += " from **bots**"
        elif mode == "keyword" and keyword:
            summary += f' containing **"{keyword}"**'

        if skipped_old:
            summary += f"\nSkipped **{skipped_old}** old message(s) (>14 days)."

        confirm = await ctx.send(summary)
        try:
            await confirm.delete(delay=CONFIRM_DELETE_AFTER)
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Clear(bot))
