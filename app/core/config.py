from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PRIMARY_ENV = Path(__file__).resolve().parent.parent.parent / ".env"
_ENV_FILE = str(_PRIMARY_ENV) if _PRIMARY_ENV.exists() else ".env"

class Settings(BaseSettings):
    app_name: str = "ApplyForge Backend"
    env: str = "dev"
    secret_key: str = "change-this"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 14
    jwt_algorithm: str = "HS256"

    admin_email: str = "deeprajchouhan012@gmail.com"
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

    google_client_id: str | None = None
    google_client_secret: str | None = None

    storage_backend: str = "local"
    upload_dir: str = "./storage/uploads"

    s3_endpoint_url: str = "https://applyforge-rustfs-cd53e0-191-101-80-174.traefik.me"
    s3_access_key: str = "rustfsadmin"
    s3_secret_key: str = "vcqdhbkkeq11czol"
    s3_bucket: str = "applyforge-uploads"
    s3_region: str = "us-east-1"

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "applyforge_chunks"
    embedding_dim: int = 1536

    # LangSmith tracing (optional)
    # Set LANGSMITH_API_KEY to enable. Set LANGSMITH_ENABLED=false to disable.
    langsmith_api_key: str | None = None
    langsmith_project: str = "applyforge"
    langsmith_enabled: bool = True

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [c.strip() for c in self.cors_origins.split(",") if c.strip()]

    @property
    def ai_api_key_value(self) -> str:
        return self.ai_api_key.get_secret_value() if self.ai_api_key else ""


settings = Settings()
