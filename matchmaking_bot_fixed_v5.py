"""
╔══════════════════════════════════════════════════════════╗
║          UNIVERSAL MATCHMAKING BOT  — main.py            ║
║  Drop-in Elo tracker for any competitive Discord server  ║
╚══════════════════════════════════════════════════════════╝

QUICK-START SETUP
─────────────────
1. Set environment variables (Railway / .env / shell):
     DISCORD_TOKEN          — your bot token (required)
     LEADERBOARD_CHANNEL_ID — channel ID for the auto-updating leaderboard (required)
     MOD_ROLE_ID            — role ID that can run mod commands (required)
     DB_PATH                — path for the SQLite file (optional, defaults to matchmaking.db)

2. Edit the CONFIG block below to match your server's theme,
   rank names, and the categories players pick before a match.

3. Run:  python matchmaking_bot.py
         (or deploy to Railway / any host that supports Python)

4. IMPORTANT — after first launch, run /sync in your server (admin only) to
   register all slash commands with Discord. They may take up to an hour to
   propagate globally, but guild-synced commands appear instantly.

SLASH COMMANDS
──────────────
  Player
    /duel <opponent>                   — challenge another player
    /rank [user]                       — view rating & rank
    /profile [user]                    — full profile embed
    /history [user]                    — last 10 match results
    /setprofile <field> <value>        — edit title / move / color
    /leaderboard                       — force-refresh the leaderboard
    /ranks                             — list all rank tiers
    /categories                        — list all match categories
    /rules                             — show server rules

  Moderator  (requires Manage Messages or the configured mod role)
    /settle <winner> <loser>           — manually resolve a match
    /adjust <user> <amount>            — add or subtract rating points
    /add_category <name> [tier]        — add a new category
    /remove_category <name>            — remove a category
    /tourney_open                      — open tournament registration
    /tourney_start                     — generate the bracket
    /tourney_add <user>                — manually add player to roster
    /tourney_kick <user>               — remove player from roster
    /tourney_list                      — view current roster
    /tourney_reward <1st> <2nd> [3rd]  — award RP prizes
    /tourney_end                       — close tournament session
    /clear [amount]                    — purge messages

  Admin  (requires Administrator)
    /setup                             — post the welcome embed
    /backup                            — DM yourself the database
    /fix_database                      — patch missing DB columns
    /sync                              — sync slash commands with Discord
"""

import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import os
import asyncio
from threading import Thread
from flask import Flask, jsonify
from flask_cors import CORS


# ──────────────────────────────────────────────────────────────────────────────
#  ██████╗ ██████╗ ███╗   ██╗███████╗██╗ ██████╗
#  ██╔════╝██╔═══██╗████╗  ██║██╔════╝██║██╔════╝
#  ██║     ██║   ██║██╔██╗ ██║█████╗  ██║██║  ███╗
#  ██║     ██║   ██║██║╚██╗██║██╔══╝  ██║██║   ██║
#  ╚██████╗╚██████╔╝██║ ╚████║██║     ██║╚██████╔╝
#   ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝     ╚═╝ ╚═════╝
#
#  ← EDIT THIS BLOCK TO CUSTOMISE YOUR SERVER ←
# ──────────────────────────────────────────────────────────────────────────────

CONFIG = {
    # ── Branding ──────────────────────────────────────────────────────────────
    "server_name": "My Arena",          # Used in embeds and footers

    # ── Rating System ─────────────────────────────────────────────────────────
    "rating_label":    "RP",            # Label for points (RP / ELO / MMR / SR …)
    "starting_points": 1000,            # New-player starting rating
    "k_factor":        32,              # Elo K-factor (higher = faster rank changes)

    # ── Rank Tiers ────────────────────────────────────────────────────────────
    # List from HIGHEST to LOWEST. Discord role names must match "name" exactly.
    # emoji: standard Unicode emoji OR a Discord custom emoji string.
    "ranks": [
        {"name": "DIAMOND",  "emoji": "💎", "min": 1800, "color": 0x00ffff},
        {"name": "PLATINUM", "emoji": "🔮", "min": 1600, "color": 0xe5e4e2},
        {"name": "GOLD",     "emoji": "🥇", "min": 1400, "color": 0xffd700},
        {"name": "SILVER",   "emoji": "🥈", "min": 1200, "color": 0xc0c0c0},
        {"name": "BRONZE",   "emoji": "🥉", "min":    0, "color": 0xcd7f32},
    ],

    # ── Pre-match Category Selection ──────────────────────────────────────────
    # Players pick one of these before each match (deck / hero / character …)
    # Set to [] to skip category selection entirely.
    "category_label": "Category",       # Shown in prompts ("Deck" / "Hero" …)
    "categories": [
        # (name, tier)  ←  tier is optional flavour; use "" if not relevant
        ("Option A", "S"),
        ("Option B", "A"),
        ("Option C", "B"),
        ("Wildcard", ""),
    ],

    # ── Tournament Prizes (rating points) ─────────────────────────────────────
    "tourney_prizes": {1: 150, 2: 75, 3: 30},

    # ── Forfeit Timer ─────────────────────────────────────────────────────────
    # Seconds after the first result report before an auto-win is granted.
    "forfeit_seconds": 1800,            # 1800 = 30 minutes

    # ── Web Leaderboard URL ───────────────────────────────────────────────────
    # Set to "" to hide the "View Full Rankings" link button.
    "leaderboard_url": "",

    # ── Welcome / Intro Embed ─────────────────────────────────────────────────
    "intro_title": "Welcome to My Arena",
    "intro_description": (
        "The definitive competitive hub for this server, powered by an automated "
        "Elo tracker that records every match, calculates ratings, and maintains a "
        "live leaderboard.\n\nChallenge rivals, climb the ranks, and prove yourself."
    ),
    "rules_text": (
        "**1. Match Reporting**\n"
        "Both players must confirm the result immediately after a match. "
        "False reporting will result in penalties.\n\n"
        "**2. Disputes**\n"
        "If a dispute occurs, click the 🛠️ button — the timer pauses and a mod is pinged.\n\n"
        "**3. Sportsmanship**\n"
        "Toxic behaviour, stalling, or intentional disconnects are prohibited."
    ),
}

# ──────────────────────────────────────────────────────────────────────────────
#  END OF CONFIG — you shouldn't need to touch anything below this line.
# ──────────────────────────────────────────────────────────────────────────────

TOKEN                  = os.environ["DISCORD_TOKEN"]
LEADERBOARD_CHANNEL_ID = int(os.environ["LEADERBOARD_CHANNEL_ID"])
MOD_ROLE_ID            = int(os.environ["MOD_ROLE_ID"])
DB_NAME                = os.getenv("DB_PATH", "matchmaking.db")

RANKS           = CONFIG["ranks"]
RATING_LABEL    = CONFIG["rating_label"]
STARTING_POINTS = CONFIG["starting_points"]
K_FACTOR        = CONFIG["k_factor"]
SERVER_NAME     = CONFIG["server_name"]
CATEGORY_LABEL  = CONFIG["category_label"]
USE_CATEGORIES  = bool(CONFIG["categories"])


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════════════════════════


# SQLite tuning + basic concurrency guard (discord interactions can hit DB concurrently).
DB_LOCK = asyncio.Lock()

def db_connect():
    """Return a SQLite connection configured for concurrent-ish usage."""
    conn = sqlite3.connect(DB_NAME, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
    except Exception:
        pass
    return conn

def init_db():
    db_dir = os.path.dirname(DB_NAME)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir)
        except OSError:
            pass

    conn = db_connect()
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        guild_id TEXT,
        user_id  TEXT,
        name     TEXT,
        points   INTEGER,
        wins     INTEGER,
        losses   INTEGER,
        streak   INTEGER,
        history  TEXT,
        PRIMARY KEY (guild_id, user_id)

    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS config (
        guild_id TEXT,
        key      TEXT,
        value    TEXT,
        PRIMARY KEY (guild_id, key)

    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS profiles (
        guild_id       TEXT,
        user_id        TEXT,
        title          TEXT DEFAULT 'Newcomer',
        signature_move TEXT DEFAULT 'None',
        embed_color    TEXT,
        PRIMARY KEY (guild_id, user_id)

    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS categories (
        guild_id TEXT,
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT,
        tier     TEXT DEFAULT '',
        UNIQUE (guild_id, name)

    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS matches (
        guild_id   TEXT,
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        p1_id      TEXT,
        p2_id      TEXT,
        p1_cat     TEXT,
        p2_cat     TEXT,
        winner_id  TEXT,
        status     TEXT DEFAULT 'active',
        notes      TEXT DEFAULT '',
        channel_id INTEGER,
        message_id INTEGER,
        timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP

    )""")

    c.executemany(
        "INSERT OR IGNORE INTO categories (name, tier) VALUES (?, ?)",
        CONFIG["categories"]
    )

    conn.commit()
    conn.close()
    print(f"✅ Database ready: {DB_NAME}")


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def get_or_create_user(guild_id, user_id, name):
    """Fetch a user row; create/update name if needed."""
    async with DB_LOCK:
        conn = db_connect()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE guild_id = ? AND user_id = ?", (str(guild_id), str(user_id)))
        user = c.fetchone()
        if user is None:
            user = (str(guild_id), str(user_id), name, STARTING_POINTS, 0, 0, 0, "")
            c.execute("INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)", user)
            conn.commit()
        elif user[2] != name:
            c.execute("UPDATE users SET name=? WHERE guild_id=? AND user_id=?", (name, str(guild_id), str(user_id)))
            c.execute("SELECT * FROM users WHERE guild_id = ? AND user_id = ?", (str(guild_id), str(user_id)))
            user = c.fetchone()
            conn.commit()
        conn.close()
        return user

async def update_user_stats(u_id, pts, wins, losses, streak, history):
    async with DB_LOCK:
        conn = db_connect()
        c = conn.cursor()
        if isinstance(history, str):
            history = history.split(",") if history else []
        hist_str = ",".join(history[-10:])
        c.execute(
            "UPDATE users SET points=?, wins=?, losses=?, streak=?, history=? WHERE guild_id=? AND user_id=?",
            (int(pts), int(wins), int(losses), int(streak), hist_str, str(u_id))
        )
        conn.commit()
        conn.close()

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_rank_info(points):
    for rank in RANKS:
        if points >= rank["min"]:
            return rank
    return RANKS[-1]


def elo_gain(winner_pts, loser_pts):
    expected = 1 / (1 + 10 ** ((loser_pts - winner_pts) / 400))
    return max(1, round(K_FACTOR * (1 - expected)))


async def update_player_role(member, points):
    rank_info   = get_rank_info(points)
    target_name = rank_info["name"]
    all_names   = [r["name"] for r in RANKS]
    role = discord.utils.get(member.guild.roles, name=target_name)

    if not role or role in member.roles:
        return

    try:
        to_remove = [r for r in member.roles if r.name in all_names]
        if to_remove:
            await member.remove_roles(*to_remove, reason="Matchmaking rank update")
        await member.add_roles(role, reason="Matchmaking rank update")
    except discord.Forbidden:
        return
    except discord.HTTPException:
        return


def build_progress_bar(points):
    r_info    = get_rank_info(points)
    next_rank = next((r for r in reversed(RANKS) if r["min"] > points), None)
    if next_rank:
        total   = next_rank["min"] - r_info["min"]
        current = points - r_info["min"]
        pct     = min(max(current / total, 0.0), 1.0)
        filled  = int(pct * 10)
        bar     = "▰" * filled + "▱" * (10 - filled)
        pct_str = f"{int(pct * 100)}% → {next_rank['emoji']} {next_rank['name']}"
        return bar, pct_str, next_rank
    return "▰" * 10, "MAX RANK REACHED", None


# ── Permission checks ─────────────────────────────────────────────────────────

def has_mod_role():
    """Slash command check: requires Manage Messages OR the configured mod role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_messages:
            return True
        role = interaction.guild.get_role(MOD_ROLE_ID)
        if role and role in interaction.user.roles:
            return True
        await interaction.response.send_message(
            "❌ You need the moderator role to use this command.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


def has_admin():
    """Slash command check: requires Administrator permission."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        await interaction.response.send_message(
            "❌ You need Administrator permission to use this command.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


# ══════════════════════════════════════════════════════════════════════════════
#  TOURNAMENT STATE  (in-memory; resets on restart)
# ══════════════════════════════════════════════════════════════════════════════

tournament_players: list = []
tournament_bracket: list = []
tournament_active:  bool = False


# ══════════════════════════════════════════════════════════════════════════════
#  DISCORD UI  —  Views & Selects
# ══════════════════════════════════════════════════════════════════════════════

class LeaderboardWebView(discord.ui.View):
    def __init__(self, url):
        super().__init__(timeout=None)
        if url:
            self.add_item(discord.ui.Button(
                label="View Full Rankings",
                url=url,
                style=discord.ButtonStyle.link,
                emoji="🌐"
            ))


class CategorySelect(discord.ui.Select):
    def __init__(self, guild_id: int, match_id: int, player_id: int, player_name: str, slot: str):
        """
        slot: "p1" or "p2" — which column to write (p1_cat / p2_cat)
        """
        self.guild_id = guild_id
        conn = db_connect()
        c = conn.cursor()
        c.execute("SELECT name FROM categories WHERE guild_id = ?", (str(guild_id),))
        cats = [row[0] for row in c.fetchall()]
        conn.close()

        options = [discord.SelectOption(label=cat) for cat in cats[:25]]
        super().__init__(
            placeholder=f"{player_name}, pick your {CATEGORY_LABEL}…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"cat_{match_id}_{slot}_{player_id}",
        )
        self.match_id = match_id
        self.player_id = player_id
        self.slot = slot  # "p1" or "p2"

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message("This selector isn't for you!", ephemeral=True)

        chosen = self.values[0]
        col = "p1_cat" if self.slot == "p1" else "p2_cat"

        async with DB_LOCK:
            conn = db_connect()
            c = conn.cursor()
            c.execute(f"UPDATE matches SET {col}=? WHERE id=?", (chosen, self.match_id))
            conn.commit()
            conn.close()

        await interaction.response.send_message(f"✅ Selected **{chosen}**.", ephemeral=True)


class MatchReportingView(discord.ui.View):
    def __init__(self, p1: discord.Member, p2: discord.Member, match_id: int):
        super().__init__(timeout=7200.0)
        self.p1, self.p2 = p1, p2
        self.match_id = match_id

        # participant -> reported winner_id (or None)
        self.reports = {p1.id: None, p2.id: None}

        # Allow safe edits after long waits without relying on stale interactions
        self.bound_channel_id: int | None = None
        self.bound_message_id: int | None = None
        self.bound_guild_id: int | None = None
        self.bound_client: discord.Client | None = None

        self.forfeit_task: asyncio.Task | str | None = None

        # Set button labels dynamically
        self.report_p1.label = f"{p1.display_name} Won"
        self.report_p2.label = f"{p2.display_name} Won"

    async def _fetch_bound_message(self):
        """Fetch the message this view is attached to, if we have enough info."""
        if not (self.bound_client and self.bound_channel_id and self.bound_message_id):
            return None
        channel = self.bound_client.get_channel(self.bound_channel_id)
        if channel is None:
            try:
                channel = await self.bound_client.fetch_channel(self.bound_channel_id)
            except Exception:
                return None
        try:
            return await channel.fetch_message(self.bound_message_id)
        except Exception:
            return None

    async def start_forfeit_timer(self):
        # Don't keep an Interaction object alive for long sleeps (it can go stale).
        await asyncio.sleep(CONFIG["forfeit_seconds"])

        p1_rep, p2_rep = self.reports[self.p1.id], self.reports[self.p2.id]
        if (p1_rep and not p2_rep) or (p2_rep and not p1_rep):
            winner_id = p1_rep if p1_rep else p2_rep
            await self.finalize(None, winner_id, forfeit=True)

    async def finalize(self, interaction: discord.Interaction | None, winner_id: int, forfeit: bool = False):
        w_mem = self.p1 if winner_id == self.p1.id else self.p2
        l_mem = self.p2 if winner_id == self.p1.id else self.p1

        note = "Auto-Forfeit" if forfeit else "Standard"
        async with DB_LOCK:
            conn = db_connect()
            c = conn.cursor()
            c.execute(
                "UPDATE matches SET winner_id=?, status='completed', notes=? WHERE guild_id=? AND id=?",
                (str(winner_id), note, str(self.guild_id), self.match_id),
            )
            conn.commit()
            conn.close()

        w_data = await get_or_create_user(str(interaction.guild_id), w_mem.id, w_mem.display_name)
        l_data = await get_or_create_user(str(interaction.guild_id), l_mem.id, l_mem.display_name)
        r1, r2 = w_data[3], l_data[3]
        pts = elo_gain(r1, r2)

        w_hist = w_data[7].split(",") if w_data[7] else []
        l_hist = l_data[7].split(",") if l_data[7] else []
        w_hist.append(f"W:{l_mem.display_name}:{pts}")
        l_hist.append(f"L:{w_mem.display_name}:{pts}")

        await update_user_stats(str(interaction.guild_id), w_mem.id, r1 + pts, w_data[4] + 1, w_data[5], w_data[6] + 1, w_hist)
        await update_user_stats(str(interaction.guild_id), l_mem.id, r2 - pts, l_data[4], l_data[5] + 1, 0, l_hist)

        await update_player_role(w_mem, r1 + pts)
        await update_player_role(l_mem, r2 - pts)

        # Disable all interactive components
        for child in self.children:
            child.disabled = True

        result_line = f"🏆 **Winner:** {w_mem.mention}"
        if forfeit:
            result_line += " *(forfeit)*"

        embed = discord.Embed(
            title="✅ MATCH COMPLETE",
            description=f"**{self.p1.display_name}** vs **{self.p2.display_name}**\n\n{result_line}",
            color=0x2ECC71,
        )
        embed.set_footer(text=f"{SERVER_NAME} • Updated ladder")

        # Prefer editing via the current interaction if possible; else fetch message by IDs.
        edited = False
        if interaction is not None:
            try:
                await interaction.response.edit_message(embed=embed, view=self)
                edited = True
            except Exception:
                edited = False

        if not edited:
            msg = await self._fetch_bound_message()
            if msg:
                try:
                    await msg.edit(embed=embed, view=self)
                except Exception:
                    pass

        try:
            if self.bound_client and self.bound_guild_id:
                g = self.bound_client.get_guild(self.bound_guild_id)
                if g:
                    await refresh_leaderboard(g)
        except Exception:
            pass

    async def check_reports(self, interaction: discord.Interaction):
        p1_rep, p2_rep = self.reports[self.p1.id], self.reports[self.p2.id]

        if p1_rep and p2_rep:
            if self.forfeit_task and self.forfeit_task != "PAUSED":
                try:
                    self.forfeit_task.cancel()
                except Exception:
                    pass

            if p1_rep != p2_rep:
                embed = discord.Embed(
                    title="⚠️ MATCH DISPUTE",
                    description=(
                        f"**{self.p1.display_name}** and **{self.p2.display_name}** "
                        f"reported different results.\n"
                        f"<@&{MOD_ROLE_ID}> must resolve this via `/settle`."
                    ),
                    color=0xE74C3C,
                )
                return await interaction.response.edit_message(content=f"<@&{MOD_ROLE_ID}>", embed=embed, view=None)

            return await self.finalize(interaction, p1_rep)

        # Exactly one person has reported so far
        if not self.forfeit_task:
            other = self.p2 if interaction.user.id == self.p1.id else self.p1
            self.forfeit_task = asyncio.create_task(self.start_forfeit_timer())
            mins = CONFIG["forfeit_seconds"] // 60
            return await interaction.response.edit_message(
                content=(
                    f"⏳ **{interaction.user.display_name}** reported. "
                    f"<@{other.id}> has **{mins} minutes** to confirm or receive an auto-loss."
                )
            )

        return await interaction.response.edit_message(content="⏳ Still waiting for opponent to confirm…")

    @discord.ui.button(label="Player A Won", style=discord.ButtonStyle.success, emoji="⚔️", row=1)
    async def report_p1(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1.id, self.p2.id):
            return await interaction.response.send_message("You're not a participant in this match.", ephemeral=True)
        self.reports[interaction.user.id] = self.p1.id
        await self.check_reports(interaction)

    @discord.ui.button(label="Player B Won", style=discord.ButtonStyle.success, emoji="⚔️", row=1)
    async def report_p2(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1.id, self.p2.id):
            return await interaction.response.send_message("You're not a participant in this match.", ephemeral=True)
        self.reports[interaction.user.id] = self.p2.id
        await self.check_reports(interaction)

    @discord.ui.button(label="Dispute / Technical Issue", style=discord.ButtonStyle.secondary, emoji="🛠️", row=2)
    async def pause_timer(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.p1.id, self.p2.id):
            return await interaction.response.send_message("You're not a participant in this match.", ephemeral=True)

        if self.forfeit_task and self.forfeit_task != "PAUSED":
            try:
                self.forfeit_task.cancel()
            except Exception:
                pass
            self.forfeit_task = "PAUSED"

        embed = discord.Embed(
            title="🛠️ MATCH FROZEN",
            description=(
                f"**{interaction.user.display_name}** flagged an issue.\n"
                f"Auto-forfeit disabled. <@&{MOD_ROLE_ID}> review required."
            ),
            color=0x95A5A6,
        )
        await interaction.response.edit_message(content=f"<@&{MOD_ROLE_ID}>", embed=embed, view=None)


class ChallengeView(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=300)
        self.p1, self.p2 = p1, p2

    @discord.ui.button(label="Accept Match", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.p2.id:
            return await interaction.response.send_message(
                "This challenge isn't for you!", ephemeral=True
            )

        async with DB_LOCK:
            conn = db_connect()
            c = conn.cursor()
            # Prevent duplicate active matches between the same two players.
            c.execute(
                """SELECT id FROM matches
                   WHERE guild_id=? AND status='active'
                     AND ((p1_id=? AND p2_id=?) OR (p1_id=? AND p2_id=?))
                   ORDER BY id DESC LIMIT 1""",
                (str(self.p1.id), str(self.p2.id), str(self.p2.id), str(self.p1.id))
            )
            existing = c.fetchone()
            if existing:
                conn.close()
                return await interaction.response.send_message(
                    "⚠️ You already have an active match with this player. Please report/settle it first.",
                    ephemeral=True
                )

            c.execute(
                "INSERT INTO matches (guild_id, p1_id, p2_id, status) VALUES (?, ?, ?, 'active')",
                (str(self.guild_id), str(self.p1.id), str(self.p2.id))
            )
            match_id = c.lastrowid
            conn.commit()
            conn.close()

        step_text = (
            f"**Step 1:** Both players select their **{CATEGORY_LABEL}** below.\n"
            "**Step 2:** Report the match result using the buttons."
        ) if USE_CATEGORIES else "Report the match result using the buttons below."

        embed = discord.Embed(
            title="⚔️ MATCH ACTIVE",
            description=f"**{self.p1.display_name}** vs **{self.p2.display_name}**\n\n{step_text}",
            color=0x3498db
        )
        view = MatchReportingView(self.p1, self.p2, match_id)
        # Bind message/channel/guild so timeouts can safely edit later.
        view.bound_client = interaction.client
        view.bound_channel_id = interaction.channel.id if interaction.channel else None
        view.bound_message_id = interaction.message.id if interaction.message else None
        view.bound_guild_id = interaction.guild.id if interaction.guild else None

        if USE_CATEGORIES:
            view.add_item(CategorySelect(self.guild_id, match_id, self.p1.id, self.p1.display_name, slot='p1'))
            view.add_item(CategorySelect(self.guild_id, match_id, self.p2.id, self.p2.display_name, slot='p2'))

        await interaction.response.edit_message(content=None, embed=embed, view=view)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.p2.id:
            return await interaction.response.send_message(
                "This challenge isn't for you!", ephemeral=True
            )
        await interaction.response.edit_message(
            content=f"❌ **{self.p2.display_name}** declined the challenge.",
            embed=None, view=None
        )


# ══════════════════════════════════════════════════════════════════════════════
#  BOT SETUP
# ══════════════════════════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.members = True  # needed for reliable role updates
bot  = commands.Bot(command_prefix="$unused$", intents=intents)

tree = bot.tree

# --- App command error handler (prevents silent "did not respond") ---
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = f"❌ An error occurred: `{error}`"
    try:
        await interaction.response.send_message(msg, ephemeral=True)
    except discord.InteractionResponded:
        await interaction.followup.send(msg, ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
#  LEADERBOARD REFRESH
# ══════════════════════════════════════════════════════════════════════════════

async def refresh_leaderboard(guild):
    guild_id = str(guild.id)
    try:
        channel = guild.get_channel(LEADERBOARD_CHANNEL_ID)
        if not channel:
            print("⚠️ Leaderboard channel not found.")
            return

        async with DB_LOCK:
            conn = db_connect()
            c = conn.cursor()
            c.execute("SELECT name, points, streak FROM users WHERE guild_id = ? ORDER BY points DESC LIMIT 10", (str(guild_id),))
            top = c.fetchall()
            c.execute("SELECT value FROM config WHERE guild_id = ? AND key = 'leaderboard_msg_id'", (str(guild_id),))
            row = c.fetchone()
            saved_msg_id = None
            if row and row[0] is not None:
                try:
                    saved_msg_id = int(row[0])
                except (TypeError, ValueError):
                    saved_msg_id = None
            conn.close()

        embed = discord.Embed(
            title=f"🏆 {SERVER_NAME}: TOP 10",
            color=0xFFD700,
            timestamp=discord.utils.utcnow()
        )
        desc = ""
        for i, (name, pts, streak) in enumerate(top, 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
            fire  = "🔥" if streak >= 3 else ""
            desc += f"{medal} **{name}** {fire} — `{pts} {RATING_LABEL}`\n"
        embed.description = desc or "No matches recorded yet."
        embed.set_footer(text="Updates after every match")

        view = LeaderboardWebView(CONFIG["leaderboard_url"])
        msg  = None

        if saved_msg_id:
            try:
                msg = await channel.fetch_message(saved_msg_id)
                await msg.edit(embed=embed, view=view)
            except (discord.NotFound, discord.Forbidden):
                msg = await channel.send(embed=embed, view=view)
        else:
            msg = await channel.send(embed=embed, view=view)

        if msg and (not saved_msg_id or msg.id != saved_msg_id):
            async with DB_LOCK:
                conn = db_connect()
                c = conn.cursor()
                c.execute(
                    "INSERT OR REPLACE INTO config (guild_id, key, value) VALUES (?, 'leaderboard_msg_id', ?)",
                    (str(guild_id), str(msg.id))
                )
                conn.commit()
                conn.close()

    except Exception as e:
        print(f"❌ Leaderboard refresh error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    init_db()
    try:
        synced = await tree.sync()
        print(f"✅ Synced {len(synced)} slash commands globally.")
    except Exception as e:
        print(f"Sync error: {e}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=f"{SERVER_NAME} | /duel"
    ))
    print(f"✅ Logged in as {bot.user.name} — {SERVER_NAME} ready.")


# ══════════════════════════════════════════════════════════════════════════════
#  PLAYER SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@tree.command(name="duel", description="Challenge another player to a match.")
@app_commands.describe(opponent="The player you want to challenge")
async def duel(interaction: discord.Interaction, opponent: discord.Member):
    if opponent == interaction.user:
        return await interaction.response.send_message("❌ You can't challenge yourself!", ephemeral=True)
    if opponent.bot:
        return await interaction.response.send_message("❌ Bots don't accept challenges.", ephemeral=True)

    view  = ChallengeView(interaction.user, opponent)
    embed = discord.Embed(
        title="⚔️ CHALLENGE ISSUED",
        description=(f"{opponent.mention}, **{interaction.user.display_name}** has challenged you!\n"
                     "Do you accept?"),
        color=0x7289da
    )
    embed.set_footer(text=f"{SERVER_NAME} • Awaiting Response")
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="rank", description="View current rating and rank.")
@app_commands.describe(user="The player to look up (defaults to you)")
async def rank(interaction: discord.Interaction, user: discord.Member = None):
    member = user or interaction.user
    data   = await get_or_create_user(str(interaction.guild_id), member.id, member.display_name)
    pts    = data[3]
    r_info = get_rank_info(pts)
    bar, pct_str, _ = build_progress_bar(pts)

    total_games = data[4] + data[5]
    win_rate    = round((data[4] / total_games) * 100) if total_games > 0 else 0

    embed = discord.Embed(title=f"{r_info['emoji']} {member.display_name}", color=r_info["color"])
    embed.add_field(name=f"🏆 {RATING_LABEL}", value=f"`{pts}`",                               inline=True)
    embed.add_field(name="🔥 Streak",           value=f"{data[6]} win(s)",                      inline=True)
    embed.add_field(name="⚔️ Record",           value=f"{data[4]}W - {data[5]}L ({win_rate}%)", inline=True)
    embed.add_field(name="🚀 Progress",         value=f"{bar} {pct_str}",                       inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="profile", description="View a full profile with title, move, and stats.")
@app_commands.describe(user="The player to look up (defaults to you)")
async def profile(interaction: discord.Interaction, user: discord.Member = None):
    member = user or interaction.user
    data   = await get_or_create_user(str(interaction.guild_id), member.id, member.display_name)
    pts    = data[3]

    async with DB_LOCK:
        conn = db_connect()
        c = conn.cursor()
        c.execute("SELECT title, signature_move, embed_color FROM profiles WHERE guild_id = ? AND user_id = ?",
                  (str(interaction.guild_id), str(member.id)))
        bio = c.fetchone() or ("Newcomer", "None", None)
        conn.close()
    p_title, p_move, p_color = bio

    r_info = get_rank_info(pts)
    bar, pct_str, _ = build_progress_bar(pts)

    try:
        color_value = int(p_color, 16) if p_color else r_info["color"]
    except Exception:
        color_value = r_info["color"]

    total_games = data[4] + data[5]
    wr = round((data[4] / total_games) * 100) if total_games > 0 else 0

    embed = discord.Embed(title=f"{r_info['emoji']} {member.display_name}", color=color_value)
    embed.add_field(name="📜 Title",           value=f"*{p_title}*",                     inline=True)
    embed.add_field(name="✨ Signature Move",  value=f"**{p_move}**",                    inline=True)
    embed.add_field(name=f"🏆 {RATING_LABEL}", value=f"`{pts}`",                         inline=True)
    embed.add_field(name="⚔️ Record",          value=f"{data[4]}W - {data[5]}L ({wr}%)", inline=True)
    embed.add_field(name="🔥 Streak",          value=f"{data[6]} win(s)",                inline=True)
    embed.add_field(name="🚀 Progress",        value=f"{bar} {pct_str}",                 inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=SERVER_NAME)
    await interaction.response.send_message(embed=embed)


@tree.command(name="history", description="View the last 10 match results.")
@app_commands.describe(user="The player to look up (defaults to you)")
async def history(interaction: discord.Interaction, user: discord.Member = None):
    member   = user or interaction.user
    data     = await get_or_create_user(str(interaction.guild_id), member.id, member.display_name)
    raw_hist = data[7].split(",") if data[7] else []

    if not raw_hist:
        return await interaction.response.send_message(
            f"No match history for **{member.display_name}** yet.", ephemeral=True
        )

    display = ""
    for entry in reversed(raw_hist):
        parts = entry.split(":")
        if len(parts) >= 3:
            res, opp, rp = parts[0], parts[1], parts[2]
            circle = "🟢" if res == "W" else "🔴"
            prefix = "+" if res == "W" else "-"
            display += f"{circle} **{res}** vs {opp} (`{prefix}{rp} {RATING_LABEL}`)\n"
        elif parts[0]:
            display += f"{'🟢' if parts[0]=='W' else '🔴'} **{parts[0]}** (Legacy)\n"

    embed = discord.Embed(
        title=f"📜 {member.display_name}'s History",
        description=display or "No entries.",
        color=0x3498db
    )
    embed.set_footer(text="Last 10 matches")
    await interaction.response.send_message(embed=embed)


@tree.command(name="setprofile", description="Customise your profile title, signature move, or colour.")
@app_commands.describe(
    field="Field to update",
    value="The new value (color must be a hex code, e.g. ff0000)"
)
@app_commands.choices(field=[
    app_commands.Choice(name="title", value="title"),
    app_commands.Choice(name="move",  value="move"),
    app_commands.Choice(name="color", value="color"),
])
async def setprofile(interaction: discord.Interaction, field: str, value: str):
    col_map = {"title": "title", "move": "signature_move", "color": "embed_color"}

    if field == "color":
        value = value.lstrip("#").lower()
        try:
            int(value, 16)
        except ValueError:
            return await interaction.response.send_message(
                "❌ Colour must be a hex value, e.g. `ff0000`", ephemeral=True
            )

    async with DB_LOCK:
        conn = db_connect()
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO profiles (user_id) VALUES (?)", (str(interaction.user.id),))
        c.execute("INSERT OR IGNORE INTO profiles (guild_id, user_id) VALUES (?, ?)", (str(interaction.guild_id), str(interaction.user.id)))
        c.execute(f"UPDATE profiles SET {col_map[field]} = ? WHERE guild_id = ? AND user_id = ?",
                  (value, str(interaction.guild_id), str(interaction.user.id)))
        conn.commit()
        conn.close()
    await interaction.response.send_message(
        f"✅ Your **{field}** has been updated to: `{value}`", ephemeral=True
    )


@tree.command(name="leaderboard", description="Force-refresh the leaderboard channel.")
async def leaderboard(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    await refresh_leaderboard(interaction.guild)
    await interaction.followup.send("✅ Leaderboard refreshed!", ephemeral=True)


@tree.command(name="ranks", description="Show all rank tiers and rating thresholds.")
async def ranks(interaction: discord.Interaction):
    embed = discord.Embed(
        title=f"📊 {SERVER_NAME} — RANK TIERS",
        description=f"Earn {RATING_LABEL} by winning matches to climb the ladder!",
        color=0xffffff
    )
    lines = "\n".join(
        f"{r['emoji']} **{r['name']}** — `{r['min']}+ {RATING_LABEL}`" for r in RANKS
    )
    embed.add_field(name="Tiers (highest → lowest)", value=lines, inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="categories", description="List all registered match categories.")
async def categories(interaction: discord.Interaction):
    if not USE_CATEGORIES:
        return await interaction.response.send_message(
            "ℹ️ Category tracking is disabled for this server.", ephemeral=True
        )

    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT name, tier FROM categories ORDER BY tier")
    rows = c.fetchall()
    conn.close()

    if not rows:
        return await interaction.response.send_message(
            f"📭 No {CATEGORY_LABEL.lower()}s registered yet.", ephemeral=True
        )

    grouped = {}
    for name, tier in rows:
        grouped.setdefault(tier or "Other", []).append(name)

    tier_emojis = {"S": "⭐", "A": "🥇", "B": "🥈", "C": "🥉", "Other": "🃏"}
    embed = discord.Embed(title=f"⚔️ {SERVER_NAME}: {CATEGORY_LABEL} List", color=0x2f3136)
    for tier, names in grouped.items():
        embed.add_field(
            name=f"{tier_emojis.get(tier, '🃏')} {tier}",
            value=" • ".join(names),
            inline=False
        )
    await interaction.response.send_message(embed=embed)


@tree.command(name="rules", description="Display the server rules and ranking system.")
async def rules(interaction: discord.Interaction):
    r_summary = "\n".join(
        f"{r['emoji']} **{r['name']}**: `{r['min']}+ {RATING_LABEL}`" for r in RANKS
    )
    embed = discord.Embed(
        title=f"🛡️ {SERVER_NAME} — OFFICIAL RULES",
        description=CONFIG["rules_text"],
        color=0x7289da
    )
    embed.add_field(name="📊 RANKING SYSTEM", value=r_summary, inline=False)
    embed.set_footer(text="Play fair. Compete hard.")
    await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
#  MODERATOR SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@tree.command(name="settle", description="[MOD] Manually resolve a disputed or unrecorded match.")
@app_commands.describe(winner="The player who won", loser="The player who lost")
@has_mod_role()
async def settle(interaction: discord.Interaction, winner: discord.Member, loser: discord.Member):
    await interaction.response.defer()

    conn = db_connect()
    c = conn.cursor()
    c.execute("""
        SELECT id FROM matches
        WHERE guild_id=? AND ((p1_id=? AND p2_id=?) OR (p1_id=? AND p2_id=?))
          AND status='active'
        ORDER BY timestamp DESC LIMIT 1
    """, (str(interaction.guild_id), str(winner.id), str(loser.id), str(loser.id), str(winner.id)))
    row = c.fetchone()

    if row:
        c.execute(
            "UPDATE matches SET winner_id=?, status='completed', notes='Mod-settled' WHERE id=?",
            (str(winner.id), row[0])
        )
    else:
        c.execute(
            "INSERT INTO matches (guild_id, p1_id, p2_id, winner_id, status, notes) VALUES (?,?,?,?,'completed','Mod-settled')",
            (str(interaction.guild_id), str(winner.id), str(loser.id), str(winner.id))
        )
    conn.commit()
    conn.close()

    w_data = await get_or_create_user(self.guild_id, winner.id, winner.display_name)
    l_data = await get_or_create_user(self.guild_id, loser.id, loser.display_name)
    r1, r2 = w_data[3], l_data[3]
    pts    = elo_gain(r1, r2)

    w_hist = w_data[7].split(",") if w_data[7] else []
    l_hist = l_data[7].split(",") if l_data[7] else []
    w_hist.append(f"W:{loser.display_name}:{pts}")
    l_hist.append(f"L:{winner.display_name}:{pts}")

    await update_user_stats(str(interaction.guild_id), winner.id, r1 + pts, w_data[4] + 1, w_data[5],     w_data[6] + 1, w_hist)
    await update_user_stats(str(interaction.guild_id), loser.id,  r2 - pts, l_data[4],     l_data[5] + 1, 0,             l_hist)

    await update_player_role(winner, r1 + pts)
    await update_player_role(loser,  r2 - pts)
    await refresh_leaderboard(interaction.guild)

    embed = discord.Embed(title="⚖️ MATCH SETTLED", color=0x2ecc71)
    embed.description = f"**{winner.display_name}** defeats **{loser.display_name}**"
    embed.add_field(
        name="Result",
        value=(f"📈 **{winner.display_name}**: `+{pts} {RATING_LABEL}`\n"
               f"📉 **{loser.display_name}**: `-{pts} {RATING_LABEL}`"),
        inline=False
    )
    embed.set_footer(text=f"Settled by {interaction.user.display_name}")
    await interaction.followup.send(embed=embed)


@tree.command(name="adjust", description="[MOD] Add or subtract rating points from a player.")
@app_commands.describe(
    user="The player to adjust",
    amount="Points to add (positive) or subtract (negative)"
)
@has_mod_role()
async def adjust(interaction: discord.Interaction, user: discord.Member, amount: int):
    data    = await get_or_create_user(str(interaction.guild_id), user.id, user.display_name)
    new_pts = max(0, data[4] + amount)
    conn    = db_connect()
    c       = conn.cursor()
    c.execute("UPDATE users SET points=? WHERE guild_id=? AND user_id=?", (new_pts, str(interaction.guild_id), str(user.id)))
    conn.commit()
    conn.close()
    await update_player_role(user, new_pts)
    sign = "+" if amount >= 0 else ""
    await interaction.response.send_message(
        f"✅ **{user.display_name}** {sign}{amount} {RATING_LABEL} → `{new_pts} {RATING_LABEL}` total."
    )


@tree.command(name="add_category", description="[MOD] Add a new category to the match list.")
@app_commands.describe(name="Category name", tier="Optional tier label (S/A/B/C)")
@has_mod_role()
async def add_category(interaction: discord.Interaction, name: str, tier: str = ""):
    conn = db_connect()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO categories (guild_id, name, tier) VALUES (?, ?, ?)", (str(interaction.guild_id), name, tier))
        conn.commit()
        await interaction.response.send_message(
            f"✅ **{name}** added to the {CATEGORY_LABEL} list (tier: `{tier or 'none'}`)."
        )
    except sqlite3.IntegrityError:
        await interaction.response.send_message(f"⚠️ **{name}** already exists.", ephemeral=True)
    finally:
        conn.close()


@tree.command(name="remove_category", description="[MOD] Remove a category from the match list.")
@app_commands.describe(name="The category name to remove")
@has_mod_role()
async def remove_category(interaction: discord.Interaction, name: str):
    conn = db_connect()
    c = conn.cursor()
    c.execute("DELETE FROM categories WHERE name=?", (name,))
    conn.commit()
    deleted = c.rowcount
    conn.close()
    if deleted:
        await interaction.response.send_message(f"✅ **{name}** removed.")
    else:
        await interaction.response.send_message(f"❌ Category `{name}` not found.", ephemeral=True)


@tree.command(name="clear", description="[MOD] Delete messages from this channel.")
@app_commands.describe(amount="Number of messages to delete (default 100)")
@has_mod_role()
async def clear(interaction: discord.Interaction, amount: int = 100):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✅ Deleted `{len(deleted)}` messages.", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TOURNAMENT SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@tree.command(name="tourney_open", description="[MOD] Open tournament registration.")
@has_mod_role()
async def tourney_open(interaction: discord.Interaction):
    global tournament_players, tournament_active
    tournament_players = []
    tournament_active  = True

    embed = discord.Embed(
        title=f"🛡️ {SERVER_NAME} — TOURNAMENT OPEN",
        description="Click the button below to register!\n\n**Participants:** 0",
        color=0x2ecc71
    )
    view   = discord.ui.View(timeout=None)
    button = discord.ui.Button(label="Join Tournament", style=discord.ButtonStyle.primary, emoji="⚔️")

    async def join_callback(btn_interaction: discord.Interaction):
        if btn_interaction.user in tournament_players:
            return await btn_interaction.response.send_message(
                "You're already registered!", ephemeral=True
            )
        tournament_players.append(btn_interaction.user)
        embed.description = (
            f"Click the button below to register!\n\n"
            f"**Participants: {len(tournament_players)}**\n"
            + ", ".join(p.display_name for p in tournament_players)
        )
        await btn_interaction.message.edit(embed=embed)
        await btn_interaction.response.send_message("✅ Registered!", ephemeral=True)

    button.callback = join_callback
    view.add_item(button)
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="tourney_start", description="[MOD] Seed players and generate the bracket.")
@has_mod_role()
async def tourney_start(interaction: discord.Interaction):
    global tournament_bracket, tournament_players
    if len(tournament_players) < 2:
        return await interaction.response.send_message(
            "❌ Need at least 2 players to start.", ephemeral=True
        )

    player_data = [(p, await get_or_create_user(str(interaction.guild_id), p.id, p.display_name)[2]) for p in tournament_players]
    player_data.sort(key=lambda x: x[1], reverse=True)
    seeded = [p for p, _ in player_data]

    bracket_size = 1 << (len(seeded) - 1).bit_length()
    while len(seeded) < bracket_size:
        seeded.append(None)

    tournament_bracket = []
    for i in range(bracket_size // 2):
        p1, p2 = seeded[i], seeded[-(i + 1)]
        match  = {"p1": p1, "p2": p2, "winner": None}
        if p2 is None: match["winner"] = p1
        if p1 is None: match["winner"] = p2
        tournament_bracket.append(match)

    embed = discord.Embed(title=f"🏟️ {SERVER_NAME} — BRACKET", color=0x3498db)
    match_str = ""
    for i, m in enumerate(tournament_bracket, 1):
        n1 = m["p1"].display_name if m["p1"] else "BYE"
        n2 = m["p2"].display_name if m["p2"] else "BYE"
        match_str += f"**Match {i}:** {n1} vs {n2}\n"
    embed.description = match_str
    pings = " ".join(p.mention for p in tournament_players if p)
    await interaction.response.send_message(content=pings, embed=embed)


@tree.command(name="tourney_add", description="[MOD] Manually add a player to the tournament roster.")
@app_commands.describe(user="The player to add")
@has_mod_role()
async def tourney_add(interaction: discord.Interaction, user: discord.Member):
    global tournament_players, tournament_active
    if not tournament_active:
        return await interaction.response.send_message(
            "❌ No tournament is open. Use `/tourney_open` first.", ephemeral=True
        )
    if user in tournament_players:
        return await interaction.response.send_message(
            f"⚠️ {user.display_name} is already on the roster.", ephemeral=True
        )
    tournament_players.append(user)
    await interaction.response.send_message(f"✅ **{user.display_name}** added to the roster.")


@tree.command(name="tourney_kick", description="[MOD] Remove a player from the tournament roster.")
@app_commands.describe(user="The player to remove")
@has_mod_role()
async def tourney_kick(interaction: discord.Interaction, user: discord.Member):
    global tournament_players
    if user in tournament_players:
        tournament_players.remove(user)
        await interaction.response.send_message(f"✅ **{user.display_name}** removed from the roster.")
    else:
        await interaction.response.send_message(
            f"❌ {user.display_name} isn't on the roster.", ephemeral=True
        )


@tree.command(name="tourney_list", description="View all players currently in the tournament.")
async def tourney_list(interaction: discord.Interaction):
    if not tournament_active:
        return await interaction.response.send_message(
            "No tournament is currently active.", ephemeral=True
        )
    if not tournament_players:
        return await interaction.response.send_message(
            "The tournament is open but no one has joined yet.", ephemeral=True
        )
    lines = "\n".join(f"• {p.display_name}" for p in tournament_players)
    embed = discord.Embed(title="📝 TOURNAMENT ROSTER", description=lines, color=0x3498db)
    embed.set_footer(text=SERVER_NAME)
    await interaction.response.send_message(embed=embed)


@tree.command(name="tourney_reward", description="[MOD] Award rating point prizes to top finishers.")
@app_commands.describe(
    first="1st place player",
    second="2nd place player",
    third="3rd place player (optional)"
)
@has_mod_role()
async def tourney_reward(
    interaction: discord.Interaction,
    first: discord.Member,
    second: discord.Member,
    third: discord.Member = None
):
    await interaction.response.defer()
    prizes  = CONFIG["tourney_prizes"]
    members = {first: prizes.get(1, 150), second: prizes.get(2, 75)}
    if third:
        members[third] = prizes.get(3, 30)

    summary = ""
    # Compute new totals first, then batch DB writes (faster + fewer locks)
    updates = []  # (member, new_pts, amt)
    for member, amt in members.items():
        data    = await get_or_create_user(str(interaction.guild_id), member.id, member.display_name)
        new_pts = data[3] + amt
        updates.append((member, new_pts, amt))

    async with DB_LOCK:
        conn = db_connect()
        c = conn.cursor()
        for member, new_pts, _amt in updates:
            c.execute("UPDATE users SET points=? WHERE guild_id=? AND user_id=?", (new_pts, str(interaction.guild_id), str(member.id)))
        conn.commit()
        conn.close()

    for member, new_pts, amt in updates:
        await update_player_role(member, new_pts)
        medal   = "🥇" if amt == prizes.get(1, 150) else "🥈" if amt == prizes.get(2, 75) else "🥉"
        summary += f"{medal} **{member.display_name}**: `+{amt} {RATING_LABEL}` → `{new_pts}` total\n"
    embed = discord.Embed(title="🎊 TOURNAMENT RESULTS", description=summary, color=0xf1c40f)
    await interaction.followup.send(embed=embed)
    await refresh_leaderboard(interaction.guild)


@tree.command(name="tourney_end", description="[MOD] Close the tournament and wipe all session data.")
@has_mod_role()
async def tourney_end(interaction: discord.Interaction):
    global tournament_players, tournament_active, tournament_bracket
    if not tournament_active:
        return await interaction.response.send_message(
            "There is no active tournament to end.", ephemeral=True
        )
    tournament_players = []
    tournament_bracket = []
    tournament_active  = False
    embed = discord.Embed(
        title="🏁 TOURNAMENT CONCLUDED",
        description="The session has ended and all registration data has been wiped.",
        color=0x95a5a6
    )
    await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN SLASH COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

@tree.command(name="setup", description="[ADMIN] Post the server welcome/intro embed.")
@has_admin()
async def setup(interaction: discord.Interaction):
    async with DB_LOCK:
        conn = db_connect()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM matches WHERE guild_id = ? AND status='completed'", (str(interaction.guild_id),))
        total_matches = c.fetchone()[0]
        conn.close()

    embed = discord.Embed(
        title=f"🏛️ {CONFIG['intro_title']}",
        description=CONFIG["intro_description"],
        color=0x2b2d31
    )
    rank_lines = "\n".join(
        f"{r['emoji']} **{r['name']}** — `{r['min']}+ {RATING_LABEL}`" for r in RANKS
    )
    embed.add_field(name="🏆 RANK LADDER",   value=rank_lines,           inline=False)
    embed.add_field(name="📜 RULES SUMMARY", value=CONFIG["rules_text"], inline=False)
    embed.add_field(
        name="⚔️ HOW TO PLAY",
        value=(f"• Use `/duel @user` to challenge someone.\n"
               f"• Select your {CATEGORY_LABEL} (if enabled) and report the winner."),
        inline=False
    )
    embed.set_footer(text=f"{SERVER_NAME} • {total_matches} matches recorded • Elo enabled")

    view = discord.ui.View()
    if CONFIG["leaderboard_url"]:
        view.add_item(discord.ui.Button(
            label="View Rankings", url=CONFIG["leaderboard_url"],
            style=discord.ButtonStyle.link, emoji="🌐"
        ))
    await interaction.response.send_message(embed=embed, view=view)


@tree.command(name="backup", description="[ADMIN] Receive a database backup in your DMs.")
@has_admin()
async def backup(interaction: discord.Interaction):
    if not os.path.exists(DB_NAME):
        return await interaction.response.send_message("❌ Database file not found.", ephemeral=True)
    try:
        await interaction.user.send(
            "📦 **Database Backup**",
            file=discord.File(DB_NAME, filename="matchmaking_backup.db")
        )
        await interaction.response.send_message("✅ Backup sent to your DMs.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Backup failed: `{e}`", ephemeral=True)


@tree.command(name="fix_database", description="[ADMIN] Patch missing database columns after upgrades.")
@has_admin()

async def fix_database(interaction: discord.Interaction):
    """[ADMIN] Migrate older single-server DB schema to per-guild schema (v5)."""
    guild_id = str(interaction.guild_id)

    async with DB_LOCK:
        conn = db_connect()
        c = conn.cursor()

        def has_column(table: str, col: str) -> bool:
            c.execute(f"PRAGMA table_info({table})")
            return any(r[1] == col for r in c.fetchall())

        try:
            # If already migrated, nothing to do.
            if has_column("users", "guild_id") and has_column("matches", "guild_id"):
                await interaction.response.send_message("✅ Database schema already supports per-server ladders.", ephemeral=True)
                conn.close()
                return

            # Build new tables
            c.execute("""CREATE TABLE IF NOT EXISTS users_new (
                guild_id TEXT,
                user_id  TEXT,
                name     TEXT,
                points   INTEGER,
                wins     INTEGER,
                losses   INTEGER,
                streak   INTEGER,
                history  TEXT,
                PRIMARY KEY (guild_id, user_id)
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS profiles_new (
                guild_id       TEXT,
                user_id        TEXT,
                title          TEXT DEFAULT 'Newcomer',
                signature_move TEXT DEFAULT 'None',
                embed_color    TEXT,
                PRIMARY KEY (guild_id, user_id)
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS categories_new (
                guild_id TEXT,
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT,
                tier     TEXT DEFAULT '',
                UNIQUE (guild_id, name)
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS matches_new (
                guild_id   TEXT,
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                p1_id      TEXT,
                p2_id      TEXT,
                p1_cat     TEXT,
                p2_cat     TEXT,
                winner_id  TEXT,
                status     TEXT DEFAULT 'active',
                notes      TEXT DEFAULT '',
                channel_id INTEGER,
                message_id INTEGER,
                timestamp  DATETIME DEFAULT CURRENT_TIMESTAMP
            )""")

            c.execute("""CREATE TABLE IF NOT EXISTS config_new (
                guild_id TEXT,
                key      TEXT,
                value    TEXT,
                PRIMARY KEY (guild_id, key)
            )""")

            # Copy data forward (scope legacy data to *this* guild)
            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone():
                c.execute("SELECT user_id, name, points, wins, losses, streak, history FROM users")
                for (uid, name, pts, wins, losses, streak, hist) in c.fetchall():
                    c.execute(
                        "INSERT OR REPLACE INTO users_new (guild_id, user_id, name, points, wins, losses, streak, history) VALUES (?,?,?,?,?,?,?,?)",
                        (guild_id, str(uid), name, int(pts or 0), int(wins or 0), int(losses or 0), int(streak or 0), hist or ""),
                    )

            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='profiles'").fetchone():
                c.execute("SELECT user_id, title, signature_move, embed_color FROM profiles")
                for (uid, title, move, color) in c.fetchall():
                    c.execute(
                        "INSERT OR REPLACE INTO profiles_new (guild_id, user_id, title, signature_move, embed_color) VALUES (?,?,?,?,?)",
                        (guild_id, str(uid), title or "Newcomer", move or "None", color),
                    )

            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='categories'").fetchone():
                c.execute("SELECT name, tier FROM categories")
                for (name, tier) in c.fetchall():
                    c.execute(
                        "INSERT OR IGNORE INTO categories_new (guild_id, name, tier) VALUES (?,?,?)",
                        (guild_id, name, tier or ""),
                    )

            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='matches'").fetchone():
                # Old matches schema: (id, p1_id, p2_id, p1_cat, p2_cat, winner_id, status, notes, timestamp)
                c.execute("PRAGMA table_info(matches)")
                cols = [r[1] for r in c.fetchall()]
                if "p1_cat" in cols:
                    c.execute("SELECT id, p1_id, p2_id, p1_cat, p2_cat, winner_id, status, notes, timestamp FROM matches")
                    for row in c.fetchall():
                        (mid, p1, p2, p1c, p2c, win, status, notes, ts) = row
                        c.execute(
                            "INSERT INTO matches_new (guild_id, id, p1_id, p2_id, p1_cat, p2_cat, winner_id, status, notes, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (guild_id, mid, str(p1), str(p2), p1c, p2c, win, status, notes, ts),
                        )
                else:
                    c.execute("SELECT id, p1_id, p2_id, winner_id, status, notes, timestamp FROM matches")
                    for row in c.fetchall():
                        (mid, p1, p2, win, status, notes, ts) = row
                        c.execute(
                            "INSERT INTO matches_new (guild_id, id, p1_id, p2_id, winner_id, status, notes, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                            (guild_id, mid, str(p1), str(p2), win, status, notes, ts),
                        )

            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='config'").fetchone():
                c.execute("SELECT key, value FROM config")
                for (k, v) in c.fetchall():
                    c.execute(
                        "INSERT OR REPLACE INTO config_new (guild_id, key, value) VALUES (?,?,?)",
                        (guild_id, k, v),
                    )

            # Swap tables
            for t in ["users", "profiles", "categories", "matches", "config"]:
                c.execute(f"DROP TABLE IF EXISTS {t}")
            c.execute("ALTER TABLE users_new RENAME TO users")
            c.execute("ALTER TABLE profiles_new RENAME TO profiles")
            c.execute("ALTER TABLE categories_new RENAME TO categories")
            c.execute("ALTER TABLE matches_new RENAME TO matches")
            c.execute("ALTER TABLE config_new RENAME TO config")

            conn.commit()
            await interaction.response.send_message(
                "✅ Database migrated to per-server ladders. Legacy data was assigned to this server.",
                ephemeral=True,
            )
        except Exception as e:
            conn.rollback()
            try:
                await interaction.response.send_message(f"❌ Migration failed: `{e}`", ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(f"❌ Migration failed: `{e}`", ephemeral=True)
        finally:
            conn.close()



@tree.command(name="sync", description="[ADMIN] Re-sync all slash commands with Discord.")
@has_admin()
async def sync_commands(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    try:
        synced = await tree.sync()
        await interaction.followup.send(
            f"✅ Synced **{len(synced)}** slash commands globally.", ephemeral=True
        )
    except Exception as e:
        await interaction.followup.send(f"❌ Sync failed: `{e}`", ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
#  FLASK  (keeps the process alive on Railway / Render)
# ══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
CORS(app)


@app.route("/")
def home():
    return f"{SERVER_NAME} Matchmaking Bot — Online"


@app.route("/api/leaderboard")
def api_leaderboard():
    conn = db_connect()
    c = conn.cursor()
    c.execute("SELECT name, points, wins, losses, streak FROM users WHERE guild_id = ? ORDER BY points DESC LIMIT 50", (str(guild_id),))
    data = [
        {"name": r[0], "points": r[1], "wins": r[2], "losses": r[3], "streak": r[4]}
        for r in c.fetchall()
    ]
    conn.close()
    return jsonify(data)


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    init_db()
    keep_alive()
    bot.run(TOKEN)