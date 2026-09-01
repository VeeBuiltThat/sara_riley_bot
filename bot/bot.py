from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
import aiohttp
from discord import app_commands

from .config import Config
from .database import Store

logger = logging.getLogger(__name__)


def _discord_snowflake_time(snowflake_id: str) -> datetime:
    snowflake = int(snowflake_id)
    ms = (snowflake >> 22) + 1420070400000
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _mention_or_dash(user_id: str) -> str:
    return f"<@{user_id}>" if user_id else "—"


def _channel_mention(channel_id: str) -> str:
    return f"<#{channel_id}>" if channel_id else "—"


def _truncate(v: str, n: int) -> str:
    v = v.strip()
    if not v:
        return "(empty)"
    return v if len(v) <= n else v[: n - 1] + "…"


def _success_embed(description: str) -> discord.Embed:
    return discord.Embed(color=0x57F287, description=description)


def _error_embed(description: str) -> discord.Embed:
    return discord.Embed(color=0xED4245, description=description)


def _info_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(color=0x5865F2, title=title, description=description)


_DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
_INVITE_RE = re.compile(r"discord(?:\.gg|app\.com/invite|\.com/invite)/[a-zA-Z0-9-]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_DISCORD_ID_RE = re.compile(r"\d{15,22}")
_EMOJI_RE = re.compile(r"[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]")


def _parse_duration(s: str) -> Optional[timedelta]:
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    matches = _DURATION_RE.findall(s)
    if not matches:
        return None
    total = sum(int(n) * units[u.lower()] for n, u in matches)
    return timedelta(seconds=total)


def _format_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total <= 0:
        return "0s"
    parts = []
    for unit, secs in [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]:
        v, total = divmod(total, secs)
        if v:
            parts.append(f"{v}{unit}")
    return " ".join(parts)


def _parse_discord_ids(value: str) -> set[str]:
    return {token for token in re.split(r"[\s,]+", value.strip()) if _DISCORD_ID_RE.fullmatch(token)}


_8BALL = [
    "It is certain.", "It is decidedly so.", "Without a doubt.", "Yes, definitely.",
    "You may rely on it.", "As I see it, yes.", "Most likely.", "Outlook good.",
    "Yes.", "Signs point to yes.", "Reply hazy, try again.", "Ask again later.",
    "Better not tell you now.", "Cannot predict now.", "Concentrate and ask again.",
    "Don't count on it.", "My reply is no.", "My sources say no.",
    "Outlook not so good.", "Very doubtful.",
]

_TOPICS = [
    "What's the most useless skill you have?",
    "What song would you pick as your theme music?",
    "If you could only eat one food forever, what would it be?",
    "What's something you're irrationally afraid of?",
    "Would you rather fight one horse-sized duck or 100 duck-sized horses?",
    "What's the worst movie you actually enjoyed?",
    "If you were a vegetable, what would you be and why?",
    "What animal would make the best world leader?",
    "If time travel were possible, would you go forwards or backwards?",
]


class ModerationBot(discord.Client):
    def __init__(self, config: Config, store: Store) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guild_messages = True
        super().__init__(intents=intents)
        self.config = config
        self.store = store
        self.tree = app_commands.CommandTree(self)
        self._guild = discord.Object(id=int(config.guild_id)) if config.guild_id else None
        self._spam_tracker: dict[str, list[float]] = defaultdict(list)
        self._ai_user_cooldowns: dict[str, float] = {}
        self._ai_channel_cooldowns: dict[str, float] = {}
        self._register_commands()

    async def setup_hook(self) -> None:
        if self._guild:
            self.tree.copy_global_to(guild=self._guild)
            await self.tree.sync(guild=self._guild)
        else:
            await self.tree.sync()
        logger.info("Slash commands synced")
        self._reminder_task = asyncio.create_task(self._reminder_loop())

    async def on_ready(self) -> None:
        logger.info("Discord bot connected: %s", self.user)
        for guild in self.guilds:
            await self._get_settings(guild.id)
            await self.store.welcome_config(str(guild.id))
            await self.store.automod_config(str(guild.id))
            await self.store.ai_chat_config(str(guild.id))
            await self.store.ticket_config(str(guild.id))

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._get_settings(guild.id)
        await self.store.welcome_config(str(guild.id))
        await self.store.automod_config(str(guild.id))
        await self.store.ai_chat_config(str(guild.id))
        await self.store.ticket_config(str(guild.id))
        logger.info("Joined guild: %s (%s)", guild.name, guild.id)

    # ------------------------------------------------------------------ #
    # Slash commands                                                       #
    # ------------------------------------------------------------------ #

    def _register_commands(self) -> None:
        guild = self._guild

        @self.tree.command(name="kick", description="Kick a member", guild=guild)
        @app_commands.default_permissions(kick_members=True)
        @app_commands.describe(user="Member to kick", reason="Reason for the moderation action")
        async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided") -> None:
            reason = reason[:500]
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            try:
                await interaction.guild.kick(user, reason=reason)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Kick failed: {e}"), ephemeral=True)
                return
            await self._mod_log(interaction.guild_id, "kick", str(interaction.user.id), str(user.id), str(interaction.channel_id), reason)
            await interaction.response.send_message(embed=_success_embed(f"Kicked {user.mention}.\n**Reason:** {reason}"), ephemeral=True)

        @self.tree.command(name="ban", description="Ban a member", guild=guild)
        @app_commands.default_permissions(ban_members=True)
        @app_commands.describe(user="Member to ban", reason="Reason for the moderation action")
        async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "No reason provided") -> None:
            reason = reason[:500]
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            try:
                await interaction.guild.ban(user, reason=reason, delete_message_days=0)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Ban failed: {e}"), ephemeral=True)
                return
            await self._mod_log(interaction.guild_id, "ban", str(interaction.user.id), str(user.id), str(interaction.channel_id), reason)
            await interaction.response.send_message(embed=_success_embed(f"Banned {user.mention}.\n**Reason:** {reason}"), ephemeral=True)

        @self.tree.command(name="warn", description="Warn a member", guild=guild)
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(user="Member to warn", reason="Reason for the warning")
        async def warn(interaction: discord.Interaction, user: discord.Member, reason: str) -> None:
            reason = reason[:500]
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            warning_id = await self.store.add_warning(str(interaction.guild_id), str(user.id), str(interaction.user.id), reason)
            await self._mod_log(interaction.guild_id, "warning", str(interaction.user.id), str(user.id), str(interaction.channel_id), f"#{warning_id}: {reason}")
            if settings.dm_warnings:
                try:
                    dm = await user.create_dm()
                    guild_name = interaction.guild.name if interaction.guild else "this server"
                    await dm.send(embed=discord.Embed(
                        color=0xFEE75C,
                        title="You received a warning",
                        description=f"**Server:** {guild_name}\n**Reason:** {reason}",
                    ))
                except discord.HTTPException:
                    pass
            await interaction.response.send_message(embed=_success_embed(f"Warning #{warning_id} issued to {user.mention}."), ephemeral=True)

        @self.tree.command(name="warnings", description="Show a member's warnings", guild=guild)
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(user="Member to inspect")
        async def warnings(interaction: discord.Interaction, user: discord.Member) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            ws = await self.store.warnings(str(interaction.guild_id), str(user.id))
            if not ws:
                await interaction.response.send_message(embed=_info_embed("Warnings", f"No warnings found for {user.mention}."), ephemeral=True)
                return
            lines = [
                f"`#{w.id}` {w.created_at.strftime('%Y-%m-%d')} — <@{w.moderator_id}> — {w.reason}"
                for w in ws[:10]
            ]
            await interaction.response.send_message(
                embed=_info_embed(f"Warnings for {user} ({len(ws)} total)", "\n".join(lines)),
                ephemeral=True,
            )

        @self.tree.command(name="userinfo", description="Show information about a member", guild=guild)
        @app_commands.describe(user="Member to inspect")
        async def userinfo(interaction: discord.Interaction, user: discord.Member) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            joined = user.joined_at.strftime("%a, %d %b %Y %H:%M:%S %Z") if user.joined_at else "Unknown"
            created = _discord_snowflake_time(str(user.id)).strftime("%a, %d %b %Y %H:%M:%S UTC")
            roles = " ".join(f"<@&{r.id}>" for r in user.roles[1:]) or "None"  # skip @everyone
            embed = discord.Embed(color=0x5865F2, title=str(user))
            embed.set_thumbnail(url=user.display_avatar.url)
            embed.add_field(name="ID", value=str(user.id), inline=True)
            embed.add_field(name="Created", value=created, inline=True)
            embed.add_field(name="Joined", value=joined, inline=True)
            embed.add_field(name="Roles", value=roles)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(name="lock", description="Lock the current channel", guild=guild)
        @app_commands.default_permissions(manage_channels=True)
        @app_commands.describe(reason="Reason for the moderation action")
        async def lock(interaction: discord.Interaction, reason: str = "Channel lockdown") -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(embed=_error_embed("This command can only be used in text channels."), ephemeral=True)
                return
            everyone = interaction.guild.default_role
            existing = channel.overwrites.get(everyone)
            if existing:
                allow_p, deny_p = existing.pair()
                allow_bits, deny_bits = allow_p.value, deny_p.value
                had_overwrite = True
            else:
                allow_bits, deny_bits = 0, 0
                had_overwrite = False
            await self.store.save_channel_lock(str(interaction.guild_id), str(channel.id), had_overwrite, allow_bits, deny_bits)
            overwrite = existing or discord.PermissionOverwrite()
            overwrite.send_messages = False
            try:
                await channel.set_permissions(everyone, overwrite=overwrite, reason=reason)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Lock failed: {e}"), ephemeral=True)
                return
            await self._mod_log(interaction.guild_id, "channel_lock", str(interaction.user.id), "", str(channel.id), reason)
            await interaction.response.send_message(embed=_success_embed("Channel locked. Previous permissions were snapshotted for restoration."), ephemeral=True)

        @self.tree.command(name="unlock", description="Unlock the current channel", guild=guild)
        @app_commands.default_permissions(manage_channels=True)
        async def unlock(interaction: discord.Interaction) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                await interaction.response.send_message(embed=_error_embed("This command can only be used in text channels."), ephemeral=True)
                return
            snapshot = await self.store.channel_lock(str(interaction.guild_id), str(channel.id))
            if snapshot is None:
                await interaction.response.send_message(embed=_error_embed("No lockdown snapshot exists for this channel."), ephemeral=True)
                return
            had, allow_bits, deny_bits = snapshot
            everyone = interaction.guild.default_role
            try:
                if had:
                    overwrite = discord.PermissionOverwrite.from_pair(
                        discord.Permissions(allow_bits), discord.Permissions(deny_bits)
                    )
                    await channel.set_permissions(everyone, overwrite=overwrite)
                else:
                    await channel.set_permissions(everyone, overwrite=None)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Unlock failed: {e}"), ephemeral=True)
                return
            await self.store.delete_channel_lock(str(interaction.guild_id), str(channel.id))
            await self._mod_log(interaction.guild_id, "channel_unlock", str(interaction.user.id), "", str(channel.id), "Channel permissions restored")
            await interaction.response.send_message(embed=_success_embed("Channel unlocked and previous permissions restored."), ephemeral=True)

        # ---- Moderation (extended) ---- #

        @self.tree.command(name="unban", description="Unban a user by ID", guild=guild)
        @app_commands.default_permissions(ban_members=True)
        @app_commands.describe(user_id="Discord user ID to unban", reason="Reason")
        async def unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason provided") -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            try:
                uid = int(user_id.strip())
            except ValueError:
                await interaction.response.send_message(embed=_error_embed("Invalid user ID."), ephemeral=True)
                return
            try:
                await interaction.guild.unban(discord.Object(id=uid), reason=reason)
            except discord.NotFound:
                await interaction.response.send_message(embed=_error_embed("That user is not banned."), ephemeral=True)
                return
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Unban failed: {e}"), ephemeral=True)
                return
            await self._mod_log(interaction.guild_id, "unban", str(interaction.user.id), str(uid), str(interaction.channel_id), reason)
            await interaction.response.send_message(embed=_success_embed(f"Unbanned `{uid}`.\n**Reason:** {reason}"), ephemeral=True)

        @self.tree.command(name="timeout", description="Timeout a member", guild=guild)
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(user="Member to timeout", duration="e.g. 10m, 1h, 1d (max 28d)", reason="Reason")
        async def timeout_cmd(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str = "No reason provided") -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            td = _parse_duration(duration)
            if not td or td.total_seconds() <= 0:
                await interaction.response.send_message(embed=_error_embed("Invalid duration. Examples: `10m`, `1h`, `1d`"), ephemeral=True)
                return
            if td.total_seconds() > 60 * 60 * 24 * 28:
                await interaction.response.send_message(embed=_error_embed("Maximum timeout is 28 days."), ephemeral=True)
                return
            try:
                await user.timeout(discord.utils.utcnow() + td, reason=reason)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Timeout failed: {e}"), ephemeral=True)
                return
            await self._mod_log(interaction.guild_id, "timeout", str(interaction.user.id), str(user.id), str(interaction.channel_id), f"{_format_duration(td)}: {reason}")
            await interaction.response.send_message(embed=_success_embed(f"Timed out {user.mention} for **{_format_duration(td)}**.\n**Reason:** {reason}"), ephemeral=True)

        @self.tree.command(name="removetimeout", description="Remove a member's timeout", guild=guild)
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(user="Member to un-timeout", reason="Reason")
        async def removetimeout(interaction: discord.Interaction, user: discord.Member, reason: str = "Timeout removed") -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            try:
                await user.timeout(None, reason=reason)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            await self._mod_log(interaction.guild_id, "removetimeout", str(interaction.user.id), str(user.id), str(interaction.channel_id), reason)
            await interaction.response.send_message(embed=_success_embed(f"Removed timeout from {user.mention}."), ephemeral=True)

        @self.tree.command(name="delwarn", description="Delete a warning by ID", guild=guild)
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(warning_id="Warning ID shown in /warnings")
        async def delwarn(interaction: discord.Interaction, warning_id: int) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            ok = await self.store.del_warning(warning_id, str(interaction.guild_id))
            if ok:
                await interaction.response.send_message(embed=_success_embed(f"Warning `#{warning_id}` deleted."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=_error_embed("Warning not found."), ephemeral=True)

        @self.tree.command(name="clearwarns", description="Clear all warnings for a member", guild=guild)
        @app_commands.default_permissions(moderate_members=True)
        @app_commands.describe(user="Member whose warnings to clear")
        async def clearwarns(interaction: discord.Interaction, user: discord.Member) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            count = await self.store.clear_warnings(str(interaction.guild_id), str(user.id))
            await interaction.response.send_message(embed=_success_embed(f"Cleared **{count}** warning(s) for {user.mention}."), ephemeral=True)

        @self.tree.command(name="purge", description="Bulk delete messages (max 100)", guild=guild)
        @app_commands.default_permissions(manage_messages=True)
        @app_commands.describe(count="Number of messages to delete", user="Only delete messages from this user")
        async def purge(interaction: discord.Interaction, count: int, user: Optional[discord.Member] = None) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            count = max(1, min(100, count))
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message(embed=_error_embed("Can only purge in text channels."), ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            check = (lambda m: m.author == user) if user else None
            try:
                deleted = await interaction.channel.purge(limit=count, check=check)
            except discord.HTTPException as e:
                await interaction.followup.send(embed=_error_embed(f"Purge failed: {e}"), ephemeral=True)
                return
            label = f" from {user.mention}" if user else ""
            await self._mod_log(interaction.guild_id, "purge", str(interaction.user.id), str(user.id) if user else "", str(interaction.channel_id), f"Deleted {len(deleted)} messages{label}")
            await interaction.followup.send(embed=_success_embed(f"Deleted **{len(deleted)}** message(s){label}."), ephemeral=True)

        @self.tree.command(name="slowmode", description="Set channel slowmode (0 = off)", guild=guild)
        @app_commands.default_permissions(manage_channels=True)
        @app_commands.describe(seconds="Seconds between messages (0–21600)")
        async def slowmode(interaction: discord.Interaction, seconds: int = 0) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            seconds = max(0, min(21600, seconds))
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.response.send_message(embed=_error_embed("Text channels only."), ephemeral=True)
                return
            try:
                await interaction.channel.edit(slowmode_delay=seconds)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            msg = "Slowmode disabled." if seconds == 0 else f"Slowmode set to **{seconds}s**."
            await interaction.response.send_message(embed=_success_embed(msg), ephemeral=True)

        @self.tree.command(name="giverole", description="Give a role to a member", guild=guild)
        @app_commands.default_permissions(manage_roles=True)
        @app_commands.describe(user="Target member", role="Role to assign")
        async def giverole(interaction: discord.Interaction, user: discord.Member, role: discord.Role) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            try:
                await user.add_roles(role, reason=f"Given by {interaction.user}")
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            await interaction.response.send_message(embed=_success_embed(f"Gave {role.mention} to {user.mention}."), ephemeral=True)

        @self.tree.command(name="takerole", description="Remove a role from a member", guild=guild)
        @app_commands.default_permissions(manage_roles=True)
        @app_commands.describe(user="Target member", role="Role to remove")
        async def takerole(interaction: discord.Interaction, user: discord.Member, role: discord.Role) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            try:
                await user.remove_roles(role, reason=f"Removed by {interaction.user}")
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            await interaction.response.send_message(embed=_success_embed(f"Removed {role.mention} from {user.mention}."), ephemeral=True)

        # ---- Utility ---- #

        @self.tree.command(name="ping", description="Check bot latency", guild=guild)
        async def ping(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(embed=_info_embed("🏓 Pong!", f"Latency: **{round(self.latency * 1000)}ms**"))

        @self.tree.command(name="serverinfo", description="Show server information", guild=guild)
        async def serverinfo(interaction: discord.Interaction) -> None:
            g = interaction.guild
            embed = discord.Embed(color=0x5865F2, title=g.name)
            if g.icon:
                embed.set_thumbnail(url=g.icon.url)
            embed.add_field(name="Owner", value=f"<@{g.owner_id}>", inline=True)
            embed.add_field(name="Created", value=g.created_at.strftime("%d %b %Y"), inline=True)
            embed.add_field(name="Members", value=str(g.member_count), inline=True)
            embed.add_field(name="Channels", value=str(len(g.channels)), inline=True)
            embed.add_field(name="Roles", value=str(len(g.roles)), inline=True)
            embed.add_field(name="Boost level", value=str(g.premium_tier), inline=True)
            embed.set_footer(text=f"ID: {g.id}")
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="avatar", description="Show a user's avatar", guild=guild)
        @app_commands.describe(user="User (defaults to yourself)")
        async def avatar(interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
            target = user or interaction.user
            embed = discord.Embed(color=0x5865F2, title=f"{target}'s avatar")
            embed.set_image(url=target.display_avatar.url)
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="say", description="Send a message as the bot", guild=guild)
        @app_commands.default_permissions(manage_messages=True)
        @app_commands.describe(message="Text to send", channel="Target channel (default: current)")
        async def say(interaction: discord.Interaction, message: str, channel: Optional[discord.TextChannel] = None) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            target = channel or interaction.channel
            try:
                await target.send(message[:2000])
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            await interaction.response.send_message(embed=_success_embed(f"Sent to {target.mention}."), ephemeral=True)

        @self.tree.command(name="embed", description="Send a custom embed", guild=guild)
        @app_commands.default_permissions(manage_messages=True)
        @app_commands.describe(title="Title", description="Body text", color="Hex color e.g. #5865F2", channel="Target channel")
        async def embed_cmd(interaction: discord.Interaction, title: str = "", description: str = "", color: str = "#5865F2", channel: Optional[discord.TextChannel] = None) -> None:
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("You don't have permission to use this command."), ephemeral=True)
                return
            try:
                color_int = int(color.lstrip("#"), 16)
            except ValueError:
                color_int = 0x5865F2
            emb = discord.Embed(title=title[:256] or None, description=description[:4096] or None, color=color_int)
            target = channel or interaction.channel
            try:
                await target.send(embed=emb)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            await interaction.response.send_message(embed=_success_embed(f"Embed sent to {target.mention}."), ephemeral=True)

        @self.tree.command(name="poll", description="Create a reaction poll", guild=guild)
        @app_commands.describe(question="Poll question", options="Comma-separated options (2–9)")
        async def poll(interaction: discord.Interaction, question: str, options: str) -> None:
            choices = [o.strip() for o in options.split(",") if o.strip()][:9]
            if len(choices) < 2:
                await interaction.response.send_message(embed=_error_embed("Provide at least 2 comma-separated options."), ephemeral=True)
                return
            emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣"]
            lines = "\n".join(f"{emojis[i]} {c}" for i, c in enumerate(choices))
            embed = discord.Embed(color=0xFEE75C, title=f"📊 {question[:250]}", description=lines)
            embed.set_footer(text=f"Poll by {interaction.user}")
            await interaction.response.send_message(embed=embed)
            msg = await interaction.original_response()
            for i in range(len(choices)):
                await msg.add_reaction(emojis[i])

        # ---- Tickets ---- #

        @self.tree.command(name="ticket", description="Open a support ticket", guild=guild)
        @app_commands.describe(subject="What do you need help with?")
        async def ticket(interaction: discord.Interaction, subject: str) -> None:
            await interaction.response.defer(ephemeral=True)
            tc = await self.store.ticket_config(str(interaction.guild_id))
            g = interaction.guild
            overwrites = {
                g.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                g.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
            }
            if tc.support_role_id:
                try:
                    role = g.get_role(int(tc.support_role_id))
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
                except (ValueError, TypeError):
                    pass
            category = g.get_channel(int(tc.category_id)) if tc.category_id else None
            safe = re.sub(r"[^a-z0-9-]", "", subject.lower().replace(" ", "-"))[:40]
            chan_name = f"ticket-{safe or str(interaction.user.id)[:8]}"
            try:
                ch = await g.create_text_channel(
                    chan_name, overwrites=overwrites,
                    category=category, topic=subject[:1024],
                    reason=f"Ticket by {interaction.user}",
                )
            except discord.HTTPException as e:
                await interaction.followup.send(embed=_error_embed(f"Could not create channel: {e}"), ephemeral=True)
                return
            tid = await self.store.create_ticket(str(g.id), str(ch.id), str(interaction.user.id), subject[:500])
            await ch.send(
                content=interaction.user.mention,
                embed=discord.Embed(
                    color=0x57F287,
                    title=f"Ticket #{tid}: {subject[:100]}",
                    description=tc.welcome_message,
                ).set_footer(text="Use /ticketclose to close this ticket."),
            )
            if tc.log_channel_id:
                try:
                    lc = g.get_channel(int(tc.log_channel_id))
                    if lc:
                        await lc.send(embed=discord.Embed(color=0x5865F2, title=f"Ticket #{tid} opened", description=f"**User:** {interaction.user.mention}\n**Subject:** {subject[:200]}"))
                except Exception:
                    pass
            await interaction.followup.send(embed=_success_embed(f"Ticket created: {ch.mention}"), ephemeral=True)

        @self.tree.command(name="ticketclose", description="Close the current ticket", guild=guild)
        @app_commands.describe(reason="Reason for closing")
        async def ticketclose(interaction: discord.Interaction, reason: str = "Resolved") -> None:
            t = await self.store.ticket_by_channel(str(interaction.guild_id), str(interaction.channel_id))
            if t is None:
                await interaction.response.send_message(embed=_error_embed("This is not a ticket channel."), ephemeral=True)
                return
            settings = await self._get_settings(interaction.guild_id)
            is_creator = str(interaction.user.id) == t.creator_id
            is_staff = isinstance(interaction.user, discord.Member) and self._has_bot_permission(interaction.user, settings)
            if not is_creator and not is_staff:
                await interaction.response.send_message(embed=_error_embed("Only the ticket creator or staff can close this."), ephemeral=True)
                return
            await self.store.close_ticket(str(interaction.guild_id), str(interaction.channel_id))
            await interaction.response.send_message(embed=_info_embed("Ticket closed", f"**Reason:** {reason}\nChannel deletes in 5 seconds."))
            await asyncio.sleep(5)
            try:
                await interaction.channel.delete(reason=f"Ticket closed: {reason}")
            except discord.HTTPException:
                pass

        @self.tree.command(name="ticketadd", description="Add a user to this ticket", guild=guild)
        @app_commands.describe(user="User to add")
        async def ticketadd(interaction: discord.Interaction, user: discord.Member) -> None:
            if await self.store.ticket_by_channel(str(interaction.guild_id), str(interaction.channel_id)) is None:
                await interaction.response.send_message(embed=_error_embed("This is not a ticket channel."), ephemeral=True)
                return
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("Staff only."), ephemeral=True)
                return
            try:
                await interaction.channel.set_permissions(user, view_channel=True, send_messages=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            await interaction.response.send_message(embed=_success_embed(f"Added {user.mention} to this ticket."))

        @self.tree.command(name="ticketremove", description="Remove a user from this ticket", guild=guild)
        @app_commands.describe(user="User to remove")
        async def ticketremove(interaction: discord.Interaction, user: discord.Member) -> None:
            if await self.store.ticket_by_channel(str(interaction.guild_id), str(interaction.channel_id)) is None:
                await interaction.response.send_message(embed=_error_embed("This is not a ticket channel."), ephemeral=True)
                return
            settings = await self._get_settings(interaction.guild_id)
            if not isinstance(interaction.user, discord.Member) or not self._has_bot_permission(interaction.user, settings):
                await interaction.response.send_message(embed=_error_embed("Staff only."), ephemeral=True)
                return
            try:
                await interaction.channel.set_permissions(user, overwrite=None)
            except discord.HTTPException as e:
                await interaction.response.send_message(embed=_error_embed(f"Failed: {e}"), ephemeral=True)
                return
            await interaction.response.send_message(embed=_success_embed(f"Removed {user.mention} from this ticket."))

        # ---- Reminders ---- #

        @self.tree.command(name="remind", description="Set a reminder", guild=guild)
        @app_commands.describe(duration="When e.g. 10m, 2h, 1d", message="What to remind you about")
        async def remind(interaction: discord.Interaction, duration: str, message: str) -> None:
            td = _parse_duration(duration)
            if not td or td.total_seconds() <= 0:
                await interaction.response.send_message(embed=_error_embed("Invalid duration. Examples: `10m`, `2h`, `1d`"), ephemeral=True)
                return
            if td.total_seconds() > 86400 * 365:
                await interaction.response.send_message(embed=_error_embed("Maximum reminder duration is 1 year."), ephemeral=True)
                return
            due = datetime.now(tz=timezone.utc) + td
            rid = await self.store.add_reminder(str(interaction.guild_id), str(interaction.user.id), str(interaction.channel_id), message[:500], due)
            await interaction.response.send_message(
                embed=_success_embed(f"Reminder `#{rid}` set for **{_format_duration(td)}** from now.\n> {message[:200]}"),
                ephemeral=True,
            )

        @self.tree.command(name="reminders", description="List your active reminders", guild=guild)
        async def reminders_cmd(interaction: discord.Interaction) -> None:
            rs = await self.store.user_reminders(str(interaction.guild_id), str(interaction.user.id))
            if not rs:
                await interaction.response.send_message(embed=_info_embed("Your reminders", "No active reminders."), ephemeral=True)
                return
            lines = [f"`#{r.id}` <t:{int(r.due_at.timestamp())}:R> — {r.message[:60]}" for r in rs]
            await interaction.response.send_message(embed=_info_embed(f"Your reminders ({len(rs)})", "\n".join(lines)), ephemeral=True)

        @self.tree.command(name="delremind", description="Delete one of your reminders", guild=guild)
        @app_commands.describe(reminder_id="ID from /reminders")
        async def delremind(interaction: discord.Interaction, reminder_id: int) -> None:
            ok = await self.store.delete_reminder(reminder_id, str(interaction.user.id))
            if ok:
                await interaction.response.send_message(embed=_success_embed(f"Reminder `#{reminder_id}` deleted."), ephemeral=True)
            else:
                await interaction.response.send_message(embed=_error_embed("Reminder not found or not yours."), ephemeral=True)

        # ---- Fun ---- #

        @self.tree.command(name="8ball", description="Ask the magic 8-ball", guild=guild)
        @app_commands.describe(question="Your question")
        async def eightball(interaction: discord.Interaction, question: str) -> None:
            embed = discord.Embed(color=0x5865F2, title="🎱 Magic 8-Ball")
            embed.add_field(name="Question", value=question[:200], inline=False)
            embed.add_field(name="Answer", value=random.choice(_8BALL), inline=False)
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="roll", description="Roll dice", guild=guild)
        @app_commands.describe(dice="Dice notation e.g. 2d6, or max number e.g. 100")
        async def roll(interaction: discord.Interaction, dice: str = "6") -> None:
            rpg = re.fullmatch(r"(\d+)d(\d+)", dice.strip(), re.IGNORECASE)
            if rpg:
                num = max(1, min(20, int(rpg.group(1))))
                sides = max(2, min(1_000_000, int(rpg.group(2))))
                rolls = [random.randint(1, sides) for _ in range(num)]
                label = ", ".join(map(str, rolls)) if num <= 10 else f"{num} rolls"
                embed = discord.Embed(color=0x5865F2, title=f"🎲 {dice}", description=f"{label}\n**Total: {sum(rolls)}**")
            else:
                try:
                    n = max(2, min(1_000_000, int(dice.strip())))
                    embed = discord.Embed(color=0x5865F2, title=f"🎲 1–{n}", description=f"**{random.randint(1, n)}**")
                except ValueError:
                    embed = _error_embed("Use a number or dice notation like `2d6`.")
            await interaction.response.send_message(embed=embed)

        @self.tree.command(name="coinflip", description="Flip a coin", guild=guild)
        async def coinflip(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(embed=discord.Embed(color=0xFEE75C, description=f"🪙 **{random.choice(['Heads', 'Tails'])}!**"))

        @self.tree.command(name="choose", description="Pick one option at random", guild=guild)
        @app_commands.describe(options="Comma-separated choices")
        async def choose(interaction: discord.Interaction, options: str) -> None:
            choices = [o.strip() for o in options.split(",") if o.strip()]
            if len(choices) < 2:
                await interaction.response.send_message(embed=_error_embed("Provide at least 2 comma-separated options."), ephemeral=True)
                return
            await interaction.response.send_message(embed=discord.Embed(color=0x5865F2, description=f"🎯 I choose: **{random.choice(choices)}**"))

        @self.tree.command(name="topic", description="Get a random conversation topic", guild=guild)
        async def topic(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(embed=discord.Embed(color=0x5865F2, description=f"💬 {random.choice(_TOPICS)}"))



    async def _get_settings(self, guild_id):
        guild = self.get_guild(int(guild_id))
        return await self.store.settings(
            str(guild_id),
            guild.name if guild else "",
            self.config.default_log_channel_id,
            self.config.default_owner_role_id,
            self.config.default_admin_role_id,
            self.config.default_mod_role_id,
        )

    def _has_bot_permission(self, member: discord.Member, settings) -> bool:
        role_ids = {r.id for r in member.roles}
        for role_id_str, need_toggle in (
            (settings.owner_role_id, False),
            (settings.admin_role_id, False),
            (settings.mod_role_id, True),
        ):
            if not role_id_str:
                continue
            try:
                if int(role_id_str) in role_ids:
                    if need_toggle and not settings.mod_commands_enabled:
                        continue
                    return True
            except ValueError:
                pass
        return False

    async def _mod_log(self, guild_id: int, event: str, actor: str, target: str, channel: str, details: str) -> None:
        await self.store.audit(str(guild_id), event, actor, target, channel, details)
        try:
            settings = await self._get_settings(guild_id)
        except Exception:
            return
        if not settings.log_moderation or not settings.log_channel_id:
            return
        embed = discord.Embed(
            title=f"Moderation event: {event}",
            description=details,
            timestamp=datetime.now(tz=timezone.utc),
        )
        embed.add_field(name="Moderator", value=_mention_or_dash(actor), inline=True)
        embed.add_field(name="Target", value=_mention_or_dash(target), inline=True)
        embed.add_field(name="Channel", value=_channel_mention(channel), inline=True)
        try:
            ch = self.get_channel(int(settings.log_channel_id))
            if ch:
                await ch.send(embed=embed)
        except Exception:
            pass

    async def _reminder_loop(self) -> None:
        while not self.is_closed():
            await asyncio.sleep(30)
            try:
                for r in await self.store.due_reminders():
                    await self.store.delete_reminder(r.id, r.user_id)
                    ch = self.get_channel(int(r.channel_id))
                    if ch:
                        try:
                            await ch.send(
                                content=f"<@{r.user_id}>",
                                embed=discord.Embed(
                                    color=0xFEE75C, title="⏰ Reminder",
                                    description=r.message[:2000],
                                    timestamp=datetime.now(tz=timezone.utc),
                                ),
                            )
                        except discord.HTTPException:
                            pass
            except Exception as exc:
                logger.warning("Reminder loop error: %s", exc)



    # ------------------------------------------------------------------ #
    # Event listeners                                                      #
    # ------------------------------------------------------------------ #

    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild:
            return
        try:
            settings = await self._get_settings(message.guild.id)
        except Exception:
            return
        if not settings.log_deletes:
            return
        author_id = str(message.author.id) if message.author else "unknown"
        content = message.content or "Content unavailable (not cached)."
        await self.store.audit(str(message.guild.id), "message_delete", author_id, "", str(message.channel.id), _truncate(content, 1800))
        if not settings.log_channel_id:
            return
        embed = discord.Embed(title="Message deleted", description=_truncate(content, 3500), timestamp=datetime.now(tz=timezone.utc))
        embed.add_field(name="Author", value=_mention_or_dash(author_id), inline=True)
        embed.add_field(name="Channel", value=_channel_mention(str(message.channel.id)), inline=True)
        try:
            ch = self.get_channel(int(settings.log_channel_id))
            if ch:
                await ch.send(embed=embed)
        except Exception:
            pass

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if not before.guild:
            return
        if not after.author or after.author.bot:
            return
        if before.content == after.content:
            return
        try:
            settings = await self._get_settings(before.guild.id)
        except Exception:
            return
        if not settings.log_edits:
            return
        details = f"Before: {_truncate(before.content, 700)}\nAfter: {_truncate(after.content, 700)}"
        await self.store.audit(str(before.guild.id), "message_edit", str(after.author.id), "", str(before.channel.id), details)
        if not settings.log_channel_id:
            return
        embed = discord.Embed(title="Message edited", timestamp=datetime.now(tz=timezone.utc))
        embed.add_field(name="Author", value=after.author.mention, inline=True)
        embed.add_field(name="Channel", value=_channel_mention(str(before.channel.id)), inline=True)
        embed.add_field(name="Before", value=_truncate(before.content, 1000), inline=False)
        embed.add_field(name="After", value=_truncate(after.content, 1000), inline=False)
        try:
            ch = self.get_channel(int(settings.log_channel_id))
            if ch:
                await ch.send(embed=embed)
        except Exception:
            pass

    async def on_member_join(self, member: discord.Member) -> None:
        wc = await self.store.welcome_config(str(member.guild.id))
        if wc.welcome_enabled and wc.welcome_channel_id:
            ch = self.get_channel(int(wc.welcome_channel_id))
            if ch:
                try:
                    await ch.send(wc.welcome_message.format(
                        user=member.mention,
                        username=str(member),
                        server=member.guild.name,
                        member_count=member.guild.member_count,
                    )[:2000])
                except discord.HTTPException:
                    pass

    async def on_member_remove(self, member: discord.Member) -> None:
        wc = await self.store.welcome_config(str(member.guild.id))
        if wc.goodbye_enabled and wc.goodbye_channel_id:
            ch = self.get_channel(int(wc.goodbye_channel_id))
            if ch:
                try:
                    await ch.send(wc.goodbye_message.format(
                        user=member.mention,
                        username=str(member),
                        server=member.guild.name,
                        member_count=member.guild.member_count,
                    )[:2000])
                except discord.HTTPException:
                    pass

    async def _handle_prefix_command(self, message: discord.Message) -> bool:
        prefix = self.config.command_prefix
        if not message.content.startswith(prefix):
            return False
        command_line = message.content[len(prefix):].strip()
        if not command_line:
            return True
        command, _, arguments = command_line.partition(" ")
        command = command.lower()
        arguments = arguments.strip()

        if command == "help":
            await message.channel.send(
                embed=_info_embed(
                    "Commands",
                    f"`{prefix}ping`, `{prefix}roll [number|NdN]`, `{prefix}coinflip`, "
                    f"`{prefix}choose option 1, option 2`, `{prefix}topic`",
                )
            )
        elif command == "ping":
            await message.channel.send(
                embed=_info_embed("Pong!", f"Latency: **{round(self.latency * 1000)}ms**")
            )
        elif command == "coinflip":
            await message.channel.send(
                embed=discord.Embed(
                    color=0xFEE75C,
                    description=f"**{random.choice(['Heads', 'Tails'])}!**",
                )
            )
        elif command == "topic":
            await message.channel.send(
                embed=discord.Embed(color=0x5865F2, description=random.choice(_TOPICS))
            )
        elif command == "choose":
            choices = [choice.strip() for choice in arguments.split(",") if choice.strip()]
            if len(choices) < 2:
                await message.channel.send(embed=_error_embed("Provide at least two comma-separated options."))
            else:
                await message.channel.send(
                    embed=discord.Embed(
                        color=0x5865F2,
                        description=f"I choose: **{random.choice(choices)}**",
                    )
                )
        elif command == "roll":
            dice = arguments or "6"
            roll_match = re.fullmatch(r"(\d+)d(\d+)", dice, re.IGNORECASE)
            if roll_match:
                count = max(1, min(20, int(roll_match.group(1))))
                sides = max(2, min(1_000_000, int(roll_match.group(2))))
                rolls = [random.randint(1, sides) for _ in range(count)]
                result = ", ".join(map(str, rolls)) if count <= 10 else f"{count} rolls"
                embed = discord.Embed(color=0x5865F2, title=dice, description=f"{result}\n**Total: {sum(rolls)}**")
            else:
                try:
                    sides = max(2, min(1_000_000, int(dice)))
                    embed = discord.Embed(color=0x5865F2, title=f"1-{sides}", description=f"**{random.randint(1, sides)}**")
                except ValueError:
                    embed = _error_embed("Use a number or dice notation like `2d6`.")
            await message.channel.send(embed=embed)
        else:
            custom_command = await self.store.custom_command(str(message.guild.id), command)
            if custom_command:
                await message.channel.send(custom_command.response[:2000])
            else:
                await message.channel.send(
                    embed=_error_embed(f"Unknown command. Use `{prefix}help` to list available commands.")
                )
        return True

    async def _automod_blocked(self, message: discord.Message) -> bool:
        try:
            am = await self.store.automod_config(str(message.guild.id))
        except Exception:
            return False
        if not am.enabled:
            return False

        exempt_role_ids = _parse_discord_ids(am.exempt_role_ids)
        author_roles = getattr(message.author, "roles", ())
        if exempt_role_ids and any(str(role.id) in exempt_role_ids for role in author_roles):
            return False
        if str(message.channel.id) in _parse_discord_ids(am.exempt_channel_ids):
            return False

        content = message.content
        key = f"{message.guild.id}:{message.author.id}"
        now = datetime.now(tz=timezone.utc).timestamp()
        triggered, reason = False, ""

        if am.anti_spam_enabled:
            times = self._spam_tracker[key]
            times.append(now)
            times[:] = [t for t in times if now - t < am.anti_spam_interval]
            if len(times) >= am.anti_spam_threshold:
                triggered, reason = True, f"Spam ({len(times)} msgs in {am.anti_spam_interval}s)"
                self._spam_tracker[key] = []

        if not triggered and am.anti_mention_enabled:
            if len({m.id for m in message.mentions}) >= am.anti_mention_threshold:
                triggered, reason = True, f"Mass mention ({len(message.mentions)} users)"

        if not triggered and am.anti_invite_enabled and _INVITE_RE.search(content):
            triggered, reason = True, "Server invite link"

        if not triggered and am.anti_link_enabled and _URL_RE.search(content):
            triggered, reason = True, "Unsolicited link"

        if not triggered and am.anti_caps_enabled:
            letters = [character for character in content if character.isalpha()]
            if len(letters) >= 10:
                uppercase_percent = sum(character.isupper() for character in letters) * 100 / len(letters)
                if uppercase_percent >= am.anti_caps_threshold:
                    triggered, reason = True, f"Excessive caps ({uppercase_percent:.0f}%)"

        if not triggered and am.anti_emoji_enabled:
            emoji_count = len(_EMOJI_RE.findall(content))
            if emoji_count >= am.anti_emoji_threshold:
                triggered, reason = True, f"Emoji spam ({emoji_count} emoji)"

        if not triggered and am.banned_words:
            cl = content.lower()
            for word in (w.strip().lower() for w in am.banned_words.split("\n") if w.strip()):
                if re.search(rf"\b{re.escape(word)}\b", cl):
                    triggered, reason = True, "Banned word"
                    break

        if not triggered:
            return False

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        action = am.action

        if action == "warn":
            await self.store.add_warning(guild_id, user_id, str(self.user.id), f"[Automod] {reason}")
            try:
                await message.channel.send(
                    embed=_error_embed(f"{message.author.mention} — {reason} — message removed."),
                    delete_after=8,
                )
            except discord.HTTPException:
                pass
        elif action == "timeout":
            if isinstance(message.author, discord.Member):
                try:
                    await message.author.timeout(discord.utils.utcnow() + timedelta(minutes=10), reason=f"Automod: {reason}")
                except discord.HTTPException:
                    pass
        elif action == "kick":
            try:
                await message.guild.kick(message.author, reason=f"Automod: {reason}")
            except discord.HTTPException:
                pass
        elif action == "ban":
            try:
                await message.guild.ban(message.author, reason=f"Automod: {reason}", delete_message_days=1)
            except discord.HTTPException:
                pass

        await self.store.audit(guild_id, "automod", str(self.user.id), user_id, str(message.channel.id), f"{action}: {reason}")
        return True

    async def _handle_ai_chat(self, message: discord.Message) -> None:
        if not self.user:
            return
        try:
            ai_config = await self.store.ai_chat_config(str(message.guild.id))
        except Exception:
            logger.warning("Could not load AI chat configuration for guild %s", message.guild.id, exc_info=True)
            return
        if not ai_config.enabled or ai_config.channel_id != str(message.channel.id):
            return

        mentioned = self.user in message.mentions
        replies_to_bot = False
        if message.reference and message.reference.message_id:
            referenced_message = message.reference.resolved
            if not isinstance(referenced_message, discord.Message):
                try:
                    referenced_message = await message.channel.fetch_message(
                        message.reference.message_id
                    )
                except discord.HTTPException:
                    referenced_message = None
            replies_to_bot = (
                isinstance(referenced_message, discord.Message)
                and referenced_message.author.id == self.user.id
            )
        if ai_config.mention_only and not (mentioned or replies_to_bot):
            return
        prompt = re.sub(rf"<@!?{self.user.id}>", "", message.content).strip()
        if not prompt:
            return

        now = time.monotonic()
        user_key = f"{message.guild.id}:{message.author.id}"
        channel_key = f"{message.guild.id}:{message.channel.id}"
        if now < self._ai_user_cooldowns.get(user_key, 0) or now < self._ai_channel_cooldowns.get(channel_key, 0):
            return
        self._ai_user_cooldowns[user_key] = now + ai_config.user_cooldown_seconds
        self._ai_channel_cooldowns[channel_key] = now + ai_config.channel_cooldown_seconds

        system_prompt = ai_config.system_prompt[:6000]
        staff_memory = ai_config.staff_memory.strip()[:4000]
        if staff_memory:
            memory_heading = (
                "\n\n## Staff-authored context and factual notes\n"
                "Treat these as context and factual notes. Do not claim information that is not in these notes.\n"
            )
            available_memory = max(0, 6000 - len(system_prompt) - len(memory_heading))
            system_prompt = f"{system_prompt}{memory_heading}{staff_memory[:available_memory]}"

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with message.channel.typing():
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{self.config.ollama_base_url}/api/chat",
                        json={
                            "model": ai_config.model,
                            "stream": False,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": prompt},
                            ],
                        },
                    ) as response:
                        response.raise_for_status()
                        payload = await response.json()
            content = str(payload.get("message", {}).get("content", "")).strip()
            if content:
                await message.reply(
                    content[:1800],
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            logger.warning("Ollama request failed for guild %s", message.guild.id, exc_info=True)
        except discord.HTTPException:
            logger.warning("Could not send Ollama response in channel %s", message.channel.id, exc_info=True)

    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.author.bot:
            return
        if await self._handle_prefix_command(message):
            return
        if await self._automod_blocked(message):
            return
        await self._handle_ai_chat(message)

