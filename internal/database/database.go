package database

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

type Store struct{ DB *sql.DB }

type GuildSettings struct {
	GuildID       string
	LogChannelID  string
	LogDeletes    bool
	LogEdits      bool
	LogModeration bool
	DMWarnings    bool
}

type Warning struct {
	ID          int64
	GuildID     string
	UserID      string
	ModeratorID string
	Reason      string
	CreatedAt   time.Time
}

func Open(path string) (*Store, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	s := &Store{DB: db}
	if err := s.migrate(context.Background()); err != nil {
		_ = db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) Close() error { return s.DB.Close() }

func (s *Store) migrate(ctx context.Context) error {
	stmts := []string{
		`PRAGMA journal_mode=WAL;`,
		`PRAGMA busy_timeout=5000;`,
		`CREATE TABLE IF NOT EXISTS guild_settings (
			guild_id TEXT PRIMARY KEY,
			log_channel_id TEXT NOT NULL DEFAULT '',
			log_deletes INTEGER NOT NULL DEFAULT 1,
			log_edits INTEGER NOT NULL DEFAULT 1,
			log_moderation INTEGER NOT NULL DEFAULT 1,
			dm_warnings INTEGER NOT NULL DEFAULT 1,
			updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
		);`,
		`CREATE TABLE IF NOT EXISTS warnings (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			guild_id TEXT NOT NULL,
			user_id TEXT NOT NULL,
			moderator_id TEXT NOT NULL,
			reason TEXT NOT NULL,
			created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
		);`,
		`CREATE INDEX IF NOT EXISTS idx_warnings_guild_user ON warnings(guild_id, user_id);`,
		`CREATE TABLE IF NOT EXISTS audit_events (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			guild_id TEXT NOT NULL,
			event_type TEXT NOT NULL,
			actor_id TEXT NOT NULL DEFAULT '',
			target_id TEXT NOT NULL DEFAULT '',
			channel_id TEXT NOT NULL DEFAULT '',
			details TEXT NOT NULL DEFAULT '',
			created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
		);`,
		`CREATE INDEX IF NOT EXISTS idx_audit_guild_created ON audit_events(guild_id, created_at DESC);`,
		`CREATE TABLE IF NOT EXISTS channel_locks (
			guild_id TEXT NOT NULL,
			channel_id TEXT NOT NULL,
			had_overwrite INTEGER NOT NULL,
			allow_bits INTEGER NOT NULL,
			deny_bits INTEGER NOT NULL,
			locked_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
			PRIMARY KEY(guild_id, channel_id)
		);`,
	}
	for _, stmt := range stmts {
		if _, err := s.DB.ExecContext(ctx, stmt); err != nil {
			return fmt.Errorf("migration failed: %w", err)
		}
	}
	return nil
}

func (s *Store) Settings(ctx context.Context, guildID string) (GuildSettings, error) {
	_, err := s.DB.ExecContext(ctx, `INSERT OR IGNORE INTO guild_settings(guild_id) VALUES (?)`, guildID)
	if err != nil {
		return GuildSettings{}, err
	}
	var g GuildSettings
	err = s.DB.QueryRowContext(ctx, `SELECT guild_id, log_channel_id, log_deletes, log_edits, log_moderation, dm_warnings FROM guild_settings WHERE guild_id=?`, guildID).
		Scan(&g.GuildID, &g.LogChannelID, &g.LogDeletes, &g.LogEdits, &g.LogModeration, &g.DMWarnings)
	return g, err
}

func (s *Store) AddWarning(ctx context.Context, guildID, userID, moderatorID, reason string) (int64, error) {
	res, err := s.DB.ExecContext(ctx, `INSERT INTO warnings(guild_id,user_id,moderator_id,reason) VALUES (?,?,?,?)`, guildID, userID, moderatorID, reason)
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}

func (s *Store) Warnings(ctx context.Context, guildID, userID string) ([]Warning, error) {
	rows, err := s.DB.QueryContext(ctx, `SELECT id,guild_id,user_id,moderator_id,reason,created_at FROM warnings WHERE guild_id=? AND user_id=? ORDER BY created_at DESC`, guildID, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Warning
	for rows.Next() {
		var w Warning
		if err := rows.Scan(&w.ID, &w.GuildID, &w.UserID, &w.ModeratorID, &w.Reason, &w.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, w)
	}
	return out, rows.Err()
}

func (s *Store) Audit(ctx context.Context, guildID, eventType, actorID, targetID, channelID, details string) error {
	_, err := s.DB.ExecContext(ctx, `INSERT INTO audit_events(guild_id,event_type,actor_id,target_id,channel_id,details) VALUES (?,?,?,?,?,?)`, guildID, eventType, actorID, targetID, channelID, details)
	return err
}

func (s *Store) SaveChannelLock(ctx context.Context, guildID, channelID string, hadOverwrite bool, allow, deny int64) error {
	_, err := s.DB.ExecContext(ctx, `INSERT INTO channel_locks(guild_id,channel_id,had_overwrite,allow_bits,deny_bits) VALUES (?,?,?,?,?) ON CONFLICT(guild_id,channel_id) DO UPDATE SET had_overwrite=excluded.had_overwrite,allow_bits=excluded.allow_bits,deny_bits=excluded.deny_bits,locked_at=CURRENT_TIMESTAMP`, guildID, channelID, hadOverwrite, allow, deny)
	return err
}

func (s *Store) ChannelLock(ctx context.Context, guildID, channelID string) (had bool, allow, deny int64, found bool, err error) {
	err = s.DB.QueryRowContext(ctx, `SELECT had_overwrite,allow_bits,deny_bits FROM channel_locks WHERE guild_id=? AND channel_id=?`, guildID, channelID).Scan(&had, &allow, &deny)
	if err == sql.ErrNoRows {
		return false, 0, 0, false, nil
	}
	if err != nil {
		return false, 0, 0, false, err
	}
	return had, allow, deny, true, nil
}

func (s *Store) DeleteChannelLock(ctx context.Context, guildID, channelID string) error {
	_, err := s.DB.ExecContext(ctx, `DELETE FROM channel_locks WHERE guild_id=? AND channel_id=?`, guildID, channelID)
	return err
}
