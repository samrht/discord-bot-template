from __future__ import annotations

import discord
from discord.ext import commands
from typing import Optional, List


# ---------- helpers ----------
def _shorten(s: str, n: int = 80) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _fmt_usage(prefix: str, cmd: commands.Command) -> str:
    if cmd.usage:
        return f"{prefix}{cmd.qualified_name} {cmd.usage}".strip()
    sig = cmd.signature.strip()
    return f"{prefix}{cmd.qualified_name} {sig}".strip()


def _cog_desc(cog: commands.Cog) -> str:
    d = (cog.__doc__ or "").strip()
    return d if d else "No description."


def _cmd_desc(cmd: commands.Command) -> str:
    d = (cmd.help or cmd.brief or "").strip()
    return d if d else "No description."


def _pick_emoji(name: str) -> str:
    n = name.lower()
    if "music" in n:
        return "🎵"
    if "moder" in n or "ban" in n or "kick" in n or "mute" in n:
        return "🛡️"
    if "info" in n or "user" in n or "server" in n:
        return "ℹ️"
    if "fun" in n or "game" in n or "blackjack" in n or "roll" in n:
        return "🎲"
    if "util" in n or "tools" in n or "help" in n:
        return "🧰"
    return "📦"


# ---------- UI ----------
class CogSelect(discord.ui.Select):
    def __init__(self, parent_view: "HelpView", options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Pick a category (cog)…",
            min_values=1,
            max_values=1,
            options=options[:25],
            row=0,
        )
        # DO NOT name this "parent" — discord.py already uses .parent internally
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.show_cog(interaction, self.values[0])


class CommandSelect(discord.ui.Select):
    def __init__(self, parent_view: "HelpView", options: List[discord.SelectOption]):
        super().__init__(
            placeholder="Pick a command…",
            min_values=1,
            max_values=1,
            options=options[:25],
            row=1,
        )
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.show_command(interaction, self.values[0])


class BackButton(discord.ui.Button):
    def __init__(self, parent_view: "HelpView", row: int = 2):
        super().__init__(label="Back", style=discord.ButtonStyle.secondary, emoji="⬅️", row=row)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await self.parent_view.show_home(interaction)


class HelpView(discord.ui.View):
    def __init__(self, cog: "Help", ctx: commands.Context, *, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.bot = cog.bot
        self.prefix = "!"  # keep your prefix consistent

        self.message: Optional[discord.Message] = None
        self._selected_cog: Optional[str] = None

        self._build()

    def _build(self) -> None:
        self.clear_items()
        self.add_item(CogSelect(self, self.cog._get_cog_options()))
        self.add_item(CommandSelect(self, self.cog._get_command_options(limit=25)))

    async def show_home(self, interaction: discord.Interaction):
        emb = self.cog._home_embed()
        await interaction.response.edit_message(embed=emb, view=self)

    async def show_cog(self, interaction: discord.Interaction, cog_name: str):
        self._selected_cog = cog_name
        emb = self.cog._cog_embed(cog_name)

        self.clear_items()
        self.add_item(CogSelect(self, self.cog._get_cog_options(selected=cog_name)))
        self.add_item(CommandSelect(self, self.cog._get_command_options(cog_name=cog_name)))
        self.add_item(BackButton(self, row=2))

        await interaction.response.edit_message(embed=emb, view=self)

    async def show_command(self, interaction: discord.Interaction, cmd_qualified: str):
        emb = self.cog._command_embed(cmd_qualified)

        self.clear_items()
        self.add_item(CogSelect(self, self.cog._get_cog_options(selected=self._selected_cog)))

        if self._selected_cog:
            self.add_item(CommandSelect(self, self.cog._get_command_options(cog_name=self._selected_cog)))
        else:
            self.add_item(CommandSelect(self, self.cog._get_command_options(limit=25)))

        self.add_item(BackButton(self, row=2))
        await interaction.response.edit_message(embed=emb, view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


# ---------- Cog ----------
class Help(commands.Cog):
    """📖 Help & command reference"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help")
    async def help(
        self,
        ctx: commands.Context,
        cog_or_command: Optional[str] = None,
        subcommand: Optional[str] = None,  # kept for compatibility
    ):
        """
        !help
        !help <cog>
        !help <command>
        !help <command> <subcommand>
        """

        if cog_or_command is None:
            view = HelpView(self, ctx)
            emb = self._home_embed()
            msg = await ctx.send(embed=emb, view=view)
            view.message = msg
            return

        # fallback: command lookup
        command = self.bot.get_command(cog_or_command)
        if command:
            return await ctx.send(embed=self._command_embed(command.qualified_name))

        # fallback: cog lookup
        cog = self.bot.get_cog(cog_or_command.capitalize())
        if cog:
            return await ctx.send(embed=self._cog_embed(cog.qualified_name))

        await ctx.send(f"❌ No command or cog named `{cog_or_command}` found.")

    # -------- data builders --------
    def _iter_cogs_with_commands(self) -> List[commands.Cog]:
        out: List[commands.Cog] = []
        for c in self.bot.cogs.values():
            try:
                if c.get_commands():
                    out.append(c)
            except Exception:
                continue
        return sorted(out, key=lambda x: x.qualified_name.lower())

    def _get_cog_options(self, selected: Optional[str] = None) -> List[discord.SelectOption]:
        opts: List[discord.SelectOption] = []
        for c in self._iter_cogs_with_commands():
            opts.append(
                discord.SelectOption(
                    label=c.qualified_name,
                    value=c.qualified_name,
                    description=_shorten(_cog_desc(c), 80),
                    emoji=_pick_emoji(c.qualified_name),
                    default=(selected is not None and selected.lower() == c.qualified_name.lower()),
                )
            )
        if not opts:
            opts.append(discord.SelectOption(label="No cogs found", value="none", default=True))
        return opts

    def _get_command_options(self, cog_name: Optional[str] = None, limit: int = 25) -> List[discord.SelectOption]:
        cmds: List[commands.Command] = []

        if cog_name:
            cog = self.bot.get_cog(cog_name)
            if cog:
                cmds = list(cog.get_commands())
        else:
            for c in self._iter_cogs_with_commands():
                cmds.extend(list(c.get_commands()))

        cmds = [c for c in cmds if not c.hidden and c.enabled]
        cmds.sort(key=lambda x: x.qualified_name.lower())
        cmds = cmds[:limit]

        opts: List[discord.SelectOption] = []
        for cmd in cmds:
            opts.append(
                discord.SelectOption(
                    label=f"!{cmd.qualified_name}",
                    value=cmd.qualified_name,
                    description=_shorten(_cmd_desc(cmd), 80),
                    emoji="🔧",
                )
            )

        if not opts:
            opts.append(discord.SelectOption(label="No commands found", value="none", default=True))
        return opts

    # -------- embeds --------
    def _home_embed(self) -> discord.Embed:
        emb = discord.Embed(
            title="📖 Help Panel",
            description="Pick a category or a command using the menus below.\n\n"
                        "Old-school: `!help <cog>` or `!help <command>`.",
            color=discord.Color.blurple(),
        )

        cogs = self._iter_cogs_with_commands()
        if not cogs:
            emb.add_field(name="Nothing loaded", value="No loaded cogs with commands.", inline=False)
            return emb

        lines = []
        for c in cogs:
            lines.append(f"{_pick_emoji(c.qualified_name)} **{c.qualified_name}** — {_shorten(_cog_desc(c), 60)}")

        emb.add_field(name="Categories", value="\n".join(lines[:12]), inline=False)
        if len(lines) > 12:
            emb.set_footer(text=f"+ {len(lines) - 12} more (use the dropdown).")
        else:
            emb.set_footer(text="Use the dropdowns. Stop free-typing chaos.")
        return emb

    def _cog_embed(self, cog_name: str) -> discord.Embed:
        cog = self.bot.get_cog(cog_name)
        if not cog:
            return discord.Embed(title="❌ Not found", description=f"No cog named `{cog_name}`.", color=discord.Color.red())

        emb = discord.Embed(
            title=f"{_pick_emoji(cog.qualified_name)} {cog.qualified_name}",
            description=_cog_desc(cog),
            color=discord.Color.green(),
        )

        cmds = [c for c in cog.get_commands() if not c.hidden and c.enabled]
        cmds.sort(key=lambda x: x.qualified_name.lower())

        if not cmds:
            emb.add_field(name="Commands", value="(none)", inline=False)
            return emb

        chunks: List[str] = []
        for cmd in cmds[:12]:
            usage = _fmt_usage("!", cmd)
            chunks.append(f"**!{cmd.name}**\n`{usage}`\n{_shorten(_cmd_desc(cmd), 80)}")

        emb.add_field(name="Commands", value="\n\n".join(chunks), inline=False)
        if len(cmds) > 12:
            emb.set_footer(text=f"+ {len(cmds) - 12} more (use the command dropdown).")
        else:
            emb.set_footer(text="Use the command dropdown for details.")
        return emb

    def _command_embed(self, cmd_qualified: str) -> discord.Embed:
        cmd = self.bot.get_command(cmd_qualified)
        if not cmd:
            return discord.Embed(title="❌ Not found", description=f"No command named `{cmd_qualified}`.", color=discord.Color.red())

        usage = _fmt_usage("!", cmd)

        emb = discord.Embed(
            title=f"🔧 !{cmd.qualified_name}",
            description=_cmd_desc(cmd),
            color=discord.Color.orange(),
        )
        emb.add_field(name="Usage", value=f"`{usage}`", inline=False)

        aliases = getattr(cmd, "aliases", [])
        if aliases:
            emb.add_field(name="Aliases", value=", ".join(f"`!{a}`" for a in aliases), inline=False)

        parent = cmd.cog.qualified_name if cmd.cog else "No cog"
        emb.add_field(name="Category", value=f"`{parent}`", inline=True)
        emb.set_footer(text="Clean help UI. Your old one deserved jail time.")
        return emb


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
