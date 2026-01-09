from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from typing import Dict, Any, Optional, List, Tuple

import discord
from discord.ext import commands

logging.basicConfig(level=logging.INFO)

DEFAULT_BALANCE = 1_000_000.0


# ---------- helpers ----------
def hand_total(cards: List[int]) -> int:
    """
    Cards: 2-10, and 11 for Ace.
    Count Aces as 11, then downgrade to 1 as needed.
    """
    total = sum(cards)
    aces = cards.count(11)
    while total > 21 and aces > 0:
        total -= 10  # 11 -> 1
        aces -= 1
    return total


def format_money(x: float) -> str:
    return f"${x:,.2f}"


# ---------- Views ----------
class BetView(discord.ui.View):
    def __init__(self, ctx: commands.Context, balance: float):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.balance = balance
        self.bet_amount: Optional[float] = None
        self.custom = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "❌ Only the command author can use these buttons.", ephemeral=True
            )
            return False
        return True

    async def _set_bet(self, interaction: discord.Interaction, amount: float):
        if amount <= 0:
            await interaction.response.send_message(
                "❌ Bet must be positive.", ephemeral=True
            )
            return
        if amount > self.balance:
            await interaction.response.send_message(
                "❌ Insufficient balance.", ephemeral=True
            )
            return
        self.bet_amount = amount
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="$100", style=discord.ButtonStyle.primary)
    async def bet_100(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._set_bet(interaction, 100.0)

    @discord.ui.button(label="$500", style=discord.ButtonStyle.primary)
    async def bet_500(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._set_bet(interaction, 500.0)

    @discord.ui.button(label="$1000", style=discord.ButtonStyle.primary)
    async def bet_1000(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._set_bet(interaction, 1000.0)

    @discord.ui.button(label="All In", style=discord.ButtonStyle.danger)
    async def all_in(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._set_bet(interaction, float(self.balance))

    @discord.ui.button(label="Custom", style=discord.ButtonStyle.secondary)
    async def custom_bet(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.custom = True
        await interaction.response.defer()
        self.stop()


class ActionView(discord.ui.View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.action: Optional[str] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "❌ Only the command author can use these buttons.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.green, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = "hit"
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.red, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = "stand"
        await interaction.response.defer()
        self.stop()


class PlayAgainView(discord.ui.View):
    def __init__(self, ctx: commands.Context):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.choice: Optional[bool] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "❌ Only the command author can use these buttons.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green, emoji="✅")
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="No", style=discord.ButtonStyle.red, emoji="❌")
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.choice = False
        await interaction.response.defer()
        self.stop()


# ---------- Cog ----------
class Blackjack(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_file = "blackjack_player_data.json"
        self.player_data: Dict[str, Dict[str, Any]] = {}

        self._file_lock = asyncio.Lock()
        self._active_games: Dict[int, asyncio.Lock] = {}  # per-user lock

        # basic emoji mapping for 2-11 (Ace)
        self.card_emojis = {
            2: "🂢",
            3: "🂣",
            4: "🂤",
            5: "🂥",
            6: "🂦",
            7: "🂧",
            8: "🂨",
            9: "🂩",
            10: "🂪",
            11: "🂡",
        }

        self._load_data()

    # ---- persistence ----
    def _load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.player_data = data
                else:
                    self.player_data = {}
                logging.info("Blackjack data loaded.")
            except Exception:
                logging.exception("Failed to load blackjack data.")
                self.player_data = {}
        else:
            self.player_data = {}

    async def _save_data(self):
        # atomic write to avoid corruption
        async with self._file_lock:
            tmp = self.data_file + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.player_data, f, indent=2)
                os.replace(tmp, self.data_file)
            except Exception:
                logging.exception("Failed to save blackjack data.")
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass

    def _ensure_player(self, user_id: int):
        key = str(user_id)
        if key not in self.player_data:
            self.player_data[key] = {
                "balance": DEFAULT_BALANCE,
                "wins": 0,
                "losses": 0,
                "ties": 0,
            }

    async def _reset_if_bankrupt(self, user_id: int) -> bool:
        """
        If user is bankrupt (<=0), reset balance for next time and return True.
        """
        pdata = self.player_data[str(user_id)]
        bal = float(pdata["balance"])
        if bal <= 0:
            pdata["balance"] = DEFAULT_BALANCE
            await self._save_data()
            return True
        return False

    # ---- embeds ----
    def make_embed(
        self,
        title: str,
        desc: str,
        color: discord.Color,
        fields: Optional[List[Tuple[str, str, bool]]] = None,
    ):
        embed = discord.Embed(title=title, description=desc, color=color)
        name = self.bot.user.name if self.bot.user else "Bot"
        embed.set_footer(text=f"Blackjack | {name}")
        embed.timestamp = discord.utils.utcnow()
        if fields:
            for n, v, inline in fields:
                embed.add_field(name=n, value=v, inline=inline)
        return embed

    def cards_str(self, cards: List[int]) -> str:
        return " ".join(self.card_emojis.get(c, str(c)) for c in cards)

    # ---- game ----
    def _get_user_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._active_games:
            self._active_games[user_id] = asyncio.Lock()
        return self._active_games[user_id]

    def _build_deck(self) -> List[int]:
        deck = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11] * 4
        random.shuffle(deck)
        return deck

    async def _prompt_custom_bet(
        self, ctx: commands.Context, balance: float
    ) -> Optional[float]:
        prompt = self.make_embed(
            "💸 Custom Bet",
            f"Type a bet amount (1 to {format_money(balance)}).",
            discord.Color.blurple(),
        )
        await ctx.send(embed=prompt)

        def check(m: discord.Message) -> bool:
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=30)
            bet = float(msg.content.strip())
            if bet <= 0 or bet > balance:
                await ctx.send(
                    embed=self.make_embed(
                        "❌ Invalid Bet",
                        "Bet must be within your balance.",
                        discord.Color.red(),
                    )
                )
                return None
            return bet
        except asyncio.TimeoutError:
            await ctx.send(
                embed=self.make_embed(
                    "⏰ Timeout",
                    "You took too long. Game canceled.",
                    discord.Color.red(),
                )
            )
            return None
        except ValueError:
            await ctx.send(
                embed=self.make_embed(
                    "❌ Invalid Input",
                    "That wasn’t a number. Game canceled.",
                    discord.Color.red(),
                )
            )
            return None

    async def _play_round(self, ctx: commands.Context) -> bool:
        """
        Plays exactly one round.
        Returns True if round completed; False if canceled/timeout.
        """
        user_id = ctx.author.id
        self._ensure_player(user_id)

        pdata = self.player_data[str(user_id)]

        # Safety reset if someone saved nonsense
        if float(pdata["balance"]) <= 0:
            pdata["balance"] = DEFAULT_BALANCE
            await self._save_data()

        balance = float(pdata["balance"])

        await ctx.send(
            embed=self.make_embed(
                "🎰 Blackjack",
                f"Welcome, {ctx.author.mention}!",
                discord.Color.green(),
                fields=[("💰 Balance", format_money(balance), True)],
            )
        )

        # Bet selection
        bet_view = BetView(ctx, balance)
        bet_embed = self.make_embed(
            "💸 Place Your Bet",
            "Pick a bet with buttons.",
            discord.Color.blurple(),
            fields=[("Available", format_money(balance), True)],
        )
        await ctx.send(embed=bet_embed, view=bet_view)
        await bet_view.wait()

        bet_amount: Optional[float] = bet_view.bet_amount
        if bet_amount is None and bet_view.custom:
            bet_amount = await self._prompt_custom_bet(ctx, balance)

        if bet_amount is None:
            return False

        # Clamp bet just in case
        bet_amount = float(bet_amount)
        if bet_amount <= 0 or bet_amount > balance:
            await ctx.send(
                embed=self.make_embed(
                    "❌ Invalid Bet",
                    "That bet amount isn’t allowed.",
                    discord.Color.red(),
                )
            )
            return False

        await ctx.send(
            embed=self.make_embed(
                "✅ Bet Placed",
                f"You bet {format_money(bet_amount)}.",
                discord.Color.green(),
            )
        )

        deck = self._build_deck()
        player_cards = [deck.pop(), deck.pop()]
        dealer_cards = [deck.pop(), deck.pop()]  # one hidden initially

        player_total = hand_total(player_cards)
        dealer_total = hand_total(dealer_cards)

        hidden_dealer = f"{self.cards_str([dealer_cards[0]])} ❓"
        await ctx.send(
            embed=self.make_embed(
                "🃏 Game Start",
                "Hit or stand.",
                discord.Color.blurple(),
                fields=[
                    ("Dealer", hidden_dealer, False),
                    (
                        "You",
                        f"{self.cards_str(player_cards)} (Total: {player_total})",
                        False,
                    ),
                ],
            )
        )

        # Immediate blackjack checks
        player_blackjack = player_total == 21 and len(player_cards) == 2
        dealer_blackjack = dealer_total == 21 and len(dealer_cards) == 2

        if player_blackjack and dealer_blackjack:
            pdata["ties"] += 1
            await self._save_data()
            await ctx.send(
                embed=self.make_embed(
                    "🤝 Tie",
                    "Both got Blackjack.",
                    discord.Color.greyple(),
                    fields=[
                        (
                            "Dealer",
                            f"{self.cards_str(dealer_cards)} (Total: {dealer_total})",
                            False,
                        ),
                        (
                            "You",
                            f"{self.cards_str(player_cards)} (Total: {player_total})",
                            False,
                        ),
                    ],
                )
            )
            return True

        if player_blackjack:
            payout = 1.5 * bet_amount
            pdata["wins"] += 1
            pdata["balance"] = float(pdata["balance"]) + payout
            await self._save_data()
            await ctx.send(
                embed=self.make_embed(
                    "🏆 Blackjack!",
                    f"You win {format_money(payout)}!",
                    discord.Color.gold(),
                    fields=[
                        (
                            "You",
                            f"{self.cards_str(player_cards)} (Total: {player_total})",
                            False,
                        ),
                        ("New Balance", format_money(float(pdata["balance"])), True),
                    ],
                )
            )
            return True

        if dealer_blackjack:
            pdata["losses"] += 1
            pdata["balance"] = float(pdata["balance"]) - bet_amount
            await self._save_data()
            await ctx.send(
                embed=self.make_embed(
                    "😞 Dealer Blackjack",
                    "Dealer wins.",
                    discord.Color.red(),
                    fields=[
                        (
                            "Dealer",
                            f"{self.cards_str(dealer_cards)} (Total: {dealer_total})",
                            False,
                        ),
                        (
                            "You",
                            f"{self.cards_str(player_cards)} (Total: {player_total})",
                            False,
                        ),
                        ("New Balance", format_money(float(pdata["balance"])), True),
                    ],
                )
            )
            return True

        # Player turn
        while True:
            player_total = hand_total(player_cards)
            if player_total > 21:
                break

            action_view = ActionView(ctx)
            await ctx.send(
                embed=self.make_embed(
                    "🃏 Your Turn",
                    "Choose: Hit or Stand",
                    discord.Color.blurple(),
                    fields=[
                        ("Dealer", hidden_dealer, False),
                        (
                            "You",
                            f"{self.cards_str(player_cards)} (Total: {player_total})",
                            False,
                        ),
                    ],
                ),
                view=action_view,
            )
            await action_view.wait()

            if action_view.action is None:
                await ctx.send(
                    embed=self.make_embed(
                        "⏰ Timeout",
                        "No action chosen. Game canceled.",
                        discord.Color.red(),
                    )
                )
                return False

            if action_view.action == "stand":
                break

            player_cards.append(deck.pop())

        player_total = hand_total(player_cards)

        # Player busts
        if player_total > 21:
            pdata["losses"] += 1
            pdata["balance"] = float(pdata["balance"]) - bet_amount
            await self._save_data()

            await ctx.send(
                embed=self.make_embed(
                    "💥 Bust!",
                    f"You busted with {player_total}.",
                    discord.Color.red(),
                    fields=[
                        (
                            "You",
                            f"{self.cards_str(player_cards)} (Total: {player_total})",
                            False,
                        ),
                        ("New Balance", format_money(float(pdata["balance"])), True),
                    ],
                )
            )
            return True

        # Dealer reveal & play
        await ctx.send(
            embed=self.make_embed(
                "🃏 Dealer Reveals",
                "Dealer plays.",
                discord.Color.blurple(),
                fields=[
                    (
                        "Dealer",
                        f"{self.cards_str(dealer_cards)} (Total: {dealer_total})",
                        False,
                    )
                ],
            )
        )

        while hand_total(dealer_cards) < 17:
            dealer_cards.append(deck.pop())
            dealer_total = hand_total(dealer_cards)
            await ctx.send(
                embed=self.make_embed(
                    "🃏 Dealer Draws",
                    "Dealer draws a card.",
                    discord.Color.blurple(),
                    fields=[
                        (
                            "Dealer",
                            f"{self.cards_str(dealer_cards)} (Total: {dealer_total})",
                            False,
                        )
                    ],
                )
            )
            await asyncio.sleep(1)

        dealer_total = hand_total(dealer_cards)

        # Outcome
        if dealer_total > 21:
            pdata["wins"] += 1
            pdata["balance"] = float(pdata["balance"]) + bet_amount
            title, desc, color = "🎉 You Win!", "Dealer busted.", discord.Color.green()
        elif dealer_total == player_total:
            pdata["ties"] += 1
            title, desc, color = "🤝 Tie", "Same total.", discord.Color.greyple()
        elif dealer_total > player_total:
            pdata["losses"] += 1
            pdata["balance"] = float(pdata["balance"]) - bet_amount
            title, desc, color = (
                "😞 You Lose",
                "Dealer higher total.",
                discord.Color.red(),
            )
        else:
            pdata["wins"] += 1
            pdata["balance"] = float(pdata["balance"]) + bet_amount
            title, desc, color = "🎉 You Win!", "Higher total.", discord.Color.green()

        await self._save_data()

        await ctx.send(
            embed=self.make_embed(
                title,
                desc,
                color,
                fields=[
                    (
                        "Dealer",
                        f"{self.cards_str(dealer_cards)} (Total: {dealer_total})",
                        False,
                    ),
                    (
                        "You",
                        f"{self.cards_str(player_cards)} (Total: {player_total})",
                        False,
                    ),
                    ("Balance", format_money(float(pdata["balance"])), True),
                ],
            )
        )

        return True

    async def _stats_and_maybe_continue(self, ctx: commands.Context) -> bool:
        """
        Shows stats and asks to play again.
        Returns True if player wants another round, False otherwise.
        Also enforces your bankruptcy rule: if balance <= 0 -> end + reset.
        """
        user_id = ctx.author.id
        pdata = self.player_data[str(user_id)]
        bal = float(pdata["balance"])

        await ctx.send(
            embed=self.make_embed(
                "📊 Your Stats",
                "Current stats:",
                discord.Color.blurple(),
                fields=[
                    ("Wins", str(pdata["wins"]), True),
                    ("Losses", str(pdata["losses"]), True),
                    ("Ties", str(pdata["ties"]), True),
                    ("Balance", format_money(bal), True),
                ],
            )
        )

        # Your rule: bankrupt ends game AND resets for next time
        if bal <= 0:
            pdata["balance"] = DEFAULT_BALANCE
            await self._save_data()

            await ctx.send(
                embed=self.make_embed(
                    "💸 Game Over",
                    f"You hit {format_money(0)}.\nNext time you play, you’re back to {format_money(DEFAULT_BALANCE)}.",
                    discord.Color.red(),
                )
            )
            return False

        view = PlayAgainView(ctx)
        await ctx.send(
            embed=self.make_embed(
                "🔄 Play Again?", "Yes or No", discord.Color.blurple()
            ),
            view=view,
        )
        await view.wait()

        return bool(view.choice)

    @commands.command(name="blackjack", help="Play Blackjack with buttons.")
    async def blackjack(self, ctx: commands.Context):
        # Prevent multi-start spam per user
        lock = self._get_user_lock(ctx.author.id)
        if lock.locked():
            return await ctx.send(
                "You're already in a Blackjack game. Finish that first."
            )

        async with lock:
            self._ensure_player(ctx.author.id)

            # Extra safety reset if player is bankrupt before starting
            if await self._reset_if_bankrupt(ctx.author.id):
                await ctx.send(
                    embed=self.make_embed(
                        "💰 Balance Reset",
                        f"Your balance was {format_money(0)}. Reset to {format_money(DEFAULT_BALANCE)}.",
                        discord.Color.green(),
                    )
                )

            while True:
                completed = await self._play_round(ctx)
                if not completed:
                    # canceled/timeout => stop cleanly
                    await ctx.send(
                        embed=self.make_embed(
                            "👋 Goodbye", "Game ended.", discord.Color.blurple()
                        )
                    )
                    break

                again = await self._stats_and_maybe_continue(ctx)
                if not again:
                    await ctx.send(
                        embed=self.make_embed(
                            "👋 Goodbye",
                            "Thanks for playing Blackjack!",
                            discord.Color.blurple(),
                        )
                    )
                    break


async def setup(bot: commands.Bot):
    await bot.add_cog(Blackjack(bot))
