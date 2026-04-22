from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ApplyForge Backend"
    env: str = "dev"
    secret_key: str = "change-this"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 14
    jwt_algorithm: str = "HS256"

    database_url: str = "mysql+pymysql://AF-tst-admin:Password@123@applyforge-applyforgedb-y53jkg:3306/AF-tst-db"
    cors_origins: str = "http://localhost:3000"

    llm_provider: str = "mock"
    llm_model: str = "mock-llm"
    embedding_provider: str = "mock"
    embedding_model: str = "mock-embed"
    ai_api_key: str | None = None

    s3_endpoint_url: str = "http://s3.applyforge-seaweedfs-ff59a1-191-101-80-174.traefik.me"
    s3_access_key: str = "admin"
    s3_secret_key: str = "xnwbtw0csn7ob4pa"
    s3_bucket: str = "applyforge-uploads"
    s3_region: str = "us-east-1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [c.strip() for c in self.cors_origins.split(",") if c.strip()]


settings = Settings()
