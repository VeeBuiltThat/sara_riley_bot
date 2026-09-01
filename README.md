# Sentinel — Discord Moderation Bot + Flask Dashboard

A Discord moderation stack with a Flask control dashboard.

## Included features

- `/kick user [reason]`
- `/ban user [reason]`
- `/warn user reason` with persistent warnings and optional DM
- `/warnings user`
- `/userinfo user`
- `/lock [reason]` and `/unlock`
- Guild-scoped custom prefix commands managed from the dashboard
- Opt-in Ollama casual chat in one configured channel, with mention mode and cooldowns
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
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r bot/requirements.txt -r dashboard/website/requirements.txt
python -m bot.main
```

In another terminal:

```bash
gunicorn --chdir dashboard/website --bind 0.0.0.0:8501 --workers 2 --threads 4 app:app
```

Open `http://localhost:8501`. Configure Discord OAuth and `DASHBOARD_SECRET_KEY` in `.env` before signing in.

## Docker

```bash
cp .env.example .env
docker compose up --build -d
```

The dashboard is exposed on port `8501`. For internet-facing deployments, put it behind HTTPS and an authenticated reverse proxy (Cloudflare Access, Authelia, oauth2-proxy, etc.). The built-in password is a deployment baseline, not an enterprise identity provider.

## Ollama chat

Set `OLLAMA_BASE_URL` in `.env` when Ollama is not available at the default `http://host.docker.internal:11434`. Open **AI chat** in the dashboard, select a Discord channel, and enable it. Chat is off by default, responds only to member messages in that channel, and requires a bot mention by default. The bot sends no provider credentials and does not autonomously post messages.

## Operational notes

- SQLite uses WAL mode and a busy timeout so the Go bot and Streamlit dashboard can safely share the database for this workload.
- Deleted message content can only be logged if DiscordGo had the message cached before deletion. Restarts or high-volume cache misses can therefore produce metadata-only delete logs.
- `/lock` snapshots the current `@everyone` channel overwrite and then denies `Send Messages`. `/unlock` restores the exact prior overwrite, or removes the temporary overwrite when none existed before.
- Moderation commands also rely on Discord role hierarchy: the bot's role must sit above the member it is moderating.

## Production hardening ideas

For larger communities, the next upgrades should be Discord OAuth2 dashboard login with guild/role authorization, PostgreSQL instead of SQLite, immutable external audit storage, configurable escalation rules, timed mutes/timeouts, case management, and a proper channel-permission snapshot for reversible lockdowns.
