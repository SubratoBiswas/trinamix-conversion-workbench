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

    # File storage
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
    def upload_path(self):
        from pathlib import Path
        p = Path(self.UPLOAD_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_path(self):
        from pathlib import Path
        p = Path(self.OUTPUT_DIR).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
