"""Application configuration loaded from environment variables / .env file."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"], env_file_encoding="utf-8", extra="ignore"
    )

    # Database - MongoDB Atlas connection string
    MONGODB_URI: str = "mongodb://localhost:27017/conversion_workbench"
    MONGODB_DB: str = "conversion_workbench"

    # File storage.
    # On Render the container disk is EPHEMERAL — wiped on every redeploy, which
    # loses uploaded template/dataset files. Attach a Render Persistent Disk
    # mounted at /var/data (or set DATA_DIR) and uploaded files survive deploys.
    DATA_DIR: str = ""
    UPLOAD_DIR: str = "./uploads"
    OUTPUT_DIR: str = "./outputs"

    # Auth
    JWT_SECRET: str = "trinamix-local-dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480

    # Seed admin
    ADMIN_EMAIL: str = "admin@trinamix.com"
    ADMIN_PASSWORD: str = "admin123"
    ADMIN_NAME: str = "Trinamix Admin"

    # AI provider
    AI_PROVIDER: str = "none"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"

    # CORS
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    @property
    def _data_root(self):
        """Persistent storage root, if available: DATA_DIR env, else a mounted
        Render persistent disk at /var/data. None → fall back to local dirs."""
        from pathlib import Path
        if self.DATA_DIR:
            return Path(self.DATA_DIR)
        if Path("/var/data").is_dir():   # Render Persistent Disk default mount
            return Path("/var/data")
        return None

    @property
    def upload_path(self):
        from pathlib import Path
        root = self._data_root
        p = (root / "uploads") if root is not None else Path(self.UPLOAD_DIR)
        p = p.resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_path(self):
        from pathlib import Path
        root = self._data_root
        p = (root / "outputs") if root is not None else Path(self.OUTPUT_DIR)
        p = p.resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
