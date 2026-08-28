# Sentinel — Discord Moderation Bot + Streamlit Dashboard

A clean moderation stack built with Go, DiscordGo, SQLite, and Streamlit.

## Included features

- `/kick user [reason]`
- `/ban user [reason]`
- `/warn user reason` with persistent warnings and optional DM
- `/warnings user`
- `/userinfo user`
- `/lock [reason]` and `/unlock`
- Deleted-message logging
- Edited-message logging with before/after content when available in cache
- Moderation audit log persisted to SQLite
- Streamlit dashboard for logging settings, warnings, and audit history
- Docker Compose deployment

## Architecture

The bot and dashboard share one SQLite database. The bot owns Discord interactions and event processing; the dashboard only manages configuration and reads moderation data. Secrets stay in environment variables.

## Discord setup

1. Create an application and bot in the Discord Developer Portal.
2. Enable **Server Members Intent** and **Message Content Intent** under the bot's Privileged Gateway Intents. Message Content is required if you want useful deleted/edited message content logs.
3. Invite the bot with the `bot` and `applications.commands` scopes.
4. Give it only the permissions it needs: Kick Members, Ban Members, Moderate Members, Manage Channels, View Channels, Send Messages, Embed Links, Read Message History.
5. Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN` and a strong `DASHBOARD_PASSWORD`. `DISCORD_GUILD_ID` is optional; if set, slash commands register to that guild and update immediately during development. If blank, they register globally and Discord propagation can take longer.

## Local development

```bash
cp .env.example .env
go mod tidy
go run ./cmd/bot
```

In another terminal:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

Open `http://localhost:8501` and sign in with `DASHBOARD_PASSWORD`.

## Docker

```bash
cp .env.example .env
docker compose up --build -d
```

The dashboard is exposed on port `8501`. For internet-facing deployments, put it behind HTTPS and an authenticated reverse proxy (Cloudflare Access, Authelia, oauth2-proxy, etc.). The built-in password is a deployment baseline, not an enterprise identity provider.

## Operational notes

- SQLite uses WAL mode and a busy timeout so the Go bot and Streamlit dashboard can safely share the database for this workload.
- Deleted message content can only be logged if DiscordGo had the message cached before deletion. Restarts or high-volume cache misses can therefore produce metadata-only delete logs.
- `/lock` snapshots the current `@everyone` channel overwrite and then denies `Send Messages`. `/unlock` restores the exact prior overwrite, or removes the temporary overwrite when none existed before.
- Moderation commands also rely on Discord role hierarchy: the bot's role must sit above the member it is moderating.

## Production hardening ideas

For larger communities, the next upgrades should be Discord OAuth2 dashboard login with guild/role authorization, PostgreSQL instead of SQLite, immutable external audit storage, configurable escalation rules, timed mutes/timeouts, case management, and a proper channel-permission snapshot for reversible lockdowns.
