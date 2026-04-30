from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file (backend/.env).
# Fall back to the current working directory if the primary path doesn't exist.
_PRIMARY_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
_ENV_FILE = str(_PRIMARY_ENV) if _PRIMARY_ENV.exists() else ".env"

class Settings(BaseSettings):
    app_name: str = "ApplyForge Backend"
    env: str = "dev"
    secret_key: str = "change-this"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 14
    jwt_algorithm: str = "HS256"

    # Admin bootstrap — the first user registered with this email becomes admin
    admin_email: str = "deeprajchouhan012@gmail.com"
    # If set, admin will be auto-created on first boot (used in production seeding)
    admin_password: str | None = None

    database_url: str = "mysql+pymysql://AF-tst-admin:Password%40123@applyforge-applyforgedb-y53jkg:3306/AF-tst-db"
    cors_origins: str = "http://localhost:3000"

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    ai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    ai_request_timeout_seconds: float = 30.0
    ai_max_retries: int = 2
    ai_retry_backoff_seconds: float = 0.5
    ai_allow_mock_providers: bool = False

    # Google OAuth — set these in .env to enable Google Sign-In
    google_client_id: str | None = None
    google_client_secret: str | None = None

    s3_endpoint_url: str = "http://s3.applyforge-seaweedfs-ff59a1-191-101-80-174.traefik.me"
    s3_access_key: str = "admin"
    s3_secret_key: str = "xnwbtw0csn7ob4pa"
    s3_bucket: str = "applyforge-uploads"
    s3_region: str = "us-east-1"

    # Qdrant vector store — used by RAGService for efficient similarity search
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "applyforge_chunks"
    # Must match the output dimension of the configured embedding model.
    # text-embedding-3-small / ada-002 → 1536  |  text-embedding-3-large → 3072
    # MockEmbeddingProvider (dev/test) → 16
    embedding_dim: int = 1536

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [c.strip() for c in self.cors_origins.split(",") if c.strip()]

    @property
    def ai_api_key_value(self) -> str:
        return self.ai_api_key.get_secret_value() if self.ai_api_key else ""


settings = Settings()

