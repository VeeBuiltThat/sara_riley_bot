from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
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
        self._register_commands()

    async def setup_hook(self) -> None:
        if self._guild:
            self.tree.copy_global_to(guild=self._guild)
            await self.tree.sync(guild=self._guild)
        else:
            await self.tree.sync()
        logger.info("Slash commands synced")

    async def on_ready(self) -> None:
        logger.info("Discord bot connected: %s", self.user)

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
            warning_id = await self.store.add_warning(str(interaction.guild_id), str(user.id), str(interaction.user.id), reason)
            await self._mod_log(interaction.guild_id, "warning", str(interaction.user.id), str(user.id), str(interaction.channel_id), f"#{warning_id}: {reason}")
            settings = await self.store.settings(str(interaction.guild_id))
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

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _mod_log(self, guild_id: int, event: str, actor: str, target: str, channel: str, details: str) -> None:
        await self.store.audit(str(guild_id), event, actor, target, channel, details)
        try:
            settings = await self.store.settings(str(guild_id))
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

    # ------------------------------------------------------------------ #
    # Event listeners                                                      #
    # ------------------------------------------------------------------ #

    async def on_message_delete(self, message: discord.Message) -> None:
        if not message.guild:
            return
        try:
            settings = await self.store.settings(str(message.guild.id))
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
            settings = await self.store.settings(str(before.guild.id))
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
