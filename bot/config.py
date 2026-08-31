import os


class Config:
    def __init__(self, discord_token: str, guild_id: str, db_host: str, db_port: int, db_name: str, db_user: str, db_password: str):
        self.discord_token = discord_token
        self.guild_id = guild_id
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password


def load() -> Config:
    token = os.getenv("DISCORD_TOKEN", "")
    guild_id = os.getenv("DISCORD_GUILD_ID", "")
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = int(os.getenv("DB_PORT", "3306"))
    db_name = os.getenv("DB_NAME", "")
    db_user = os.getenv("DB_USER", "")
    db_password = os.getenv("DB_PASSWORD", "")
    if not token:
        raise ValueError("DISCORD_TOKEN is required")
    if not db_name:
        raise ValueError("DB_NAME is required")
    return Config(token, guild_id, db_host, db_port, db_name, db_user, db_password)
