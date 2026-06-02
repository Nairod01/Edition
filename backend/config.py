import os
from pathlib import Path
from pydantic_settings import BaseSettings

# Racine du projet = deux niveaux au-dessus de ce fichier (backend/config.py → Edition/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = str(_PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str
    DATABASE_URL: str = "sqlite:///./edition_corrector.db"
    UPLOAD_DIR: str = "./uploads"
    OUTPUT_DIR: str = "./outputs"
    MAX_FILE_SIZE_MB: int = 50
    # Claude models — Sonnet for Pass 2 / fact-check, Haiku for Pass 1a / 1b
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_FAST_MODEL: str = "claude-haiku-4-5-20251001"
    # Claude pricing (USD per 1M tokens)
    CLAUDE_INPUT_PRICE: float = 3.0
    CLAUDE_OUTPUT_PRICE: float = 15.0

    # Auth — set JWT_SECRET to a long random string in production (.env)
    JWT_SECRET: str = "CHANGE_ME_set_JWT_SECRET_in_env_file"
    # Initial admin account — set both to auto-create on first startup
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None

    # Credits — default monthly limit per user in USD (~10€)
    DEFAULT_MONTHLY_LIMIT_USD: float = 11.0
    EUR_PER_USD: float = 0.92  # approximate — for display only

    # CORS — comma-separated list of allowed origins.
    # For local dev the default covers the Next.js dev server.
    # In production set ALLOWED_ORIGINS to your actual domain(s).
    ALLOWED_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Rate limiting — requests per minute per IP on upload/confirm endpoints.
    # Set RATE_LIMIT_ENABLED=false to disable during local dev if slowdown-free iteration is needed.
    RATE_LIMIT_ENABLED: bool = True
    UPLOAD_RATE_LIMIT: str = "10/minute"   # e.g. "10/minute", "100/hour"
    CONFIRM_RATE_LIMIT: str = "20/minute"

    class Config:
        env_file = _ENV_FILE
        extra = "ignore"   # variables inconnues dans .env ignorées silencieusement

    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()

# Ensure directories exist
Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
Path(settings.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
