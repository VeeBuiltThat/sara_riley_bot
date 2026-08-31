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

