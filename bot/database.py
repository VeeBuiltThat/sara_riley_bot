from __future__ import annotations

import aiosqlite
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


@dataclass
class Warning:
    id: int
    guild_id: str
    user_id: str
    moderator_id: str
    reason: str
    created_at: datetime


class Store:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    @classmethod
    async def open(cls, path: str) -> "Store":
        db = await aiosqlite.connect(path)
        store = cls(db)
        await store._migrate()
        return store

    async def close(self) -> None:
        await self.db.close()

    async def _migrate(self) -> None:
        stmts = [
            "PRAGMA journal_mode=WAL;",
            "PRAGMA busy_timeout=5000;",
            """CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                log_channel_id TEXT NOT NULL DEFAULT '',
                log_deletes INTEGER NOT NULL DEFAULT 1,
                log_edits INTEGER NOT NULL DEFAULT 1,
                log_moderation INTEGER NOT NULL DEFAULT 1,
                dm_warnings INTEGER NOT NULL DEFAULT 1,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );""",
            """CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                moderator_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );""",
            "CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id);",
            """CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                channel_id TEXT NOT NULL DEFAULT '',
                details TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );""",
            "CREATE INDEX IF NOT EXISTS idx_audit_guild_created ON audit_events(guild_id, created_at DESC);",
            """CREATE TABLE IF NOT EXISTS channel_locks (
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                had_overwrite INTEGER NOT NULL,
                allow_bits INTEGER NOT NULL,
                deny_bits INTEGER NOT NULL,
                locked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(guild_id, channel_id)
            );""",
        ]
        for stmt in stmts:
            await self.db.execute(stmt)
        await self.db.commit()

    async def settings(self, guild_id: str) -> GuildSettings:
        await self.db.execute(
            "INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)", (guild_id,)
        )
        await self.db.commit()
        async with self.db.execute(
            "SELECT guild_id, log_channel_id, log_deletes, log_edits, log_moderation, dm_warnings "
            "FROM guild_settings WHERE guild_id=?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return GuildSettings(guild_id=guild_id)
        return GuildSettings(
            guild_id=row[0],
            log_channel_id=row[1],
            log_deletes=bool(row[2]),
            log_edits=bool(row[3]),
            log_moderation=bool(row[4]),
            dm_warnings=bool(row[5]),
        )

    async def add_warning(self, guild_id: str, user_id: str, moderator_id: str, reason: str) -> int:
        cursor = await self.db.execute(
            "INSERT INTO warnings(guild_id,user_id,moderator_id,reason) VALUES (?,?,?,?)",
            (guild_id, user_id, moderator_id, reason),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def warnings(self, guild_id: str, user_id: str) -> list[Warning]:
        async with self.db.execute(
            "SELECT id,guild_id,user_id,moderator_id,reason,created_at "
            "FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC",
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            ts = row[5]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            result.append(Warning(
                id=row[0], guild_id=row[1], user_id=row[2],
                moderator_id=row[3], reason=row[4], created_at=ts,
            ))
        return result

    async def audit(self, guild_id: str, event_type: str, actor_id: str, target_id: str, channel_id: str, details: str) -> None:
        await self.db.execute(
            "INSERT INTO audit_events(guild_id,event_type,actor_id,target_id,channel_id,details) VALUES (?,?,?,?,?,?)",
            (guild_id, event_type, actor_id, target_id, channel_id, details),
        )
        await self.db.commit()

    async def save_channel_lock(self, guild_id: str, channel_id: str, had_overwrite: bool, allow_bits: int, deny_bits: int) -> None:
        await self.db.execute(
            """INSERT INTO channel_locks(guild_id,channel_id,had_overwrite,allow_bits,deny_bits)
               VALUES (?,?,?,?,?)
               ON CONFLICT(guild_id,channel_id) DO UPDATE SET
                 had_overwrite=excluded.had_overwrite,
                 allow_bits=excluded.allow_bits,
                 deny_bits=excluded.deny_bits,
                 locked_at=CURRENT_TIMESTAMP""",
            (guild_id, channel_id, int(had_overwrite), allow_bits, deny_bits),
        )
        await self.db.commit()

    async def channel_lock(self, guild_id: str, channel_id: str) -> Optional[tuple[bool, int, int]]:
        """Returns (had_overwrite, allow_bits, deny_bits) or None if not found."""
        async with self.db.execute(
            "SELECT had_overwrite,allow_bits,deny_bits FROM channel_locks WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        return (bool(row[0]), row[1], row[2])

    async def delete_channel_lock(self, guild_id: str, channel_id: str) -> None:
        await self.db.execute(
            "DELETE FROM channel_locks WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id),
        )
        await self.db.commit()
