from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Union

import discord
from discord.ext import commands


DiscordUserLike = Union[discord.User, discord.Member]


def fmt_dt(dt: Optional[datetime]) -> str:
    if dt is None:
        return "-"
    ts = int(dt.timestamp())
    return f"<t:{ts}:F>  •  <t:{ts}:R>"


def yesno(v: bool) -> str:
    return "✅ Yes" if v else "❌ No"


def shorten(text: str, max_len: int = 250) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def key_perms_summary(perms: discord.Permissions) -> str:
    mapping = [
        ("👑 Administrator", perms.administrator),
        ("🛠️ Manage Server", perms.manage_guild),
        ("🎭 Manage Roles", perms.manage_roles),
        ("🧱 Manage Channels", perms.manage_channels),
        ("🧹 Manage Messages", perms.manage_messages),
        ("🔨 Ban Members", perms.ban_members),
        ("🥾 Kick Members", perms.kick_members),
        ("🧑‍⚖️ Moderate Members", perms.moderate_members),
        ("📣 Mention Everyone", perms.mention_everyone),
        ("🪝 Manage Webhooks", perms.manage_webhooks),
    ]
    out = [name for name, val in mapping if val]
    return ", ".join(out) if out else "-"


def all_true_perms(perms: discord.Permissions) -> str:
    granted = [name.replace("_", " ").title() for name, val in perms if val]
    return ", ".join(granted) if granted else "-"


class UserInfo(commands.Cog):
    """👤 User lookup: account + server details + perms + voice + last message"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="userinfo",
        aliases=["ui", "whois"],
        help="Show user info. Usage: !userinfo [@user|user_id]",
        usage="!userinfo [@user|user_id]",
    )
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, target: Optional[str] = None):
        guild = ctx.guild
        if guild is None:
            return await ctx.send("This command only works in servers.")

        member: Optional[discord.Member] = None
        user: Optional[DiscordUserLike] = None

        # 1) Mention
        if ctx.message.mentions:
            mentioned = ctx.message.mentions[0]
            if isinstance(mentioned, discord.Member):
                member = mentioned
                user = mentioned
            else:
                # It's a User mention (rare in guild context, but type-safe)
                user = mentioned

        # 2) ID
        elif target and target.isdigit():
            uid = int(target)
            member = guild.get_member(uid)
            if member is not None:
                user = member
            else:
                try:
                    fetched_user = await self.bot.fetch_user(uid)
                    user = fetched_user
                except discord.NotFound:
                    return await ctx.send("❌ No user found with that ID.")
                except discord.HTTPException as e:
                    return await ctx.send(f"❌ Discord API error: `{e}`")

        # 3) Default to author
        else:
            if isinstance(ctx.author, discord.Member):
                member = ctx.author
                user = ctx.author
            else:
                user = ctx.author  # type: ignore[assignment]

        if user is None:
            return await ctx.send("❌ Couldn't resolve that user.")

        # ---------- Always available ----------
        created_at = fmt_dt(user.created_at)
        avatar_url = user.display_avatar.url

        # banner (optional)
        banner_url: Optional[str] = None
        try:
            fetched = await self.bot.fetch_user(user.id)
            if fetched.banner:
                banner_url = fetched.banner.url
        except Exception:
            pass

        # badges (public flags)
        flags = getattr(user, "public_flags", None)
        badge_list: List[str] = []
        if flags:
            for name, val in flags:
                if val:
                    badge_list.append(name.replace("_", " ").title())
        badges = ", ".join(badge_list) if badge_list else "-"

        # ---------- Server-only defaults ----------
        joined_at = "-"
        nick = "-"
        top_role = "-"
        roles_str = "-"
        perms_key = "-"
        perms_all = "-"
        voice_str = "-"
        last_msg_str = "-"
        status = "-"
        activity = "-"
        boosting = "-"
        boosting_since = "-"
        timed_out_until = "-"

        # ---------- Server-only details ----------
        if member is not None:
            joined_at = fmt_dt(member.joined_at)
            nick = member.nick or "-"
            top_role = member.top_role.mention if member.top_role else "-"

            roles = [r.mention for r in member.roles if r.name != "@everyone"]
            roles_str = ", ".join(roles) if roles else "-"

            perms = member.guild_permissions
            perms_key = key_perms_summary(perms)
            perms_all = all_true_perms(perms)

            # voice
            if member.voice and member.voice.channel:
                vc = member.voice.channel
                vc_name = getattr(vc, "name", "Voice")
                voice_str = (
                    f"🔊 **{vc_name}**\n"
                    f"🎙️ Self Mute: {yesno(bool(member.voice.self_mute))}\n"
                    f"🎧 Self Deaf: {yesno(bool(member.voice.self_deaf))}\n"
                    f"🔇 Server Mute: {yesno(bool(member.voice.mute))}\n"
                    f"🙉 Server Deaf: {yesno(bool(member.voice.deaf))}"
                )
            else:
                voice_str = "-"

            # last message in THIS channel
            try:
                if isinstance(ctx.channel, (discord.TextChannel, discord.Thread)):
                    async for msg in ctx.channel.history(limit=200):
                        if msg.author.id == user.id:
                            content = msg.content or ""
                            if not content and msg.attachments:
                                content = f"[{len(msg.attachments)} attachment(s)]"
                            elif not content and msg.embeds:
                                content = f"[{len(msg.embeds)} embed(s)]"
                            if not content:
                                content = "[empty message]"

                            last_msg_str = (
                                f"📝 {shorten(content)}\n"
                                f"🕒 {fmt_dt(msg.created_at)}\n"
                                f"🔗 [Jump to message]({msg.jump_url})"
                            )
                            break
            except Exception:
                last_msg_str = "-"

            # presence
            status = str(getattr(member, "status", "unknown")).title()
            if member.activities:
                for a in member.activities:
                    if a and getattr(a, "name", None):
                        activity = a.name
                        break

            boosting = yesno(member.premium_since is not None)
            boosting_since = (
                fmt_dt(member.premium_since) if member.premium_since else "-"
            )
            timed_out_until = (
                fmt_dt(member.timed_out_until) if member.timed_out_until else "-"
            )

        # ---------- Embed ----------
        embed = discord.Embed(
            title=f"👤 User Info — {user}",
            description=f"✨ Display Name: **{user.display_name}**",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=avatar_url)

        embed.add_field(name="🆔 User ID", value=str(user.id), inline=True)
        embed.add_field(
            name="🤖 Bot?", value=yesno(bool(getattr(user, "bot", False))), inline=True
        )
        embed.add_field(name="🏅 Badges", value=badges, inline=False)

        embed.add_field(name="📅 Account Created", value=created_at, inline=False)
        embed.add_field(name="🏠 Server Joined", value=joined_at, inline=False)

        embed.add_field(name="🏷️ Nickname", value=nick, inline=True)
        embed.add_field(name="👑 Top Role", value=top_role, inline=True)

        embed.add_field(name="🎭 Roles", value=roles_str, inline=False)

        embed.add_field(name="🟢 Status", value=status, inline=True)
        embed.add_field(name="🎮 Activity", value=activity, inline=True)

        embed.add_field(name="🔐 Key Permissions", value=perms_key, inline=False)
        embed.add_field(
            name="📜 All Granted Permissions",
            value=shorten(perms_all, 900),
            inline=False,
        )

        embed.add_field(name="🔊 Voice", value=voice_str, inline=False)
        embed.add_field(
            name="🧾 Last Message (this channel)", value=last_msg_str, inline=False
        )

        embed.add_field(name="💎 Boosting?", value=boosting, inline=True)
        embed.add_field(name="💎 Boosting Since", value=boosting_since, inline=False)
        embed.add_field(name="⏳ Timed Out Until", value=timed_out_until, inline=False)

        if banner_url:
            embed.set_image(url=banner_url)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(UserInfo(bot))
