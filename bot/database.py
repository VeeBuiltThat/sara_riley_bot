from __future__ import annotations

import aiomysql
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class GuildSettings:
    guild_id: str
    log_channel_id: str = ""
    log_deletes: bool = True
    log_edits: bool = True
    log_moderation: bool = True
    dm_warnings: bool = True
    owner_role_id: str = ""
    admin_role_id: str = ""
    mod_role_id: str = ""
    mod_commands_enabled: bool = True


@dataclass
class Warning:
    id: int
    guild_id: str
    user_id: str
    moderator_id: str
    reason: str
    created_at: datetime


@dataclass
class WelcomeConfig:
    guild_id: str
    welcome_channel_id: str = ""
    welcome_message: str = "Welcome {user} to **{server}**!"
    goodbye_channel_id: str = ""
    goodbye_message: str = "**{username}** has left the server."
    welcome_enabled: bool = False
    goodbye_enabled: bool = False


@dataclass
class AutomodConfig:
    guild_id: str
    enabled: bool = False
    anti_spam_enabled: bool = False
    anti_spam_threshold: int = 5
    anti_spam_interval: int = 5
    anti_mention_enabled: bool = False
    anti_mention_threshold: int = 5
    anti_invite_enabled: bool = False
    anti_link_enabled: bool = False
    banned_words: str = ""
    action: str = "warn"


@dataclass
class Ticket:
    id: int
    guild_id: str
    channel_id: str
    creator_id: str
    subject: str
    status: str
    created_at: datetime


@dataclass
class TicketConfig:
    guild_id: str
    category_id: str = ""
    log_channel_id: str = ""
    support_role_id: str = ""
    welcome_message: str = "Support ticket opened. A staff member will be with you shortly."


@dataclass
class Reminder:
    id: int
    guild_id: str
    user_id: str
    channel_id: str
    message: str
    due_at: datetime


class Store:
    def __init__(self, pool: aiomysql.Pool):
        self._pool = pool

    @classmethod
    async def open(cls, host: str, port: int, user: str, password: str, db: str) -> "Store":
        pool = await aiomysql.create_pool(
            host=host, port=port,
            user=user, password=password,
            db=db, autocommit=True,
            charset="utf8mb4",
            minsize=1, maxsize=10,
        )
        store = cls(pool)
        await store._migrate()
        return store

    async def close(self) -> None:
        self._pool.close()
        await self._pool.wait_closed()

    async def _migrate(self) -> None:
        stmts = [
            """CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id VARCHAR(20) NOT NULL,
                log_channel_id VARCHAR(20) NOT NULL DEFAULT '',
                log_deletes TINYINT(1) NOT NULL DEFAULT 1,
                log_edits TINYINT(1) NOT NULL DEFAULT 1,
                log_moderation TINYINT(1) NOT NULL DEFAULT 1,
                dm_warnings TINYINT(1) NOT NULL DEFAULT 1,
                owner_role_id VARCHAR(20) NOT NULL DEFAULT '',
                admin_role_id VARCHAR(20) NOT NULL DEFAULT '',
                mod_role_id VARCHAR(20) NOT NULL DEFAULT '',
                mod_commands_enabled TINYINT(1) NOT NULL DEFAULT 1,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS warnings (
                id INT NOT NULL AUTO_INCREMENT,
                guild_id VARCHAR(20) NOT NULL,
                user_id VARCHAR(20) NOT NULL,
                moderator_id VARCHAR(20) NOT NULL,
                reason TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                INDEX idx_warnings_guild_user (guild_id, user_id)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS audit_events (
                id INT NOT NULL AUTO_INCREMENT,
                guild_id VARCHAR(20) NOT NULL,
                event_type VARCHAR(50) NOT NULL,
                actor_id VARCHAR(20) NOT NULL DEFAULT '',
                target_id VARCHAR(20) NOT NULL DEFAULT '',
                channel_id VARCHAR(20) NOT NULL DEFAULT '',
                details TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                INDEX idx_audit_guild_created (guild_id, created_at)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS channel_locks (
                guild_id VARCHAR(20) NOT NULL,
                channel_id VARCHAR(20) NOT NULL,
                had_overwrite TINYINT(1) NOT NULL,
                allow_bits BIGINT NOT NULL,
                deny_bits BIGINT NOT NULL,
                locked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, channel_id)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS welcome_config (
                guild_id VARCHAR(20) NOT NULL,
                welcome_channel_id VARCHAR(20) NOT NULL DEFAULT '',
                welcome_message VARCHAR(2000) NOT NULL DEFAULT 'Welcome {user} to **{server}**!',
                goodbye_channel_id VARCHAR(20) NOT NULL DEFAULT '',
                goodbye_message VARCHAR(2000) NOT NULL DEFAULT '**{username}** has left the server.',
                welcome_enabled TINYINT(1) NOT NULL DEFAULT 0,
                goodbye_enabled TINYINT(1) NOT NULL DEFAULT 0,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS automod_config (
                guild_id VARCHAR(20) NOT NULL,
                enabled TINYINT(1) NOT NULL DEFAULT 0,
                anti_spam_enabled TINYINT(1) NOT NULL DEFAULT 0,
                anti_spam_threshold INT NOT NULL DEFAULT 5,
                anti_spam_interval INT NOT NULL DEFAULT 5,
                anti_mention_enabled TINYINT(1) NOT NULL DEFAULT 0,
                anti_mention_threshold INT NOT NULL DEFAULT 5,
                anti_invite_enabled TINYINT(1) NOT NULL DEFAULT 0,
                anti_link_enabled TINYINT(1) NOT NULL DEFAULT 0,
                banned_words VARCHAR(4000) NOT NULL DEFAULT '',
                action VARCHAR(10) NOT NULL DEFAULT 'warn',
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS tickets (
                id INT NOT NULL AUTO_INCREMENT,
                guild_id VARCHAR(20) NOT NULL,
                channel_id VARCHAR(20) NOT NULL DEFAULT '',
                creator_id VARCHAR(20) NOT NULL,
                subject TEXT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'open',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                INDEX idx_tickets_guild (guild_id),
                INDEX idx_tickets_channel (channel_id)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS ticket_config (
                guild_id VARCHAR(20) NOT NULL,
                category_id VARCHAR(20) NOT NULL DEFAULT '',
                log_channel_id VARCHAR(20) NOT NULL DEFAULT '',
                support_role_id VARCHAR(20) NOT NULL DEFAULT '',
                welcome_message VARCHAR(2000) NOT NULL DEFAULT 'Support ticket opened. A staff member will be with you shortly.',
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id)
            ) CHARACTER SET utf8mb4;""",
            """CREATE TABLE IF NOT EXISTS reminders (
                id INT NOT NULL AUTO_INCREMENT,
                guild_id VARCHAR(20) NOT NULL DEFAULT '',
                user_id VARCHAR(20) NOT NULL,
                channel_id VARCHAR(20) NOT NULL,
                message TEXT NOT NULL,
                due_at DATETIME NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                INDEX idx_reminders_due (due_at)
            ) CHARACTER SET utf8mb4;""",
        ]
        # Columns added after initial release — safe no-op when they already exist
        alters = [
            "ALTER TABLE guild_settings ADD COLUMN owner_role_id VARCHAR(20) NOT NULL DEFAULT ''",
            "ALTER TABLE guild_settings ADD COLUMN admin_role_id VARCHAR(20) NOT NULL DEFAULT ''",
            "ALTER TABLE guild_settings ADD COLUMN mod_role_id VARCHAR(20) NOT NULL DEFAULT ''",
            "ALTER TABLE guild_settings ADD COLUMN mod_commands_enabled TINYINT(1) NOT NULL DEFAULT 1",
        ]
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                for stmt in stmts:
                    await cur.execute(stmt)
                for alter in alters:
                    try:
                        await cur.execute(alter)
                    except Exception:
                        pass

    async def settings(
        self,
        guild_id: str,
        default_log_channel: str = "",
        default_owner_role: str = "",
        default_admin_role: str = "",
        default_mod_role: str = "",
    ) -> GuildSettings:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO guild_settings"
                    " (guild_id, log_channel_id, owner_role_id, admin_role_id, mod_role_id)"
                    " VALUES (%s, %s, %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE guild_id=guild_id",
                    (guild_id, default_log_channel, default_owner_role, default_admin_role, default_mod_role),
                )
                await cur.execute(
                    "SELECT guild_id, log_channel_id, log_deletes, log_edits, log_moderation,"
                    " dm_warnings, owner_role_id, admin_role_id, mod_role_id, mod_commands_enabled"
                    " FROM guild_settings WHERE guild_id=%s",
                    (guild_id,),
                )
                row = await cur.fetchone()
        if not row:
            return GuildSettings(guild_id=guild_id)
        return GuildSettings(
            guild_id=row[0],
            log_channel_id=row[1],
            log_deletes=bool(row[2]),
            log_edits=bool(row[3]),
            log_moderation=bool(row[4]),
            dm_warnings=bool(row[5]),
            owner_role_id=row[6] or "",
            admin_role_id=row[7] or "",
            mod_role_id=row[8] or "",
            mod_commands_enabled=bool(row[9]),
        )

    async def add_warning(self, guild_id: str, user_id: str, moderator_id: str, reason: str) -> int:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO warnings(guild_id,user_id,moderator_id,reason) VALUES (%s,%s,%s,%s)",
                    (guild_id, user_id, moderator_id, reason),
                )
                return cur.lastrowid

    async def warnings(self, guild_id: str, user_id: str) -> list[Warning]:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id,guild_id,user_id,moderator_id,reason,created_at "
                    "FROM warnings WHERE guild_id=%s AND user_id=%s ORDER BY created_at DESC",
                    (guild_id, user_id),
                )
                rows = await cur.fetchall()
        return [
            Warning(
                id=row[0], guild_id=row[1], user_id=row[2],
                moderator_id=row[3], reason=row[4],
                created_at=row[5] if isinstance(row[5], datetime) else datetime.fromisoformat(str(row[5])),
            )
            for row in rows
        ]

    async def audit(self, guild_id: str, event_type: str, actor_id: str, target_id: str, channel_id: str, details: str) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO audit_events(guild_id,event_type,actor_id,target_id,channel_id,details) VALUES (%s,%s,%s,%s,%s,%s)",
                    (guild_id, event_type, actor_id, target_id, channel_id, details),
                )

    async def save_channel_lock(self, guild_id: str, channel_id: str, had_overwrite: bool, allow_bits: int, deny_bits: int) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO channel_locks(guild_id,channel_id,had_overwrite,allow_bits,deny_bits)
                       VALUES (%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE
                         had_overwrite=VALUES(had_overwrite),
                         allow_bits=VALUES(allow_bits),
                         deny_bits=VALUES(deny_bits),
                         locked_at=CURRENT_TIMESTAMP""",
                    (guild_id, channel_id, int(had_overwrite), allow_bits, deny_bits),
                )

    async def channel_lock(self, guild_id: str, channel_id: str) -> Optional[tuple[bool, int, int]]:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT had_overwrite,allow_bits,deny_bits FROM channel_locks WHERE guild_id=%s AND channel_id=%s",
                    (guild_id, channel_id),
                )
                row = await cur.fetchone()
        if not row:
            return None
        return (bool(row[0]), row[1], row[2])

    async def delete_channel_lock(self, guild_id: str, channel_id: str) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM channel_locks WHERE guild_id=%s AND channel_id=%s",
                    (guild_id, channel_id),
                )

    # ------------------------------------------------------------------ #
    # Warnings (extended)                                                  #
    # ------------------------------------------------------------------ #

    async def del_warning(self, warning_id: int, guild_id: str) -> bool:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM warnings WHERE id=%s AND guild_id=%s",
                    (warning_id, guild_id),
                )
                return cur.rowcount > 0

    async def clear_warnings(self, guild_id: str, user_id: str) -> int:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM warnings WHERE guild_id=%s AND user_id=%s",
                    (guild_id, user_id),
                )
                return cur.rowcount

    # ------------------------------------------------------------------ #
    # Welcome / Goodbye                                                    #
    # ------------------------------------------------------------------ #

    async def welcome_config(self, guild_id: str) -> WelcomeConfig:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO welcome_config (guild_id) VALUES (%s)"
                    " ON DUPLICATE KEY UPDATE guild_id=guild_id",
                    (guild_id,),
                )
                await cur.execute(
                    "SELECT guild_id, welcome_channel_id, welcome_message,"
                    " goodbye_channel_id, goodbye_message, welcome_enabled, goodbye_enabled"
                    " FROM welcome_config WHERE guild_id=%s",
                    (guild_id,),
                )
                row = await cur.fetchone()
        if not row:
            return WelcomeConfig(guild_id=guild_id)
        return WelcomeConfig(
            guild_id=row[0], welcome_channel_id=row[1] or "",
            welcome_message=row[2] or "Welcome {user} to **{server}**!",
            goodbye_channel_id=row[3] or "",
            goodbye_message=row[4] or "**{username}** has left the server.",
            welcome_enabled=bool(row[5]), goodbye_enabled=bool(row[6]),
        )

    async def save_welcome_config(
        self, guild_id: str,
        welcome_channel_id: str, welcome_message: str,
        goodbye_channel_id: str, goodbye_message: str,
        welcome_enabled: bool, goodbye_enabled: bool,
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE welcome_config"
                    " SET welcome_channel_id=%s, welcome_message=%s,"
                    "     goodbye_channel_id=%s, goodbye_message=%s,"
                    "     welcome_enabled=%s, goodbye_enabled=%s, updated_at=CURRENT_TIMESTAMP"
                    " WHERE guild_id=%s",
                    (welcome_channel_id, welcome_message, goodbye_channel_id, goodbye_message,
                     int(welcome_enabled), int(goodbye_enabled), guild_id),
                )

    # ------------------------------------------------------------------ #
    # Automod                                                              #
    # ------------------------------------------------------------------ #

    async def automod_config(self, guild_id: str) -> AutomodConfig:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO automod_config (guild_id) VALUES (%s)"
                    " ON DUPLICATE KEY UPDATE guild_id=guild_id",
                    (guild_id,),
                )
                await cur.execute(
                    "SELECT guild_id, enabled, anti_spam_enabled, anti_spam_threshold,"
                    " anti_spam_interval, anti_mention_enabled, anti_mention_threshold,"
                    " anti_invite_enabled, anti_link_enabled, banned_words, action"
                    " FROM automod_config WHERE guild_id=%s",
                    (guild_id,),
                )
                row = await cur.fetchone()
        if not row:
            return AutomodConfig(guild_id=guild_id)
        return AutomodConfig(
            guild_id=row[0], enabled=bool(row[1]),
            anti_spam_enabled=bool(row[2]), anti_spam_threshold=row[3], anti_spam_interval=row[4],
            anti_mention_enabled=bool(row[5]), anti_mention_threshold=row[6],
            anti_invite_enabled=bool(row[7]), anti_link_enabled=bool(row[8]),
            banned_words=row[9] or "", action=row[10] or "warn",
        )

    async def save_automod_config(
        self, guild_id: str,
        enabled: bool,
        anti_spam_enabled: bool, anti_spam_threshold: int, anti_spam_interval: int,
        anti_mention_enabled: bool, anti_mention_threshold: int,
        anti_invite_enabled: bool, anti_link_enabled: bool,
        banned_words: str, action: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE automod_config"
                    " SET enabled=%s, anti_spam_enabled=%s, anti_spam_threshold=%s,"
                    "     anti_spam_interval=%s, anti_mention_enabled=%s, anti_mention_threshold=%s,"
                    "     anti_invite_enabled=%s, anti_link_enabled=%s,"
                    "     banned_words=%s, action=%s, updated_at=CURRENT_TIMESTAMP"
                    " WHERE guild_id=%s",
                    (int(enabled), int(anti_spam_enabled), anti_spam_threshold, anti_spam_interval,
                     int(anti_mention_enabled), anti_mention_threshold,
                     int(anti_invite_enabled), int(anti_link_enabled),
                     banned_words, action, guild_id),
                )

    # ------------------------------------------------------------------ #
    # Tickets                                                              #
    # ------------------------------------------------------------------ #

    async def create_ticket(self, guild_id: str, channel_id: str, creator_id: str, subject: str) -> int:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO tickets(guild_id,channel_id,creator_id,subject) VALUES (%s,%s,%s,%s)",
                    (guild_id, channel_id, creator_id, subject),
                )
                return cur.lastrowid

    async def ticket_by_channel(self, guild_id: str, channel_id: str) -> Optional[Ticket]:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id,guild_id,channel_id,creator_id,subject,status,created_at"
                    " FROM tickets WHERE guild_id=%s AND channel_id=%s",
                    (guild_id, channel_id),
                )
                row = await cur.fetchone()
        if not row:
            return None
        return Ticket(
            id=row[0], guild_id=row[1], channel_id=row[2],
            creator_id=row[3], subject=row[4], status=row[5],
            created_at=row[6] if isinstance(row[6], datetime) else datetime.fromisoformat(str(row[6])),
        )

    async def close_ticket(self, guild_id: str, channel_id: str) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE tickets SET status='closed' WHERE guild_id=%s AND channel_id=%s",
                    (guild_id, channel_id),
                )

    async def ticket_config(self, guild_id: str) -> TicketConfig:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO ticket_config (guild_id) VALUES (%s)"
                    " ON DUPLICATE KEY UPDATE guild_id=guild_id",
                    (guild_id,),
                )
                await cur.execute(
                    "SELECT guild_id, category_id, log_channel_id, support_role_id, welcome_message"
                    " FROM ticket_config WHERE guild_id=%s",
                    (guild_id,),
                )
                row = await cur.fetchone()
        if not row:
            return TicketConfig(guild_id=guild_id)
        return TicketConfig(
            guild_id=row[0], category_id=row[1] or "",
            log_channel_id=row[2] or "", support_role_id=row[3] or "",
            welcome_message=row[4] or "Support ticket opened. A staff member will be with you shortly.",
        )

    async def save_ticket_config(
        self, guild_id: str,
        category_id: str, log_channel_id: str,
        support_role_id: str, welcome_message: str,
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE ticket_config"
                    " SET category_id=%s, log_channel_id=%s, support_role_id=%s,"
                    "     welcome_message=%s, updated_at=CURRENT_TIMESTAMP"
                    " WHERE guild_id=%s",
                    (category_id, log_channel_id, support_role_id, welcome_message, guild_id),
                )

    # ------------------------------------------------------------------ #
    # Reminders                                                            #
    # ------------------------------------------------------------------ #

    async def add_reminder(
        self, guild_id: str, user_id: str, channel_id: str,
        message: str, due_at: datetime,
    ) -> int:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO reminders(guild_id,user_id,channel_id,message,due_at)"
                    " VALUES (%s,%s,%s,%s,%s)",
                    (guild_id, user_id, channel_id, message, due_at),
                )
                return cur.lastrowid

    async def user_reminders(self, guild_id: str, user_id: str) -> list[Reminder]:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id,guild_id,user_id,channel_id,message,due_at"
                    " FROM reminders WHERE guild_id=%s AND user_id=%s ORDER BY due_at ASC LIMIT 20",
                    (guild_id, user_id),
                )
                rows = await cur.fetchall()
        return [
            Reminder(
                id=r[0], guild_id=r[1], user_id=r[2], channel_id=r[3], message=r[4],
                due_at=r[5] if isinstance(r[5], datetime) else datetime.fromisoformat(str(r[5])),
            )
            for r in rows
        ]

    async def due_reminders(self) -> list[Reminder]:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id,guild_id,user_id,channel_id,message,due_at"
                    " FROM reminders WHERE due_at <= NOW() ORDER BY due_at ASC LIMIT 50",
                )
                rows = await cur.fetchall()
        return [
            Reminder(
                id=r[0], guild_id=r[1], user_id=r[2], channel_id=r[3], message=r[4],
                due_at=r[5] if isinstance(r[5], datetime) else datetime.fromisoformat(str(r[5])),
            )
            for r in rows
        ]

    async def delete_reminder(self, reminder_id: int, user_id: str) -> bool:
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM reminders WHERE id=%s AND user_id=%s",
                    (reminder_id, user_id),
                )
                return cur.rowcount > 0

