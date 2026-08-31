import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import pymysql
import pymysql.cursors
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DEFAULT_LOG_CHANNEL = os.getenv("DEFAULT_LOG_CHANNEL_ID", "")

st.set_page_config(page_title="Sentinel Control", page_icon="🛡️", layout="wide")


@contextmanager
def connect():
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()


def auth():
    if not PASSWORD:
        st.error("DASHBOARD_PASSWORD is not configured.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.title("🛡️ Sentinel Control")
    st.caption("Secure moderation control plane")
    supplied = st.text_input("Dashboard password", type="password")
    if st.button("Sign in", type="primary", use_container_width=True):
        if hashlib.sha256(supplied.encode()).digest() == hashlib.sha256(PASSWORD.encode()).digest():
            st.session_state.authenticated = True
            st.rerun()
        st.error("Invalid password.")
    st.stop()


def guild_ids():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT guild_id FROM guild_settings ORDER BY guild_id")
            rows = cur.fetchall()
    return [r["guild_id"] for r in rows]


def selected_guild() -> str:
    return st.session_state.get("guild_id", "")


def overview_page():
    guild_id = selected_guild()
    st.header("Overview")
    if not guild_id:
        st.info("Select a server from the sidebar.")
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM warnings WHERE guild_id=%s", (guild_id,))
            warnings_cnt = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM audit_events WHERE guild_id=%s", (guild_id,))
            events_cnt = cur.fetchone()["cnt"]
            cur.execute("SELECT log_channel_id FROM guild_settings WHERE guild_id=%s", (guild_id,))
            row = cur.fetchone()
    log_ch = row["log_channel_id"] if row else ""
    a, b, c = st.columns(3)
    a.metric("Warnings issued", warnings_cnt)
    b.metric("Audit events", events_cnt)
    c.metric("Log channel", f"#{log_ch}" if log_ch else "Not set")
    if not log_ch:
        st.warning("⚠️ No log channel configured — go to **Settings** and enter a channel ID.")


def settings_page():
    guild_id = selected_guild()
    st.header("Guild settings")
    if not guild_id:
        st.info("Select a server from the sidebar.")
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM guild_settings WHERE guild_id=%s", (guild_id,))
            row = cur.fetchone()
    if not row:
        st.error("Guild not found in database.")
        return
    with st.form("settings"):
        log_channel = st.text_input(
            "Log channel ID",
            value=row["log_channel_id"],
            placeholder=DEFAULT_LOG_CHANNEL or "e.g. 1234567890123456789",
            help="ID of the Discord text channel where the bot sends mod logs, deletes, and edits.",
        )
        st.caption("Right-click a channel in Discord → Copy Channel ID (Developer Mode must be on).")
        st.divider()
        st.subheader("Roles")
        r1, r2, r3 = st.columns(3)
        with r1:
            owner_role = st.text_input("Owner role ID", value=row["owner_role_id"] or "", help="Full access. Cannot be restricted.")
        with r2:
            admin_role = st.text_input("Admin role ID", value=row["admin_role_id"] or "", help="Full access. Cannot be restricted.")
        with r3:
            mod_role = st.text_input("Moderator role ID", value=row["mod_role_id"] or "", help="Access gated by the toggle below.")
        mod_enabled = st.toggle("Moderator commands enabled", value=bool(row["mod_commands_enabled"]),
                                help="When off, only Owner and Admin roles can use bot commands.")
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            log_deletes = st.toggle("Log deleted messages", value=bool(row["log_deletes"]))
            log_edits = st.toggle("Log edited messages", value=bool(row["log_edits"]))
        with c2:
            log_moderation = st.toggle("Log moderation actions", value=bool(row["log_moderation"]))
            dm_warnings = st.toggle("DM users when warned", value=bool(row["dm_warnings"]))
        if st.form_submit_button("Save settings", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE guild_settings"
                        " SET log_channel_id=%s, owner_role_id=%s, admin_role_id=%s,"
                        "     mod_role_id=%s, mod_commands_enabled=%s,"
                        "     log_deletes=%s, log_edits=%s,"
                        "     log_moderation=%s, dm_warnings=%s, updated_at=CURRENT_TIMESTAMP"
                        " WHERE guild_id=%s",
                        (
                            log_channel.strip(),
                            owner_role.strip(), admin_role.strip(), mod_role.strip(), int(mod_enabled),
                            int(log_deletes), int(log_edits), int(log_moderation), int(dm_warnings),
                            guild_id,
                        ),
                    )
            st.success("Settings saved.")


def warnings_page():
    guild_id = selected_guild()
    st.header("Warnings")
    if not guild_id:
        st.info("Select a server from the sidebar.")
        return
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT id, user_id, moderator_id, reason, created_at"
            " FROM warnings WHERE guild_id=%s ORDER BY created_at DESC LIMIT 500",
            conn, params=(guild_id,),
        )
    if df.empty:
        st.info("No warnings on record for this server.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True)


def audit_page():
    guild_id = selected_guild()
    st.header("Audit log")
    if not guild_id:
        st.info("Select a server from the sidebar.")
        return
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT id, event_type, actor_id, target_id, channel_id, details, created_at"
            " FROM audit_events WHERE guild_id=%s ORDER BY created_at DESC LIMIT 1000",
            conn, params=(guild_id,),
        )
    if df.empty:
        st.info("No audit events recorded yet for this server.")
        return
    event_types = sorted(df["event_type"].dropna().unique().tolist())
    selected = st.multiselect("Filter by event type", event_types, default=event_types)
    filtered = df[df["event_type"].isin(selected)] if selected else df.iloc[0:0]
    st.dataframe(filtered, use_container_width=True, hide_index=True)


def welcome_page():
    guild_id = selected_guild()
    st.header("Welcome & Goodbye")
    if not guild_id:
        st.info("Select a server from the sidebar.")
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT welcome_channel_id, welcome_message, goodbye_channel_id,"
                " goodbye_message, welcome_enabled, goodbye_enabled"
                " FROM welcome_config WHERE guild_id=%s",
                (guild_id,),
            )
            row = cur.fetchone()
    if not row:
        st.info("Welcome config not yet initialised — start the bot first.")
        return
    with st.form("welcome"):
        st.subheader("Welcome message")
        w_enabled = st.toggle("Enabled", value=bool(row["welcome_enabled"]))
        w_channel = st.text_input("Welcome channel ID", value=row["welcome_channel_id"] or "")
        w_msg = st.text_area(
            "Message", value=row["welcome_message"] or "Welcome {user} to **{server}**!",
            help="Variables: {user} mention, {username} tag, {server} name, {member_count}",
        )
        st.divider()
        st.subheader("Goodbye message")
        g_enabled = st.toggle("Enabled", value=bool(row["goodbye_enabled"]))
        g_channel = st.text_input("Goodbye channel ID", value=row["goodbye_channel_id"] or "")
        g_msg = st.text_area(
            "Message", value=row["goodbye_message"] or "**{username}** has left the server.",
            help="Variables: {user} mention, {username} tag, {server} name, {member_count}",
        )
        if st.form_submit_button("Save", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE welcome_config"
                        " SET welcome_channel_id=%s, welcome_message=%s, goodbye_channel_id=%s,"
                        "     goodbye_message=%s, welcome_enabled=%s, goodbye_enabled=%s,"
                        "     updated_at=CURRENT_TIMESTAMP"
                        " WHERE guild_id=%s",
                        (w_channel.strip(), w_msg, g_channel.strip(), g_msg,
                         int(w_enabled), int(g_enabled), guild_id),
                    )
            st.success("Saved.")


def automod_page():
    guild_id = selected_guild()
    st.header("Automod")
    if not guild_id:
        st.info("Select a server from the sidebar.")
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT enabled, anti_spam_enabled, anti_spam_threshold, anti_spam_interval,"
                " anti_mention_enabled, anti_mention_threshold,"
                " anti_invite_enabled, anti_link_enabled, banned_words, action"
                " FROM automod_config WHERE guild_id=%s",
                (guild_id,),
            )
            row = cur.fetchone()
    if not row:
        st.info("Automod config not yet initialised — start the bot first.")
        return
    with st.form("automod"):
        enabled = st.toggle("Enable automod", value=bool(row["enabled"]))
        action = st.selectbox(
            "Default action when triggered",
            ["warn", "timeout", "kick", "ban"],
            index=["warn", "timeout", "kick", "ban"].index(row["action"] or "warn"),
            help="warn = send warning; timeout = 10min timeout; kick/ban = permanent.",
        )
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            spam_on = st.toggle("Anti-spam", value=bool(row["anti_spam_enabled"]))
            spam_msgs = st.number_input("Messages threshold", min_value=2, max_value=30, value=int(row["anti_spam_threshold"]))
            spam_secs = st.number_input("Within N seconds", min_value=1, max_value=60, value=int(row["anti_spam_interval"]))
            mention_on = st.toggle("Anti mass-mention", value=bool(row["anti_mention_enabled"]))
            mention_threshold = st.number_input("Mention threshold", min_value=2, max_value=20, value=int(row["anti_mention_threshold"]))
        with c2:
            invite_on = st.toggle("Block server invites", value=bool(row["anti_invite_enabled"]))
            link_on = st.toggle("Block all links", value=bool(row["anti_link_enabled"]))
        st.divider()
        banned_words = st.text_area(
            "Banned words (one per line)", value=row["banned_words"] or "",
            help="Case-insensitive whole-word matching.",
        )
        if st.form_submit_button("Save", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE automod_config"
                        " SET enabled=%s, anti_spam_enabled=%s, anti_spam_threshold=%s,"
                        "     anti_spam_interval=%s, anti_mention_enabled=%s, anti_mention_threshold=%s,"
                        "     anti_invite_enabled=%s, anti_link_enabled=%s,"
                        "     banned_words=%s, action=%s, updated_at=CURRENT_TIMESTAMP"
                        " WHERE guild_id=%s",
                        (int(enabled), int(spam_on), int(spam_msgs), int(spam_secs),
                         int(mention_on), int(mention_threshold),
                         int(invite_on), int(link_on),
                         banned_words.strip(), action, guild_id),
                    )
            st.success("Saved.")


def tickets_page():
    guild_id = selected_guild()
    st.header("Ticket system")
    if not guild_id:
        st.info("Select a server from the sidebar.")
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT category_id, log_channel_id, support_role_id, welcome_message"
                " FROM ticket_config WHERE guild_id=%s",
                (guild_id,),
            )
            row = cur.fetchone()
        df = pd.read_sql_query(
            "SELECT id, creator_id, subject, status, created_at"
            " FROM tickets WHERE guild_id=%s ORDER BY created_at DESC LIMIT 100",
            conn, params=(guild_id,),
        )
    if not row:
        st.info("Ticket config not yet initialised — start the bot first.")
        return
    with st.form("tickets"):
        t1, t2 = st.columns(2)
        with t1:
            cat = st.text_input("Category ID", value=row["category_id"] or "", help="Discord category for new ticket channels.")
            log_ch = st.text_input("Log channel ID", value=row["log_channel_id"] or "", help="Channel where ticket opens/closes are logged.")
        with t2:
            support_role = st.text_input("Support role ID", value=row["support_role_id"] or "", help="Role that can see all tickets.")
        welcome_msg = st.text_area("Ticket welcome message", value=row["welcome_message"] or "Support ticket opened. A staff member will be with you shortly.")
        if st.form_submit_button("Save", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ticket_config"
                        " SET category_id=%s, log_channel_id=%s, support_role_id=%s,"
                        "     welcome_message=%s, updated_at=CURRENT_TIMESTAMP"
                        " WHERE guild_id=%s",
                        (cat.strip(), log_ch.strip(), support_role.strip(), welcome_msg, guild_id),
                    )
            st.success("Saved.")
    st.divider()
    st.subheader("Recent tickets")
    if df.empty:
        st.info("No tickets yet.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)




auth()

st.sidebar.title("🛡️ Sentinel Control")
if st.sidebar.button("Sign out", use_container_width=True):
    st.session_state.clear()
    st.rerun()

st.sidebar.divider()

# Server selector — every guild that has used the bot appears here
all_guilds = guild_ids()
default_guild = os.getenv("DISCORD_GUILD_ID", "")
if default_guild and default_guild not in all_guilds:
    all_guilds = [default_guild] + all_guilds

if not all_guilds:
    st.sidebar.info("No servers found yet. Start the bot first.")
else:
    current = st.session_state.get("guild_id", all_guilds[0])
    if current not in all_guilds:
        current = all_guilds[0]
    st.session_state["guild_id"] = st.sidebar.selectbox(
        "Server", all_guilds, index=all_guilds.index(current)
    )

st.sidebar.divider()
st.sidebar.caption("Each server configures its own log channel and toggles in Settings.")

pages = {
    "Control": [
        st.Page(overview_page, title="Overview", icon="📊"),
        st.Page(settings_page, title="Settings", icon="⚙️"),
    ],
    "Logging": [
        st.Page(warnings_page, title="Warnings", icon="⚠️"),
        st.Page(audit_page, title="Audit log", icon="🧾"),
    ],
    "Features": [
        st.Page(welcome_page, title="Welcome / Goodbye", icon="👋"),
        st.Page(automod_page, title="Automod", icon="🤖"),
        st.Page(tickets_page, title="Tickets", icon="🎫"),
    ],
}
pg = st.navigation(pages)
pg.run()

import pymysql.cursors
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")

st.set_page_config(page_title="Sentinel Control", page_icon="🛡️", layout="wide")


@contextmanager
def connect():
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()


def auth():
    if not PASSWORD:
        st.error("DASHBOARD_PASSWORD is not configured.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.title("🛡️ Sentinel Control")
    st.caption("Secure moderation control plane")
    supplied = st.text_input("Dashboard password", type="password")
    if st.button("Sign in", type="primary", use_container_width=True):
        if hashlib.sha256(supplied.encode()).digest() == hashlib.sha256(PASSWORD.encode()).digest():
            st.session_state.authenticated = True
            st.rerun()
        st.error("Invalid password.")
    st.stop()


def ensure_guild(guild_id: str):
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT IGNORE INTO guild_settings(guild_id) VALUES (%s)", (guild_id,))


def guild_ids():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT guild_id FROM guild_settings ORDER BY guild_id")
            rows = cur.fetchall()
    return [r["guild_id"] for r in rows]


def settings_page():
    st.header("Guild settings")
    guilds = guild_ids()
    default_guild = os.getenv("DISCORD_GUILD_ID", "")
    seed = default_guild or (guilds[0] if guilds else "")
    guild_id = st.text_input("Guild ID", value=seed, help="Discord server ID to configure.")
    if not guild_id:
        st.info("Enter a guild ID to begin configuration.")
        return
    ensure_guild(guild_id)
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM guild_settings WHERE guild_id=%s", (guild_id,))
            row = cur.fetchone()
    with st.form("settings"):
        log_channel = st.text_input("Log channel ID", value=row["log_channel_id"], help="Channel that receives moderation and message event embeds.")
        c1, c2 = st.columns(2)
        with c1:
            log_deletes = st.toggle("Log deleted messages", value=bool(row["log_deletes"]))
            log_edits = st.toggle("Log edited messages", value=bool(row["log_edits"]))
        with c2:
            log_moderation = st.toggle("Log moderation actions", value=bool(row["log_moderation"]))
            dm_warnings = st.toggle("DM users when warned", value=bool(row["dm_warnings"]))
        if st.form_submit_button("Save settings", type="primary"):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE guild_settings SET log_channel_id=%s, log_deletes=%s, log_edits=%s, log_moderation=%s, dm_warnings=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
                        (log_channel.strip(), int(log_deletes), int(log_edits), int(log_moderation), int(dm_warnings), guild_id),
                    )
            st.success("Settings saved.")


def warnings_page():
    st.header("Warnings")
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT id, guild_id, user_id, moderator_id, reason, created_at FROM warnings ORDER BY created_at DESC LIMIT 500",
            conn,
        )
    st.dataframe(df, use_container_width=True, hide_index=True)


def audit_page():
    st.header("Audit events")
    with connect() as conn:
        df = pd.read_sql_query(
            "SELECT id, guild_id, event_type, actor_id, target_id, channel_id, details, created_at FROM audit_events ORDER BY created_at DESC LIMIT 1000",
            conn,
        )
    if df.empty:
        st.info("No audit events recorded yet.")
        return
    event_types = sorted(df["event_type"].dropna().unique().tolist())
    selected = st.multiselect("Event types", event_types, default=event_types)
    filtered = df[df["event_type"].isin(selected)] if selected else df.iloc[0:0]
    st.dataframe(filtered, use_container_width=True, hide_index=True)


def overview_page():
    st.header("Overview")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM warnings")
            warnings = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM audit_events")
            events = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) as cnt FROM guild_settings")
            guilds = cur.fetchone()["cnt"]
    a, b, c = st.columns(3)
    a.metric("Configured guilds", guilds)
    b.metric("Warnings", warnings)
    c.metric("Audit events", events)
    st.markdown("Use **Guild settings** to configure logging behavior and the destination log channel. Changes are read by the bot without requiring a dashboard restart.")


auth()
st.sidebar.title("Sentinel Control")
if st.sidebar.button("Sign out"):
    st.session_state.clear()
    st.rerun()

pages = {
    "Control": [
        st.Page(overview_page, title="Overview", icon="📊"),
        st.Page(settings_page, title="Guild settings", icon="⚙️"),
    ],
    "Moderation": [
        st.Page(warnings_page, title="Warnings", icon="⚠️"),
        st.Page(audit_page, title="Audit events", icon="🧾"),
    ],
}
pg = st.navigation(pages)
pg.run()

