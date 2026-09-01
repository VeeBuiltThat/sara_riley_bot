import os
import re
import secrets
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

import pymysql
import pymysql.cursors
import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DEFAULT_LOG_CHANNEL = os.getenv("DEFAULT_LOG_CHANNEL_ID", "")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "")
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "")
CUSTOM_COMMAND_TRIGGER_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
OLLAMA_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
DISCORD_SNOWFLAKE_RE = re.compile(r"^\d{15,22}$")

if not DASHBOARD_SECRET_KEY:
    raise RuntimeError("DASHBOARD_SECRET_KEY is required")

app = Flask(__name__)
app.secret_key = DASHBOARD_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=6),
)


@contextmanager
def connect():
    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()


def fetch_one(sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


def fetch_all(sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def execute(sql, params=()):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def discord_api(path, token):
    response = requests.get(
        f"https://discord.com/api/v10{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("landing"))
        return view(*args, **kwargs)

    return wrapped


def csrf_token():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token


def verify_csrf():
    supplied = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        abort(400, "Invalid CSRF token")


def bool_form(name):
    return 1 if request.form.get(name) == "on" else 0


def bounded_form_int(name, default, minimum, maximum):
    try:
        value = int(request.form.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def sanitized_discord_ids(name):
    ids = re.findall(r"\d{15,22}", request.form.get(name, "")[:1000])
    return ",".join(dict.fromkeys(ids))[:1000]


def allowed_guild_ids():
    return set(session.get("allowed_guild_ids", []))


def current_guild():
    allowed = allowed_guild_ids()
    guild_id = session.get("guild_id")
    if guild_id not in allowed:
        guild_id = next(iter(allowed), None)
        if guild_id:
            session["guild_id"] = guild_id
    if not guild_id:
        return None
    return fetch_one(
        "SELECT guild_id, server_name, log_channel_id FROM guild_settings WHERE guild_id=%s",
        (guild_id,),
    )


def guild_context():
    ids = list(allowed_guild_ids())
    if not ids:
        return [], None
    placeholders = ",".join(["%s"] * len(ids))
    guilds = fetch_all(
        f"SELECT guild_id, server_name FROM guild_settings WHERE guild_id IN ({placeholders}) ORDER BY server_name, guild_id",
        tuple(ids),
    )
    current = current_guild()
    return guilds, current


def dashboard_render(template, *, active, **kwargs):
    guilds, guild = guild_context()
    if not guild:
        return render_template(
            "empty.html",
            active=active,
            guilds=guilds,
            guild=None,
            user=session.get("user"),
        )
    return render_template(
        template,
        active=active,
        guilds=guilds,
        guild=guild,
        user=session.get("user"),
        **kwargs,
    )


@app.get("/")
def landing():
    if session.get("user"):
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.get("/login")
def login():
    if not DISCORD_CLIENT_ID or not DISCORD_CLIENT_SECRET or not DISCORD_REDIRECT_URI:
        return render_template("config_error.html"), 500
    state = secrets.token_urlsafe(32)
    session.clear()
    session["oauth_state"] = state
    query = urlencode(
        {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "response_type": "code",
            "scope": "identify guilds guilds.members.read",
            "state": state,
        }
    )
    return redirect(f"https://discord.com/oauth2/authorize?{query}")


@app.get("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.pop("oauth_state", None)
    if (
        not code
        or not state
        or not expected_state
        or not secrets.compare_digest(state, expected_state)
    ):
        flash("Discord sign-in could not be verified. Please try again.", "error")
        return redirect(url_for("landing"))

    response = requests.post(
        "https://discord.com/api/v10/oauth2/token",
        data={
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": DISCORD_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    if not response.ok:
        flash("Discord sign-in failed. Please try again.", "error")
        return redirect(url_for("landing"))

    token = response.json().get("access_token")
    try:
        user = discord_api("/users/@me", token)
        discord_guilds = {g["id"]: g for g in discord_api("/users/@me/guilds", token)}
        records = fetch_all(
            "SELECT guild_id, server_name, owner_role_id, admin_role_id, mod_role_id FROM guild_settings"
        )
    except (requests.RequestException, pymysql.MySQLError):
        flash("We could not load your Discord servers right now.", "error")
        return redirect(url_for("landing"))

    allowed = []
    for record in records:
        discord_guild = discord_guilds.get(record["guild_id"])
        if not discord_guild:
            continue
        if discord_guild.get("owner"):
            allowed.append(record["guild_id"])
            continue
        try:
            member = discord_api(
                f"/users/@me/guilds/{record['guild_id']}/member", token
            )
        except requests.RequestException:
            continue
        staff_roles = {
            role
            for role in (
                record["owner_role_id"],
                record["admin_role_id"],
                record["mod_role_id"],
            )
            if role
        }
        if staff_roles.intersection(member.get("roles", [])):
            allowed.append(record["guild_id"])

    session.clear()
    session.permanent = True
    avatar_hash = user.get("avatar")
    avatar_url = None
    if avatar_hash:
        avatar_format = "gif" if avatar_hash.startswith("a_") else "png"
        avatar_url = f"https://cdn.discordapp.com/avatars/{user['id']}/{avatar_hash}.{avatar_format}?size=128"
    session["user"] = {
        "id": user["id"],
        "username": user.get("global_name") or user.get("username", "Discord user"),
        "handle": user.get("username", "Discord user"),
        "avatar_url": avatar_url,
    }
    session["allowed_guild_ids"] = allowed
    session["csrf_token"] = secrets.token_urlsafe(32)
    if allowed:
        session["guild_id"] = allowed[0]
    return redirect(url_for("dashboard"))


@app.post("/logout")
@login_required
def logout():
    verify_csrf()
    session.clear()
    return redirect(url_for("landing"))


@app.post("/guild/select")
@login_required
def select_guild():
    verify_csrf()
    guild_id = request.form.get("guild_id", "")
    if guild_id not in allowed_guild_ids():
        abort(403)
    session["guild_id"] = guild_id
    return redirect(request.form.get("next") or url_for("dashboard"))


@app.get("/dashboard")
@login_required
def dashboard():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="overview")
    guild_id = guild["guild_id"]
    warning_count = fetch_one(
        "SELECT COUNT(*) AS count FROM warnings WHERE guild_id=%s", (guild_id,)
    )["count"]
    audit_count = fetch_one(
        "SELECT COUNT(*) AS count FROM audit_events WHERE guild_id=%s", (guild_id,)
    )["count"]
    ticket_count = fetch_one(
        "SELECT COUNT(*) AS count FROM tickets WHERE guild_id=%s", (guild_id,)
    )["count"]
    recent = fetch_all(
        "SELECT event_type, actor_id, target_id, details, created_at FROM audit_events WHERE guild_id=%s ORDER BY created_at DESC LIMIT 8",
        (guild_id,),
    )
    return dashboard_render(
        "dashboard.html",
        active="overview",
        warning_count=warning_count,
        audit_count=audit_count,
        ticket_count=ticket_count,
        recent=recent,
    )


@app.route("/dashboard/settings", methods=["GET", "POST"])
@login_required
def settings():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="settings")
    guild_id = guild["guild_id"]
    if request.method == "POST":
        verify_csrf()
        execute(
            "UPDATE guild_settings SET log_channel_id=%s, log_deletes=%s, log_edits=%s, log_moderation=%s, dm_warnings=%s, owner_role_id=%s, admin_role_id=%s, mod_role_id=%s, mod_commands_enabled=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
            (
                request.form.get("log_channel_id", "").strip(),
                bool_form("log_deletes"),
                bool_form("log_edits"),
                bool_form("log_moderation"),
                bool_form("dm_warnings"),
                request.form.get("owner_role_id", "").strip(),
                request.form.get("admin_role_id", "").strip(),
                request.form.get("mod_role_id", "").strip(),
                bool_form("mod_commands_enabled"),
                guild_id,
            ),
        )
        flash("Server settings saved.", "success")
        return redirect(url_for("settings"))
    row = fetch_one("SELECT * FROM guild_settings WHERE guild_id=%s", (guild_id,))
    return dashboard_render(
        "settings.html",
        active="settings",
        row=row,
        default_log_channel=DEFAULT_LOG_CHANNEL,
    )


@app.route("/dashboard/welcome", methods=["GET", "POST"])
@login_required
def welcome():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="welcome")
    guild_id = guild["guild_id"]
    row = fetch_one("SELECT * FROM welcome_config WHERE guild_id=%s", (guild_id,))
    if request.method == "POST" and row:
        verify_csrf()
        execute(
            "UPDATE welcome_config SET welcome_channel_id=%s, welcome_message=%s, goodbye_channel_id=%s, goodbye_message=%s, welcome_enabled=%s, goodbye_enabled=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
            (
                request.form.get("welcome_channel_id", "").strip(),
                request.form.get("welcome_message", "")[:2000],
                request.form.get("goodbye_channel_id", "").strip(),
                request.form.get("goodbye_message", "")[:2000],
                bool_form("welcome_enabled"),
                bool_form("goodbye_enabled"),
                guild_id,
            ),
        )
        flash("Welcome settings saved.", "success")
        return redirect(url_for("welcome"))
    return dashboard_render("welcome.html", active="welcome", row=row)


@app.route("/dashboard/automod", methods=["GET", "POST"])
@login_required
def automod():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="automod")
    guild_id = guild["guild_id"]
    row = fetch_one("SELECT * FROM automod_config WHERE guild_id=%s", (guild_id,))
    if request.method == "POST" and row:
        verify_csrf()
        action = request.form.get("action", "warn")
        if action not in {"warn", "timeout", "kick", "ban"}:
            action = "warn"
        threshold = bounded_form_int("anti_spam_threshold", 5, 2, 30)
        interval = bounded_form_int("anti_spam_interval", 10, 1, 60)
        mention_threshold = bounded_form_int("anti_mention_threshold", 5, 2, 20)
        caps_threshold = bounded_form_int("anti_caps_threshold", 70, 50, 100)
        emoji_threshold = bounded_form_int("anti_emoji_threshold", 10, 1, 50)
        execute(
            "UPDATE automod_config SET enabled=%s, anti_spam_enabled=%s, anti_spam_threshold=%s, anti_spam_interval=%s, anti_mention_enabled=%s, anti_mention_threshold=%s, anti_invite_enabled=%s, anti_link_enabled=%s, exempt_role_ids=%s, exempt_channel_ids=%s, anti_caps_enabled=%s, anti_caps_threshold=%s, anti_emoji_enabled=%s, anti_emoji_threshold=%s, banned_words=%s, action=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
            (
                bool_form("enabled"),
                bool_form("anti_spam_enabled"),
                threshold,
                interval,
                bool_form("anti_mention_enabled"),
                mention_threshold,
                bool_form("anti_invite_enabled"),
                bool_form("anti_link_enabled"),
                sanitized_discord_ids("exempt_role_ids"),
                sanitized_discord_ids("exempt_channel_ids"),
                bool_form("anti_caps_enabled"),
                caps_threshold,
                bool_form("anti_emoji_enabled"),
                emoji_threshold,
                request.form.get("banned_words", "").strip()[:4000],
                action,
                guild_id,
            ),
        )
        flash("Automod settings saved.", "success")
        return redirect(url_for("automod"))
    return dashboard_render("automod.html", active="automod", row=row)


@app.route("/dashboard/ai-chat", methods=["GET", "POST"])
@login_required
def ai_chat():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="ai_chat")
    guild_id = guild["guild_id"]
    row = fetch_one("SELECT * FROM ai_chat_config WHERE guild_id=%s", (guild_id,))
    if request.method == "POST" and row:
        verify_csrf()
        channel_id = request.form.get("channel_id", "").strip()
        model = request.form.get("model", "").strip()
        system_prompt = request.form.get("system_prompt", "").strip()
        staff_memory = request.form.get("staff_memory", "").strip()[:4000]
        if channel_id and not DISCORD_SNOWFLAKE_RE.fullmatch(channel_id):
            flash("Channel ID must be empty or a valid Discord snowflake.", "error")
        elif not OLLAMA_MODEL_RE.fullmatch(model):
            flash("Model must be 1-100 letters, numbers, dots, underscores, colons, slashes, or hyphens.", "error")
        elif not 1 <= len(system_prompt) <= 2000:
            flash("System prompt must contain 1-2000 characters.", "error")
        else:
            execute(
                "UPDATE ai_chat_config SET enabled=%s, channel_id=%s, mention_only=%s, model=%s, system_prompt=%s, staff_memory=%s, user_cooldown_seconds=%s, channel_cooldown_seconds=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
                (
                    bool_form("enabled"), channel_id, bool_form("mention_only"), model, system_prompt, staff_memory,
                    bounded_form_int("user_cooldown_seconds", 30, 5, 3600),
                    bounded_form_int("channel_cooldown_seconds", 8, 3, 600), guild_id,
                ),
            )
            flash("AI chat settings saved.", "success")
            return redirect(url_for("ai_chat"))
    return dashboard_render("ai_chat.html", active="ai_chat", row=row)


@app.route("/dashboard/tickets", methods=["GET", "POST"])
@login_required
def tickets():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="tickets")
    guild_id = guild["guild_id"]
    row = fetch_one("SELECT * FROM ticket_config WHERE guild_id=%s", (guild_id,))
    if request.method == "POST" and row:
        verify_csrf()
        execute(
            "UPDATE ticket_config SET category_id=%s, log_channel_id=%s, support_role_id=%s, welcome_message=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
            (
                request.form.get("category_id", "").strip(),
                request.form.get("log_channel_id", "").strip(),
                request.form.get("support_role_id", "").strip(),
                request.form.get("welcome_message", "")[:2000],
                guild_id,
            ),
        )
        flash("Ticket settings saved.", "success")
        return redirect(url_for("tickets"))
    ticket_rows = fetch_all(
        "SELECT id, creator_id, subject, status, created_at FROM tickets WHERE guild_id=%s ORDER BY created_at DESC LIMIT 100",
        (guild_id,),
    )
    return dashboard_render(
        "tickets.html", active="tickets", row=row, tickets=ticket_rows
    )


@app.route("/dashboard/custom-commands", methods=["GET", "POST"])
@login_required
def custom_commands():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="custom_commands")
    guild_id = guild["guild_id"]
    if request.method == "POST":
        verify_csrf()
        action = request.form.get("action", "")
        if action == "create":
            trigger = request.form.get("trigger", "").strip()
            response = request.form.get("response", "").strip()
            if not CUSTOM_COMMAND_TRIGGER_RE.fullmatch(trigger):
                flash("Trigger must contain 1-32 lowercase letters, numbers, hyphens, or underscores.", "error")
            elif not 1 <= len(response) <= 2000:
                flash("Response must contain 1-2000 characters.", "error")
            else:
                try:
                    execute(
                        "INSERT INTO custom_commands (guild_id, `trigger`, response, enabled) VALUES (%s, %s, %s, %s)",
                        (guild_id, trigger, response, bool_form("enabled")),
                    )
                except pymysql.err.IntegrityError:
                    flash("That trigger already exists for this server.", "error")
                else:
                    flash(f"Custom command !{trigger} created.", "success")
        elif action in {"enabled", "delete"}:
            command_id = request.form.get("command_id", "")
            if not command_id.isdigit():
                abort(400, "Invalid custom command")
            if action == "enabled":
                execute(
                    "UPDATE custom_commands SET enabled=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s AND guild_id=%s",
                    (request.form.get("enabled") == "1", int(command_id), guild_id),
                )
                flash("Custom command status updated.", "success")
            else:
                execute(
                    "DELETE FROM custom_commands WHERE id=%s AND guild_id=%s",
                    (int(command_id), guild_id),
                )
                flash("Custom command deleted.", "success")
        else:
            abort(400, "Unknown custom command action")
        return redirect(url_for("custom_commands"))
    rows = fetch_all(
        "SELECT id, `trigger`, response, enabled, created_at, updated_at FROM custom_commands WHERE guild_id=%s ORDER BY `trigger`",
        (guild_id,),
    )
    return dashboard_render("custom_commands.html", active="custom_commands", rows=rows)


@app.get("/dashboard/warnings")
@login_required
def warnings():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="warnings")
    rows = fetch_all(
        "SELECT id, user_id, moderator_id, reason, created_at FROM warnings WHERE guild_id=%s ORDER BY created_at DESC LIMIT 500",
        (guild["guild_id"],),
    )
    return dashboard_render("warnings.html", active="warnings", rows=rows)


@app.get("/dashboard/audit")
@login_required
def audit():
    guild = current_guild()
    if not guild:
        return dashboard_render("empty.html", active="audit")
    event_type = request.args.get("type", "").strip()
    all_types = fetch_all(
        "SELECT DISTINCT event_type FROM audit_events WHERE guild_id=%s AND event_type IS NOT NULL ORDER BY event_type",
        (guild["guild_id"],),
    )
    if event_type:
        rows = fetch_all(
            "SELECT id, event_type, actor_id, target_id, channel_id, details, created_at FROM audit_events WHERE guild_id=%s AND event_type=%s ORDER BY created_at DESC LIMIT 1000",
            (guild["guild_id"], event_type),
        )
    else:
        rows = fetch_all(
            "SELECT id, event_type, actor_id, target_id, channel_id, details, created_at FROM audit_events WHERE guild_id=%s ORDER BY created_at DESC LIMIT 1000",
            (guild["guild_id"],),
        )
    return dashboard_render(
        "audit.html",
        active="audit",
        rows=rows,
        event_types=[r["event_type"] for r in all_types],
        selected_type=event_type,
    )


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8501, debug=True)
