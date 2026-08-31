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

st.set_page_config(page_title="Sentinel Control", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """<style>
    [data-testid="stSidebar"] { border-right: 1px solid #e5e7eb; }
    [data-testid="stMetric"] { border: 1px solid #e5e7eb; padding: 1rem; border-radius: 6px; }
    .panel-title { font-size: 1.6rem; font-weight: 650; margin-bottom: .2rem; }
    .panel-subtitle { color: #6b7280; margin-bottom: 1.6rem; }
    </style>""",
    unsafe_allow_html=True,
)


@contextmanager
def connect():
    conn = pymysql.connect(
        host=DB_HOST, port=DB_PORT, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, cursorclass=pymysql.cursors.DictCursor,
        autocommit=True, charset="utf8mb4",
    )
    try:
        yield conn
    finally:
        conn.close()


def page_heading(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="panel-title">{title}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="panel-subtitle">{subtitle}</div>', unsafe_allow_html=True)


def auth() -> None:
    if not PASSWORD:
        st.error("DASHBOARD_PASSWORD is not configured.")
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.title("Sentinel Control")
    st.caption("Server moderation configuration")
    supplied = st.text_input("Dashboard password", type="password")
    if st.button("Sign in", type="primary", use_container_width=True):
        expected = hashlib.sha256(PASSWORD.encode()).digest()
        actual = hashlib.sha256(supplied.encode()).digest()
        if actual == expected:
            st.session_state.authenticated = True
            st.rerun()
        st.error("Invalid password.")
    st.stop()


def guilds() -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT guild_id, server_name FROM guild_settings ORDER BY server_name, guild_id")
            return cur.fetchall()


def selected_guild_id() -> str:
    return st.session_state.get("guild_id", "")


def selected_guild_name() -> str:
    return st.session_state.get("server_name", "Selected server")


def selected_or_empty() -> bool:
    if selected_guild_id():
        return True
    st.info("No server has registered yet. Start the bot in the Discord server first.")
    return False


def overview_page() -> None:
    if not selected_or_empty():
        return
    guild_id = selected_guild_id()
    page_heading(selected_guild_name(), "Configuration and activity overview")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS count FROM warnings WHERE guild_id=%s", (guild_id,))
            warning_count = cur.fetchone()["count"]
            cur.execute("SELECT COUNT(*) AS count FROM audit_events WHERE guild_id=%s", (guild_id,))
            audit_count = cur.fetchone()["count"]
            cur.execute("SELECT log_channel_id FROM guild_settings WHERE guild_id=%s", (guild_id,))
            settings = cur.fetchone()
    columns = st.columns(3)
    columns[0].metric("Warnings issued", warning_count)
    columns[1].metric("Audit events", audit_count)
    columns[2].metric("Log channel", settings["log_channel_id"] or "Not configured")
    if not settings or not settings["log_channel_id"]:
        st.warning("Logging is not configured. Add a log channel in Server settings.")


def settings_page() -> None:
    if not selected_or_empty():
        return
    guild_id = selected_guild_id()
    page_heading("Server settings", "Logging, staff access, and moderation preferences")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM guild_settings WHERE guild_id=%s", (guild_id,))
            row = cur.fetchone()
    if not row:
        st.error("The selected server configuration was not found.")
        return
    with st.form("server_settings"):
        st.subheader("Logging")
        log_channel = st.text_input("Log channel ID", value=row["log_channel_id"] or "", placeholder=DEFAULT_LOG_CHANNEL or "Discord channel ID")
        left, right = st.columns(2)
        with left:
            log_deletes = st.toggle("Log deleted messages", value=bool(row["log_deletes"]))
            log_edits = st.toggle("Log edited messages", value=bool(row["log_edits"]))
        with right:
            log_moderation = st.toggle("Log moderation actions", value=bool(row["log_moderation"]))
            dm_warnings = st.toggle("Send warning direct messages", value=bool(row["dm_warnings"]))
        st.divider()
        st.subheader("Staff roles")
        st.caption("Copy IDs from Discord with Developer Mode enabled. Owner and admin roles always have bot access.")
        owner, admin, moderator = st.columns(3)
        with owner:
            owner_role = st.text_input("Owner role ID", value=row["owner_role_id"] or "")
        with admin:
            admin_role = st.text_input("Admin role ID", value=row["admin_role_id"] or "")
        with moderator:
            mod_role = st.text_input("Moderator role ID", value=row["mod_role_id"] or "")
        mod_enabled = st.toggle("Enable moderator commands", value=bool(row["mod_commands_enabled"]))
        if st.form_submit_button("Save server settings", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE guild_settings SET log_channel_id=%s, log_deletes=%s, log_edits=%s, log_moderation=%s, dm_warnings=%s, owner_role_id=%s, admin_role_id=%s, mod_role_id=%s, mod_commands_enabled=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
                        (log_channel.strip(), int(log_deletes), int(log_edits), int(log_moderation), int(dm_warnings), owner_role.strip(), admin_role.strip(), mod_role.strip(), int(mod_enabled), guild_id),
                    )
            st.success("Server settings saved.")


def welcome_page() -> None:
    if not selected_or_empty():
        return
    guild_id = selected_guild_id()
    page_heading("Welcome and goodbye", "Member join and leave messages")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM welcome_config WHERE guild_id=%s", (guild_id,))
            row = cur.fetchone()
    if not row:
        st.info("This feature will be available after the bot has restarted once.")
        return
    with st.form("welcome_settings"):
        welcome_enabled = st.toggle("Enable welcome message", value=bool(row["welcome_enabled"]))
        welcome_channel = st.text_input("Welcome channel ID", value=row["welcome_channel_id"] or "")
        welcome_message = st.text_area("Welcome message", value=row["welcome_message"], height=120)
        goodbye_enabled = st.toggle("Enable goodbye message", value=bool(row["goodbye_enabled"]))
        goodbye_channel = st.text_input("Goodbye channel ID", value=row["goodbye_channel_id"] or "")
        goodbye_message = st.text_area("Goodbye message", value=row["goodbye_message"], height=120)
        st.caption("Available variables: {user}, {username}, {server}, {member_count}")
        if st.form_submit_button("Save welcome settings", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE welcome_config SET welcome_channel_id=%s, welcome_message=%s, goodbye_channel_id=%s, goodbye_message=%s, welcome_enabled=%s, goodbye_enabled=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
                        (welcome_channel.strip(), welcome_message[:2000], goodbye_channel.strip(), goodbye_message[:2000], int(welcome_enabled), int(goodbye_enabled), guild_id),
                    )
            st.success("Welcome settings saved.")


def automod_page() -> None:
    if not selected_or_empty():
        return
    guild_id = selected_guild_id()
    page_heading("Automod", "Configure automatic message enforcement")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM automod_config WHERE guild_id=%s", (guild_id,))
            row = cur.fetchone()
    if not row:
        st.info("This feature will be available after the bot has restarted once.")
        return
    actions = ["warn", "timeout", "kick", "ban"]
    action = row["action"] if row["action"] in actions else "warn"
    with st.form("automod_settings"):
        enabled = st.toggle("Enable automod", value=bool(row["enabled"]))
        action = st.selectbox("Action", actions, index=actions.index(action))
        left, right = st.columns(2)
        with left:
            spam = st.toggle("Block message spam", value=bool(row["anti_spam_enabled"]))
            threshold = st.number_input("Message threshold", min_value=2, max_value=30, value=int(row["anti_spam_threshold"]))
            interval = st.number_input("Time window in seconds", min_value=1, max_value=60, value=int(row["anti_spam_interval"]))
            mentions = st.toggle("Block mass mentions", value=bool(row["anti_mention_enabled"]))
            mention_threshold = st.number_input("Mention threshold", min_value=2, max_value=20, value=int(row["anti_mention_threshold"]))
        with right:
            invites = st.toggle("Block Discord invites", value=bool(row["anti_invite_enabled"]))
            links = st.toggle("Block all links", value=bool(row["anti_link_enabled"]))
        banned_words = st.text_area("Banned words", value=row["banned_words"] or "", height=150, help="Enter one whole word or phrase per line.")
        if st.form_submit_button("Save automod settings", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE automod_config SET enabled=%s, anti_spam_enabled=%s, anti_spam_threshold=%s, anti_spam_interval=%s, anti_mention_enabled=%s, anti_mention_threshold=%s, anti_invite_enabled=%s, anti_link_enabled=%s, banned_words=%s, action=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
                        (int(enabled), int(spam), int(threshold), int(interval), int(mentions), int(mention_threshold), int(invites), int(links), banned_words.strip()[:4000], action, guild_id),
                    )
            st.success("Automod settings saved.")


def tickets_page() -> None:
    if not selected_or_empty():
        return
    guild_id = selected_guild_id()
    page_heading("Tickets", "Ticket configuration and recent activity")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM ticket_config WHERE guild_id=%s", (guild_id,))
            row = cur.fetchone()
        tickets = pd.read_sql_query("SELECT id, creator_id, subject, status, created_at FROM tickets WHERE guild_id=%s ORDER BY created_at DESC LIMIT 100", conn, params=(guild_id,))
    if not row:
        st.info("This feature will be available after the bot has restarted once.")
        return
    with st.form("ticket_settings"):
        left, right = st.columns(2)
        with left:
            category = st.text_input("Ticket category ID", value=row["category_id"] or "")
            log_channel = st.text_input("Ticket log channel ID", value=row["log_channel_id"] or "")
        with right:
            support_role = st.text_input("Support role ID", value=row["support_role_id"] or "")
        message = st.text_area("Opening message", value=row["welcome_message"], height=100)
        if st.form_submit_button("Save ticket settings", type="primary", use_container_width=True):
            with connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE ticket_config SET category_id=%s, log_channel_id=%s, support_role_id=%s, welcome_message=%s, updated_at=CURRENT_TIMESTAMP WHERE guild_id=%s",
                        (category.strip(), log_channel.strip(), support_role.strip(), message[:2000], guild_id),
                    )
            st.success("Ticket settings saved.")
    st.subheader("Recent tickets")
    if tickets.empty:
        st.info("No tickets have been opened.")
    else:
        st.dataframe(tickets, use_container_width=True, hide_index=True)


def warnings_page() -> None:
    if not selected_or_empty():
        return
    guild_id = selected_guild_id()
    page_heading("Warnings", "Moderation warnings issued in this server")
    with connect() as conn:
        warnings = pd.read_sql_query("SELECT id, user_id, moderator_id, reason, created_at FROM warnings WHERE guild_id=%s ORDER BY created_at DESC LIMIT 500", conn, params=(guild_id,))
    if warnings.empty:
        st.info("No warnings have been issued.")
    else:
        st.dataframe(warnings, use_container_width=True, hide_index=True)


def audit_page() -> None:
    if not selected_or_empty():
        return
    guild_id = selected_guild_id()
    page_heading("Audit log", "Events recorded for this server")
    with connect() as conn:
        events = pd.read_sql_query("SELECT id, event_type, actor_id, target_id, channel_id, details, created_at FROM audit_events WHERE guild_id=%s ORDER BY created_at DESC LIMIT 1000", conn, params=(guild_id,))
    if events.empty:
        st.info("No audit events have been recorded.")
        return
    event_types = sorted(events["event_type"].dropna().unique().tolist())
    active_types = st.multiselect("Event types", event_types, default=event_types)
    st.dataframe(events[events["event_type"].isin(active_types)], use_container_width=True, hide_index=True)


auth()
st.sidebar.title("Sentinel Control")
st.sidebar.caption("Moderation configuration")
if st.sidebar.button("Sign out", use_container_width=True):
    st.session_state.clear()
    st.rerun()

records = guilds()
if records:
    labels = [record["server_name"] or f"Unnamed server ({record['guild_id']})" for record in records]
    current_id = st.session_state.get("guild_id")
    current_index = next((i for i, item in enumerate(records) if item["guild_id"] == current_id), 0)
    choice = st.sidebar.selectbox("Server", range(len(records)), index=current_index, format_func=lambda index: labels[index])
    st.session_state["guild_id"] = records[choice]["guild_id"]
    st.session_state["server_name"] = labels[choice]
else:
    st.sidebar.info("No servers are available yet.")

pages = {
    "Control": [
        st.Page(overview_page, title="Overview"),
        st.Page(settings_page, title="Server settings"),
    ],
    "Configuration": [
        st.Page(welcome_page, title="Welcome and goodbye"),
        st.Page(automod_page, title="Automod"),
        st.Page(tickets_page, title="Tickets"),
    ],
    "Records": [
        st.Page(warnings_page, title="Warnings"),
        st.Page(audit_page, title="Audit log"),
    ],
}
st.navigation(pages).run()
