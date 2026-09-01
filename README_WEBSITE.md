# Sentinel website dashboard — no Streamlit

This folder is a complete replacement for the Streamlit dashboard. It is a conventional Flask + Jinja web application, served by Gunicorn. There is no `streamlit` dependency and no Streamlit runtime.

## What it keeps

It uses the existing Sentinel MySQL tables and fields already used by the bot/dashboard:

- `guild_settings`
- `welcome_config`
- `automod_config`
- `ai_chat_config`
- `ticket_config`
- `tickets`
- `warnings`
- `audit_events`

The Go bot continues doing Discord event processing and moderation. The website handles Discord OAuth, staff authorization, configuration reads/writes, and records views.

## Install into your repository

1. Copy the new `website/` directory into the repository root.
2. Replace `Dockerfile.dashboard` with the supplied file.
3. Replace or merge the supplied `docker-compose.yml`. The dashboard is published on host port `9190` and starts Gunicorn rather than Streamlit.
4. Add the website variables from `.env.example` to your real `.env`.
5. In the Discord Developer Portal, set the OAuth redirect URL to exactly the value of `DISCORD_REDIRECT_URI`.
6. Rebuild: `docker compose up --build -d`.

## Production

For the public dashboard domain, use:

`DISCORD_REDIRECT_URI=https://riley-dashboard.bh-games.com/oauth/callback`

and:

`SESSION_COOKIE_SECURE=true`

Put the Gunicorn port behind Nginx, Caddy, Traefik, or Cloudflare Tunnel and terminate HTTPS there. Do not expose the application directly with an unencrypted public login; map the proxy to the dashboard container's internal port `8501`.

## Secrets

Never put `DISCORD_CLIENT_SECRET`, `DASHBOARD_SECRET_KEY`, database passwords, or the Discord bot token into frontend JavaScript/templates. The supplied website reads them only from server-side environment variables.

The OAuth access token is used during login to verify the Discord user and staff roles, then discarded rather than stored in the browser session.

## Ollama chat

The dashboard configures the per-server AI chat settings but never receives or displays the Ollama URL. Set `OLLAMA_BASE_URL` only in the bot environment; it defaults to `http://host.docker.internal:11434` for Docker Desktop. Enable AI chat, choose one channel, and use mention-only mode by default to keep replies explicitly member initiated.
