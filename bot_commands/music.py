from __future__ import annotations

import os
import re
import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, cast, Any

import discord
from discord.ext import commands
import yt_dlp

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


# ===================== REGEX =====================
SPOTIFY_TRACK_RE = re.compile(
    r"(?:open\.spotify\.com/track/|spotify:track:)([A-Za-z0-9]+)"
)
SPOTIFY_PLAYLIST_RE = re.compile(
    r"(?:open\.spotify\.com/playlist/|spotify:playlist:)([A-Za-z0-9]+)"
)
SPOTIFY_ALBUM_RE = re.compile(
    r"(?:open\.spotify\.com/album/|spotify:album:)([A-Za-z0-9]+)"
)

YOUTUBE_URL_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE
)


# ===================== HELPERS =====================
def fmt_duration(seconds: int) -> str:
    if not seconds or seconds < 0:
        return "—"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def clamp(n: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, n))


def progress_bar(elapsed: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "—" * width
    frac = clamp(elapsed / total, 0.0, 1.0)
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


# ===================== DATA =====================
@dataclass
class Track:
    title: str
    query: str
    duration: int = 0
    webpage_url: Optional[str] = None
    thumbnail: Optional[str] = None
    requester_id: Optional[int] = None


class GuildState:
    def __init__(self) -> None:
        self.queue: List[Track] = []
        self.current: Optional[Track] = None
        self.lock = asyncio.Lock()

        # Panel message (1 per guild)
        self.panel_channel_id: Optional[int] = None
        self.panel_message_id: Optional[int] = None

        # loop: "off" | "one" | "all"
        self.loop_mode: str = "off"

        # audio volume multiplier
        self.volume: float = 1.0

        # when user skips, we don't requeue current even in loop modes
        self.skip_requested: bool = False

        # progress tracking
        self.started_at: Optional[float] = None  # monotonic timestamp when started
        self.paused_at: Optional[float] = None  # monotonic timestamp when paused
        self.paused_total: float = 0.0  # total paused duration accumulated


# ===================== UI: MODALS =====================
class VolumeModal(discord.ui.Modal, title="Set Volume"):
    vol_input = discord.ui.TextInput(
        label="Volume (%)",
        placeholder="Example: 80  or  150  or  7",
        required=True,
        max_length=6,
    )

    def __init__(self, cog: "Music", guild_id: int):
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "Server only.", ephemeral=True
            )

        raw = str(self.vol_input.value).strip().replace("%", "")
        try:
            pct = float(raw)
        except ValueError:
            return await interaction.response.send_message(
                "❌ Enter a number like 80 or 150.", ephemeral=True
            )

        # allow 1% to 300% (you can widen this if you enjoy suffering)
        pct = clamp(pct, 1.0, 300.0)
        st = self.cog._state(self.guild_id)
        st.volume = pct / 100.0

        # if currently playing, restarting the stream to apply volume is the only reliable way
        # (discord.py doesn't support live gain changes without re-wrapping audio)
        await interaction.response.send_message(
            f"🔊 Volume set to **{int(pct)}%**", ephemeral=True
        )
        await self.cog._refresh_panel(guild)


# ===================== UI: SELECTS =====================
class JumpSelect(discord.ui.Select):
    def __init__(
        self, cog: "Music", guild_id: int, options: List[discord.SelectOption]
    ):
        self.cog = cog
        self.guild_id = guild_id
        super().__init__(
            placeholder="⏭️ Jump to track…",
            min_values=1,
            max_values=1,
            options=options[:25],
            row=2,  # select = width 5, own row
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "Server only.", ephemeral=True
            )

        st = self.cog._state(self.guild_id)
        try:
            idx = int(self.values[0])
        except Exception:
            return await interaction.response.send_message(
                "❌ Invalid selection.", ephemeral=True
            )

        async with st.lock:
            if idx < 0 or idx >= len(st.queue):
                return await interaction.response.send_message(
                    "❌ That track isn't in queue anymore.", ephemeral=True
                )

            # move selected to front (next)
            chosen = st.queue.pop(idx)
            st.queue.insert(0, chosen)

        vc = guild.voice_client
        if vc and (
            cast(discord.VoiceClient, vc).is_playing()
            or cast(discord.VoiceClient, vc).is_paused()
        ):
            st.skip_requested = True
            cast(discord.VoiceClient, vc).stop()  # triggers next
        else:
            await self.cog._play_next(guild)

        await interaction.response.send_message("⏭️ Jumped.", ephemeral=True)
        await self.cog._refresh_panel(guild)


# ===================== UI: VIEW =====================
class MusicControlsView(discord.ui.View):
    def __init__(self, cog: "Music", guild_id: int, *, timeout: float = 900):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.guild_id = guild_id

        # Jump select options get injected dynamically in _refresh_panel/_ensure_panel
        # so the view is created with those options outside, not here.

    async def _gate(
        self, interaction: discord.Interaction
    ) -> tuple[Optional[discord.Guild], Optional[discord.VoiceClient], Optional[str]]:
        guild = interaction.guild
        if guild is None:
            return None, None, "This only works in a server."

        vc_proto = guild.voice_client
        if vc_proto is None:
            return guild, None, "I'm not in voice."

        vc = cast(discord.VoiceClient, vc_proto)

        user = interaction.user
        member = user if isinstance(user, discord.Member) else None
        if member is None or member.voice is None or member.voice.channel is None:
            return guild, vc, "Join a voice channel first."

        if vc.channel is not None and member.voice.channel.id != vc.channel.id:
            return guild, vc, "Be in *my* voice channel to control me."

        return guild, vc, None

    async def _ok(self, interaction: discord.Interaction, text: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    # ===== ROW 0 (max 5 items) =====
    @discord.ui.button(
        label="Pause", style=discord.ButtonStyle.secondary, emoji="⏸️", row=0
    )
    async def pause_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild, vc, err = await self._gate(interaction)
        if err or vc is None or guild is None:
            return await self._ok(interaction, f"❌ {err}")

        st = self.cog._state(guild.id)

        if vc.is_playing():
            vc.pause()
            if st.paused_at is None:
                st.paused_at = time.monotonic()
            await self._ok(interaction, "⏸️ Paused.")
        else:
            await self._ok(interaction, "Nothing is playing.")

        await self.cog._refresh_panel(guild)

    @discord.ui.button(
        label="Resume", style=discord.ButtonStyle.success, emoji="▶️", row=0
    )
    async def resume_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild, vc, err = await self._gate(interaction)
        if err or vc is None or guild is None:
            return await self._ok(interaction, f"❌ {err}")

        st = self.cog._state(guild.id)

        if vc.is_paused():
            vc.resume()
            if st.paused_at is not None:
                st.paused_total += time.monotonic() - st.paused_at
                st.paused_at = None
            await self._ok(interaction, "▶️ Resumed.")
        else:
            await self._ok(interaction, "Nothing is paused.")

        await self.cog._refresh_panel(guild)

    @discord.ui.button(
        label="Skip", style=discord.ButtonStyle.primary, emoji="⏭️", row=0
    )
    async def skip_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild, vc, err = await self._gate(interaction)
        if err or vc is None or guild is None:
            return await self._ok(interaction, f"❌ {err}")

        st = self.cog._state(guild.id)
        st.skip_requested = True

        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await self._ok(interaction, "⏭️ Skipped.")
        else:
            await self._ok(interaction, "Nothing to skip.")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild, vc, err = await self._gate(interaction)
        if err or vc is None or guild is None:
            return await self._ok(interaction, f"❌ {err}")

        st = self.cog._state(guild.id)
        async with st.lock:
            st.queue.clear()
            st.current = None
            st.skip_requested = False
            st.started_at = None
            st.paused_at = None
            st.paused_total = 0.0

        if vc.is_playing() or vc.is_paused():
            vc.stop()

        await self._ok(interaction, "⏹️ Stopped.")
        await self.cog._refresh_panel(guild)

    @discord.ui.button(
        label="Leave", style=discord.ButtonStyle.danger, emoji="🚪", row=0
    )
    async def leave_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild, vc, err = await self._gate(interaction)
        if err or vc is None or guild is None:
            return await self._ok(interaction, f"❌ {err}")

        st = self.cog._state(guild.id)
        async with st.lock:
            st.queue.clear()
            st.current = None
            st.skip_requested = False
            st.started_at = None
            st.paused_at = None
            st.paused_total = 0.0

        await vc.disconnect(force=True)
        await self._ok(interaction, "🚪 Left voice.")
        await self.cog._refresh_panel(guild)

    # ===== ROW 1 (max 5) =====
    @discord.ui.button(
        label="Loop", style=discord.ButtonStyle.secondary, emoji="🔁", row=1
    )
    async def loop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        if guild is None:
            return await self._ok(interaction, "Server only.")
        st = self.cog._state(guild.id)

        st.loop_mode = {"off": "one", "one": "all", "all": "off"}[st.loop_mode]
        label = {"off": "Off", "one": "One", "all": "All"}[st.loop_mode]
        await self._ok(interaction, f"🔁 Loop: **{label}**")
        await self.cog._refresh_panel(guild)

    @discord.ui.button(
        label="Shuffle", style=discord.ButtonStyle.secondary, emoji="🔀", row=1
    )
    async def shuffle_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        if guild is None:
            return await self._ok(interaction, "Server only.")
        st = self.cog._state(guild.id)

        async with st.lock:
            if len(st.queue) < 2:
                return await self._ok(interaction, "Queue too small.")
            import random

            random.shuffle(st.queue)

        await self._ok(interaction, "🔀 Shuffled.")
        await self.cog._refresh_panel(guild)

    @discord.ui.button(
        label="Queue", style=discord.ButtonStyle.secondary, emoji="📜", row=1
    )
    async def queue_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        if guild is None:
            return await self._ok(interaction, "Server only.")
        st = self.cog._state(guild.id)
        emb = self.cog._queue_embed(guild, st)

        if interaction.response.is_done():
            await interaction.followup.send(embed=emb, ephemeral=True)
        else:
            await interaction.response.send_message(embed=emb, ephemeral=True)

    @discord.ui.button(
        label="Volume", style=discord.ButtonStyle.secondary, emoji="🔊", row=1
    )
    async def volume_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        guild = interaction.guild
        if guild is None:
            return await self._ok(interaction, "Server only.")
        await interaction.response.send_modal(VolumeModal(self.cog, self.guild_id))

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True


# ===================== COG =====================
class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: Dict[int, GuildState] = {}

        ff = shutil.which("ffmpeg")
        if not ff:
            raise RuntimeError(
                "FFmpeg not found on PATH. Install FFmpeg and add it to PATH."
            )
        self.ffmpeg_path: str = ff

        self.sp = self._init_spotify()

        self.ydl_opts: Dict[str, object] = {
            "format": "bestaudio/best",
            "quiet": True,
            "noplaylist": True,
            "default_search": "ytsearch",
        }

        # panel auto-refresh loop
        self._panel_task = self.bot.loop.create_task(self._panel_refresher())

    def cog_unload(self) -> None:
        if self._panel_task:
            self._panel_task.cancel()

    async def _panel_refresher(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                for gid in list(self.states.keys()):
                    guild = self.bot.get_guild(gid)
                    if guild is None:
                        continue
                    await self._refresh_panel(guild)
            except asyncio.CancelledError:
                return
            except Exception:
                logging.exception("Panel refresher error")

    def _state(self, guild_id: int) -> GuildState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildState()
        return self.states[guild_id]

    def _init_spotify(self) -> Optional[spotipy.Spotify]:
        cid = os.getenv("SPOTIPY_CLIENT_ID")
        secret = os.getenv("SPOTIPY_CLIENT_SECRET")
        if not cid or not secret:
            logging.warning(
                "Spotify creds missing: set SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET."
            )
            return None
        try:
            auth = SpotifyClientCredentials(client_id=cid, client_secret=secret)
            return spotipy.Spotify(auth_manager=auth)
        except Exception:
            logging.exception("Failed to init Spotify client.")
            return None

    def _spotify_kind_and_id(self, s: str) -> Tuple[Optional[str], Optional[str]]:
        m = SPOTIFY_TRACK_RE.search(s)
        if m:
            return "track", m.group(1)
        m = SPOTIFY_ALBUM_RE.search(s)
        if m:
            return "album", m.group(1)
        m = SPOTIFY_PLAYLIST_RE.search(s)
        if m:
            return "playlist", m.group(1)
        return None, None

    def _is_youtube_url(self, s: str) -> bool:
        return bool(YOUTUBE_URL_RE.search(s))

    def _voice_client(self, ctx: commands.Context) -> Optional[discord.VoiceClient]:
        vc = ctx.voice_client
        if vc is None:
            return None
        return cast(discord.VoiceClient, vc)

    @staticmethod
    def _safe_artists(obj: Any) -> str:
        if not isinstance(obj, list):
            return ""
        names: List[str] = []
        for a in obj:
            if isinstance(a, dict):
                n = a.get("name")
                if isinstance(n, str) and n.strip():
                    names.append(n.strip())
        return ", ".join(names).strip()

    async def _ensure_voice(
        self, ctx: commands.Context
    ) -> Optional[discord.VoiceClient]:
        if ctx.guild is None:
            return None

        voice_state = getattr(ctx.author, "voice", None)
        if not voice_state or not voice_state.channel:
            return None  # no spam

        vc = self._voice_client(ctx)
        if vc and vc.is_connected():
            if vc.channel and getattr(vc.channel, "id", None) != voice_state.channel.id:
                try:
                    await vc.move_to(voice_state.channel)
                except discord.Forbidden:
                    return None
            return vc

        try:
            connected = await voice_state.channel.connect()
            return cast(discord.VoiceClient, connected)
        except Exception:
            return None

    async def _yt_resolve(
        self, query_or_url: str
    ) -> Tuple[str, str, int, Optional[str], Optional[str]]:
        def work() -> Tuple[str, str, int, Optional[str], Optional[str]]:
            ydl_opts_any = dict(self.ydl_opts)

            with yt_dlp.YoutubeDL(ydl_opts_any) as ydl:
                info = ydl.extract_info(query_or_url, download=False)

                if isinstance(info, dict) and info.get("entries"):
                    entries = info.get("entries")
                    if isinstance(entries, list) and entries:
                        info = entries[0]

                if not isinstance(info, dict):
                    raise RuntimeError("yt-dlp returned unexpected result")

                stream_url = info.get("url")
                title = str(info.get("title") or "Unknown Title")
                duration = int(info.get("duration") or 0)

                webpage = info.get("webpage_url") or info.get("original_url")
                webpage_url = webpage if isinstance(webpage, str) else None

                thumb = info.get("thumbnail")
                thumbnail = thumb if isinstance(thumb, str) else None

                if not stream_url or not isinstance(stream_url, str):
                    raise RuntimeError("No stream URL found")

                return stream_url, title, duration, webpage_url, thumbnail

        return await asyncio.to_thread(work)

    async def _spotify_tracks(self, url: str, requester_id: int) -> List[Track]:
        if self.sp is None:
            raise RuntimeError(
                "Spotify not configured (set SPOTIPY_CLIENT_ID / SPOTIPY_CLIENT_SECRET)."
            )

        kind, sid = self._spotify_kind_and_id(url)
        if not kind or not sid:
            raise RuntimeError("Invalid Spotify URL.")

        tracks: List[Track] = []

        if kind == "track":
            t = await asyncio.to_thread(self.sp.track, sid)
            if not isinstance(t, dict):
                raise RuntimeError("Spotify track response was not a dict")

            name = t.get("name")
            name_str = name if isinstance(name, str) and name.strip() else "Unknown"
            artists_str = self._safe_artists(t.get("artists"))

            dur_ms = t.get("duration_ms")
            dur = int(dur_ms // 1000) if isinstance(dur_ms, int) else 0

            q = f"{name_str} {artists_str}".strip()
            tracks.append(
                Track(
                    title=f"{name_str} — {artists_str}".strip(" —"),
                    query=q,
                    duration=dur,
                    requester_id=requester_id,
                )
            )
            return tracks

        if kind == "album":
            album = await asyncio.to_thread(self.sp.album, sid)
            if not isinstance(album, dict):
                raise RuntimeError("Spotify album response was not a dict")

            album_name = album.get("name")
            album_name_str = (
                album_name
                if isinstance(album_name, str) and album_name.strip()
                else "Album"
            )

            page = await asyncio.to_thread(self.sp.album_tracks, sid, limit=50)
            items_obj = page.get("items") if isinstance(page, dict) else []
            items = items_obj if isinstance(items_obj, list) else []

            for it in items:
                if not isinstance(it, dict):
                    continue
                nm = it.get("name")
                name_str = nm if isinstance(nm, str) and nm.strip() else ""
                artists_str = self._safe_artists(it.get("artists"))
                if not name_str or not artists_str:
                    continue
                q = f"{name_str} {artists_str}".strip()
                tracks.append(
                    Track(
                        title=f"{name_str} — {artists_str} ({album_name_str})",
                        query=q,
                        requester_id=requester_id,
                    )
                )
            return tracks

        # playlist
        offset = 0
        while True:
            page = await asyncio.to_thread(
                self.sp.playlist_items, sid, limit=100, offset=offset
            )
            if not isinstance(page, dict):
                break

            items_obj = page.get("items")
            items = items_obj if isinstance(items_obj, list) else []

            for row in items:
                if not isinstance(row, dict):
                    continue
                tr = row.get("track")
                if not isinstance(tr, dict):
                    continue
                nm = tr.get("name")
                name_str = nm if isinstance(nm, str) and nm.strip() else ""
                artists_str = self._safe_artists(tr.get("artists"))
                if not name_str or not artists_str:
                    continue
                q = f"{name_str} {artists_str}".strip()
                tracks.append(
                    Track(
                        title=f"{name_str} — {artists_str}",
                        query=q,
                        requester_id=requester_id,
                    )
                )

            if page.get("next") is None:
                break
            offset += 100

        return tracks

    # ===================== PANEL BUILD =====================
    def _compute_elapsed(self, st: GuildState) -> int:
        if st.started_at is None:
            return 0
        base = time.monotonic() - st.started_at - st.paused_total
        if st.paused_at is not None:
            base -= time.monotonic() - st.paused_at
        return max(0, int(base))

    def _panel_embed(self, guild: discord.Guild, st: GuildState) -> discord.Embed:
        vc = guild.voice_client
        channel_name = "—"
        playing_state = "⏹️ Idle"

        if vc is not None:
            vcc = cast(discord.VoiceClient, vc)
            if vcc.is_connected() and vcc.channel is not None:
                channel_name = vcc.channel.name
                if vcc.is_paused():
                    playing_state = "⏸️ Paused"
                elif vcc.is_playing():
                    playing_state = "▶️ Playing"
                else:
                    playing_state = "⏹️ Idle"

        loop_label = {"off": "Off", "one": "One", "all": "All"}[st.loop_mode]
        vol_label = f"{int(st.volume * 100)}%"

        now_title = "—"
        now_url = None
        dur = 0
        requester = "—"
        thumb = None

        if st.current:
            now_title = st.current.title
            now_url = st.current.webpage_url
            dur = st.current.duration
            thumb = st.current.thumbnail
            if st.current.requester_id:
                m = guild.get_member(st.current.requester_id)
                requester = m.mention if m else f"`{st.current.requester_id}`"

        elapsed = self._compute_elapsed(st)
        bar = progress_bar(elapsed, dur, width=20)
        time_line = f"`{fmt_duration(elapsed)}` / `{fmt_duration(dur)}`"

        desc = f"**Voice:** `{channel_name}`\n**Status:** {playing_state}\n**Loop:** `{loop_label}`  •  **Volume:** `{vol_label}`\n\n"
        desc += f"{bar}\n{time_line}"

        emb = discord.Embed(
            title="🌳🎵 woot Music", description=desc, color=discord.Color.blurple()
        )

        if now_url:
            emb.add_field(
                name="Now Playing", value=f"🎶 [{now_title}]({now_url})", inline=False
            )
        else:
            emb.add_field(name="Now Playing", value=f"🎶 {now_title}", inline=False)

        emb.add_field(name="Requested by", value=f"👤 {requester}", inline=True)
        emb.add_field(name="Queue", value=f"📜 {len(st.queue)}", inline=True)

        if st.queue:
            lines: List[str] = []
            for i, t in enumerate(st.queue[:5], start=1):
                lines.append(f"`{i}.` {t.title}")
            if len(st.queue) > 5:
                lines.append(f"…and `{len(st.queue) - 5}` more")
            emb.add_field(name="Up Next", value="\n".join(lines), inline=False)
        else:
            emb.add_field(name="Up Next", value="(empty)", inline=False)

        if thumb:
            emb.set_thumbnail(url=thumb)

        emb.set_footer(text="Use the panel. Stop typing novels in chat.")
        return emb

    def _queue_embed(self, guild: discord.Guild, st: GuildState) -> discord.Embed:
        emb = discord.Embed(title="🎵 Queue", color=discord.Color.blurple())
        if st.current:
            np = st.current.title
            emb.add_field(name="Now", value=f"▶️ {np}", inline=False)
        else:
            emb.add_field(name="Now", value="—", inline=False)

        if st.queue:
            lines: List[str] = []
            for i, t in enumerate(st.queue[:10], start=1):
                lines.append(f"`{i}.` {t.title}")
            if len(st.queue) > 10:
                lines.append(f"…and `{len(st.queue) - 10}` more")
            emb.add_field(name="Next", value="\n".join(lines), inline=False)
        else:
            emb.add_field(name="Next", value="(empty)", inline=False)
        return emb

    def _jump_options(self, st: GuildState) -> List[discord.SelectOption]:
        opts: List[discord.SelectOption] = []
        for i, t in enumerate(st.queue[:25]):
            label = t.title[:95] if t.title else "Unknown"
            opts.append(discord.SelectOption(label=label, value=str(i)))
        if not opts:
            opts.append(
                discord.SelectOption(label="(queue empty)", value="0", default=True)
            )
        return opts

    async def _ensure_panel(self, ctx: commands.Context) -> None:
        if ctx.guild is None:
            return
        st = self._state(ctx.guild.id)
        st.panel_channel_id = ctx.channel.id

        channel = ctx.channel
        if not isinstance(channel, discord.TextChannel):
            return

        view = MusicControlsView(self, ctx.guild.id)
        # add jump select (own row)
        view.add_item(JumpSelect(self, ctx.guild.id, self._jump_options(st)))

        emb = self._panel_embed(ctx.guild, st)

        if st.panel_message_id is not None:
            try:
                msg = await channel.fetch_message(st.panel_message_id)
                await msg.edit(embed=emb, view=view)
                return
            except Exception:
                st.panel_message_id = None

        msg = await channel.send(embed=emb, view=view)
        st.panel_message_id = msg.id

    async def _refresh_panel(self, guild: discord.Guild) -> None:
        st = self._state(guild.id)
        if st.panel_channel_id is None or st.panel_message_id is None:
            return

        ch = guild.get_channel(st.panel_channel_id)
        if not isinstance(ch, discord.TextChannel):
            return

        try:
            msg = await ch.fetch_message(st.panel_message_id)
        except Exception:
            return

        view = MusicControlsView(self, guild.id)
        view.add_item(JumpSelect(self, guild.id, self._jump_options(st)))

        emb = self._panel_embed(guild, st)
        try:
            await msg.edit(embed=emb, view=view)
        except Exception:
            pass

    # ===================== PLAYBACK =====================
    async def _handle_track_end(self, guild: discord.Guild, finished: Track) -> None:
        st = self._state(guild.id)

        async with st.lock:
            skip = st.skip_requested
            st.skip_requested = False

            if not skip:
                if st.loop_mode == "one":
                    st.queue.insert(0, finished)
                elif st.loop_mode == "all":
                    st.queue.append(finished)

            if st.current is finished:
                st.current = None

            st.started_at = None
            st.paused_at = None
            st.paused_total = 0.0

        await self._refresh_panel(guild)
        await self._play_next(guild)

    async def _play_next(self, guild: discord.Guild) -> None:
        st = self._state(guild.id)

        async with st.lock:
            vc_proto = guild.voice_client
            if vc_proto is None:
                st.current = None
                return

            vc = cast(discord.VoiceClient, vc_proto)
            if not vc.is_connected():
                st.current = None
                return

            if vc.is_playing() or vc.is_paused():
                return

            if not st.queue:
                st.current = None
                return

            track = st.queue.pop(0)
            st.current = track

        try:
            src_query = track.query
            if not (
                src_query.startswith("ytsearch") or self._is_youtube_url(src_query)
            ):
                src_query = f"ytsearch1:{src_query}"

            stream_url, resolved_title, dur, webpage_url, thumb = (
                await self._yt_resolve(src_query)
            )
        except Exception as e:
            logging.warning(f"Failed to load track {track.title}: {e}")
            async with st.lock:
                st.current = None
            await self._play_next(guild)
            return

        if not track.title or track.title == "YouTube link":
            track.title = resolved_title
        if track.duration <= 0 and dur > 0:
            track.duration = dur
        if not track.webpage_url:
            track.webpage_url = webpage_url
        if not track.thumbnail:
            track.thumbnail = thumb

        # reset progress timers
        st.started_at = time.monotonic()
        st.paused_at = None
        st.paused_total = 0.0

        before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        vol = max(0.05, float(st.volume))
        opts = f"-vn -filter:a volume={vol}"

        source = discord.FFmpegPCMAudio(
            stream_url,
            executable=self.ffmpeg_path,
            before_options=before_opts,
            options=opts,
        )

        def after(err: Optional[Exception]) -> None:
            if err:
                logging.error(f"Voice playback error: {err}")
            self.bot.loop.call_soon_threadsafe(
                lambda: self.bot.loop.create_task(self._handle_track_end(guild, track))
            )

        vc.play(source, after=after)
        await self._refresh_panel(guild)

    # ===================== COMMANDS (NO SPAM) =====================
    @commands.command(name="controls")
    async def controls(self, ctx: commands.Context):
        if ctx.guild is None:
            return
        await self._ensure_panel(ctx)
        try:
            await ctx.message.add_reaction("✅")
        except Exception:
            pass

    @commands.command(name="play")
    async def play(self, ctx: commands.Context, *, query: Optional[str] = None):
        if ctx.guild is None:
            return

        await self._ensure_panel(ctx)

        if not query:
            try:
                await ctx.message.add_reaction("❓")
            except Exception:
                pass
            return

        query = query.strip()

        vc = await self._ensure_voice(ctx)
        if vc is None:
            try:
                await ctx.message.add_reaction("❌")
            except Exception:
                pass
            return

        st = self._state(ctx.guild.id)
        requester_id = getattr(ctx.author, "id", None)

        kind, _sid = self._spotify_kind_and_id(query)
        try:
            if kind:
                if self.sp is None:
                    raise RuntimeError("Spotify not configured.")
                tracks = await self._spotify_tracks(
                    query, requester_id=requester_id or 0
                )
                async with st.lock:
                    st.queue.extend(tracks)
            else:
                if self._is_youtube_url(query):
                    t = Track(
                        title="YouTube link", query=query, requester_id=requester_id
                    )
                else:
                    t = Track(
                        title=query,
                        query=f"ytsearch1:{query}",
                        requester_id=requester_id,
                    )
                async with st.lock:
                    st.queue.append(t)

            try:
                await ctx.message.add_reaction("✅")
            except Exception:
                pass

            await self._refresh_panel(ctx.guild)
            await self._play_next(ctx.guild)
        except Exception:
            try:
                await ctx.message.add_reaction("❌")
            except Exception:
                pass

    @commands.command(name="skip")
    async def skip(self, ctx: commands.Context):
        if ctx.guild is None:
            return
        st = self._state(ctx.guild.id)
        vc = ctx.guild.voice_client
        if vc and (
            cast(discord.VoiceClient, vc).is_playing()
            or cast(discord.VoiceClient, vc).is_paused()
        ):
            st.skip_requested = True
            cast(discord.VoiceClient, vc).stop()
            try:
                await ctx.message.add_reaction("⏭️")
            except Exception:
                pass

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        if ctx.guild is None:
            return
        st = self._state(ctx.guild.id)
        async with st.lock:
            st.queue.clear()
            st.current = None
            st.skip_requested = False
            st.started_at = None
            st.paused_at = None
            st.paused_total = 0.0
        vc = ctx.guild.voice_client
        if vc and (
            cast(discord.VoiceClient, vc).is_playing()
            or cast(discord.VoiceClient, vc).is_paused()
        ):
            cast(discord.VoiceClient, vc).stop()
        await self._refresh_panel(ctx.guild)

    @commands.command(name="leave")
    async def leave(self, ctx: commands.Context):
        if ctx.guild is None:
            return
        st = self._state(ctx.guild.id)
        async with st.lock:
            st.queue.clear()
            st.current = None
            st.skip_requested = False
            st.started_at = None
            st.paused_at = None
            st.paused_total = 0.0
        vc = ctx.guild.voice_client
        if vc and cast(discord.VoiceClient, vc).is_connected():
            await cast(discord.VoiceClient, vc).disconnect(force=True)
        await self._refresh_panel(ctx.guild)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
