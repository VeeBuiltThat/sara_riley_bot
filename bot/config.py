import os


class Config:
    def __init__(self, discord_token: str, guild_id: str, database_path: str):
        self.discord_token = discord_token
        self.guild_id = guild_id
        self.database_path = database_path


def load() -> Config:
    token = os.getenv("DISCORD_TOKEN", "")
    guild_id = os.getenv("DISCORD_GUILD_ID", "")
    db_path = os.getenv("DATABASE_PATH", "./data/modbot.db")
    if not token:
        raise ValueError("DISCORD_TOKEN is required")
    return Config(token, guild_id, db_path)
