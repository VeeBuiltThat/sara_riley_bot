package config

import (
	"fmt"
	"os"
)

type Config struct {
	DiscordToken string
	GuildID      string
	DatabasePath string
}

func Load() (Config, error) {
	cfg := Config{
		DiscordToken: os.Getenv("DISCORD_TOKEN"),
		GuildID:      os.Getenv("DISCORD_GUILD_ID"),
		DatabasePath: os.Getenv("DATABASE_PATH"),
	}
	if cfg.DatabasePath == "" {
		cfg.DatabasePath = "./data/modbot.db"
	}
	if cfg.DiscordToken == "" {
		return Config{}, fmt.Errorf("DISCORD_TOKEN is required")
	}
	return cfg, nil
}
