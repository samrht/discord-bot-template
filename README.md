# ✨ Features
## 🎶 Music System (UI-Based)

YouTube audio playback using yt-dlp + FFmpeg

Persistent music control panel with buttons

Play, pause, resume, skip, stop, and loop controls

Queue management without chat spam

Per-user adjustable volume via UI

Progress tracking and track navigation

Automatic voice channel join, move, and disconnect handling

## 🃏 Blackjack Game

Interactive Blackjack game playable in Discord

Per-user game sessions with isolated state

Virtual balance system (default: $1,000,000)

Automatic reset when balance reaches zero

Accurate game logic including ace handling and dealer rules

Embed-based UI updates for a clean experience

## 🛠 Moderation & Utilities

Ban, unban, kick, mute (with duration support)

Automatic role handling for timed mutes

Message clearing with permission checks

User info and server info commands

Permission-aware command execution

## 📖 Custom Help System

Interactive help menu using dropdowns and embeds

Commands organized by cog/category

Replaces traditional text-heavy help commands

## 🧱 Architecture

Modular cog-based structure

Clear separation of music, moderation, utilities, games, and help

Reusable UI components (buttons, selects, modals)

Defensive error handling for stability

# ⚙️ Tech Stack

Python

discord.py (commands & UI views)

yt-dlp for media extraction

FFmpeg for audio streaming

Git & GitHub for version control

# 🚀 Setup
1. Clone the repository
git clone https://github.com/samrht/discord-bot-template.git
cd discord-bot-template

2. Install dependencies
pip install -r requirements.txt

3. Install FFmpeg

Make sure ffmpeg is installed and added to your system PATH.

4. Create a .env file

DISCORD_TOKEN=your_bot_token_here

SPOTIFY_CLIENT_ID=optional

SPOTIFY_CLIENT_SECRET=optional
#### Spotify support is optional and requires API credentials; YouTube playback works without any external APIs.

5. Run the bot
python main.py

# 📌 Notes

Secrets are managed via environment variables (no hardcoded tokens)

Designed to run locally, on a VPS, or cloud platforms

Works across Discord desktop, web, Android, and iOS clients

# 📜 License

This project is open-source and available under the MIT License.
