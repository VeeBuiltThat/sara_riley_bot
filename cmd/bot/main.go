package main

import (
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"discord-mod-bot/internal/bot"
	"discord-mod-bot/internal/config"
	"discord-mod-bot/internal/database"
	"github.com/joho/godotenv"
)

func main() {
	_ = godotenv.Load()
	cfg, err := config.Load()
	if err != nil {
		slog.Error("configuration error", "error", err)
		os.Exit(1)
	}
	store, err := database.Open(cfg.DatabasePath)
	if err != nil {
		slog.Error("database error", "error", err)
		os.Exit(1)
	}
	defer store.Close()
	b, err := bot.New(cfg.DiscordToken, cfg.GuildID, store)
	if err != nil {
		slog.Error("bot init failed", "error", err)
		os.Exit(1)
	}
	if err := b.Open(); err != nil {
		slog.Error("bot start failed", "error", err)
		os.Exit(1)
	}
	defer b.Close()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	<-stop
	slog.Info("shutting down")
}
