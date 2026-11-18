from pathlib import Path
from pydantic import SecretStr, model_validator
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
    # Key for the recruiter API's opaque public ids (DB ints are never exposed
    # in URLs/payloads). Falls back to a key derived from SECRET_KEY. Treat as
    # permanent: changing it changes every recruiter URL (old links 404).
    recruiter_id_key: SecretStr | None = None

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

    # S3-compatible storage. No defaults — must be supplied via env when S3 is in use.
    # Validated below in `_require_s3_settings_when_used`.
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: SecretStr | None = None
    s3_bucket: str | None = None

    # Adzuna Jobs API (optional — enables Adzuna as a crawler source)
    adzuna_app_id: str | None = None
    adzuna_app_key: str | None = None
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

    # Recruiter → consumer provisioning bridge (Section 5). Left unset in the
    # integrated deployment (the bridge then calls the provisioning service
    # in-process). Set these only if the recruiter platform runs as a separate
    # service that must reach this backend over HTTP.
    applyforge_provisioning_url: str | None = None
    applyforge_provisioning_key: SecretStr | None = None

    # ── Stripe billing for recruiter agencies (Phase 5.4) ──
    # Billing is disabled unless STRIPE_SECRET_KEY is set. Each plan has a price
    # for both models — flat (fixed monthly) and per-seat (quantity = seats).
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_price_pro_flat: str | None = None
    stripe_price_pro_seat: str | None = None
    stripe_price_enterprise_flat: str | None = None
    stripe_price_enterprise_seat: str | None = None
    billing_success_url: str = "https://recruiter.applyforge.co.uk/team?billing=success"
    billing_cancel_url: str = "https://recruiter.applyforge.co.uk/team?billing=cancel"
    billing_portal_return_url: str = "https://recruiter.applyforge.co.uk/team"

    # ── Self-serve onboarding (Phase 5.5) ──
    # When False (default) signups create a *pending* agency the operator must
    # approve before its owner can log in. Set True for fully open self-serve.
    recruiter_signup_open: bool = False
    # Public base URL of the recruiter app — used to build invite/claim links.
    recruiter_app_url: str = "https://recruiter.applyforge.co.uk"

    # ── Partner API (recruiter integrations) ──
    # Pepper for API-key HMACs. Falls back to a key derived from SECRET_KEY;
    # set it explicitly in prod so rotating SECRET_KEY doesn't revoke every key.
    partner_key_pepper: SecretStr | None = None
    # Default requests/minute per key (operator can override per agency/key).
    partner_rate_limit_secret: int = 120
    partner_rate_limit_publishable: int = 30
    # Per client-IP cap on public application submissions (per hour).
    partner_apply_per_ip_per_hour: int = 20
    # Optional shared limiter store; in-process limiter is used when unset.
    partner_rate_limit_redis_url: str | None = None
    # Cloudflare Turnstile secret. When set, publishable-key application
    # submissions must include a valid `captcha_token`.
    partner_turnstile_secret: SecretStr | None = None
    # "inline" runs CV parse/score + webhook delivery in-process after the
    # response (dev/small deploys); "celery" hands them to the worker.
    partner_jobs_backend: str = "inline"
    # Webhook targets must be https + public IPs. Only for local testing.
    partner_webhook_allow_insecure: bool = False
    partner_log_retention_days: int = 30
    # Requests per IP per minute before a key is even looked up (all keys, valid or not).
    partner_preauth_per_ip_per_minute: int = 600
    # Max request body for partner endpoints (CV ≤10 MB + form fields).
    partner_max_body_bytes: int = 11 * 1024 * 1024
    # How many reverse proxies we sit behind (X-Forwarded-For entries from the
    # right that we trust). 1 = Container Apps ingress; 0 = no proxy.
    partner_trusted_proxy_hops: int = 1

    # ── Email (Resend) ──
    # EMAIL_PROVIDER=resend sends for real; "console" logs only (dev/tests).
    email_provider: str = "console"
    resend_api_key: SecretStr | None = None
    resend_webhook_secret: SecretStr | None = None
    resend_api_base: str = "https://api.resend.com"
    email_domain: str = "applyforge.co.uk"
    email_from_consumer: str = "ApplyForge <no-reply@applyforge.co.uk>"
    email_from_recruiter: str = "ApplyForge Recruiter <no-reply@applyforge.co.uk>"
    # Local part for mail sent on an agency's behalf: "<Agency> via ApplyForge <notifications@…>"
    email_external_from_local: str = "notifications"
    email_reply_to: str = "support@applyforge.co.uk"
    # Operator mailboxes shown in Admin → Mail (comma-separated). Anything else
    # @email_domain that Resend receives lands in info@.
    email_mailboxes: str = "support@applyforge.co.uk,info@applyforge.co.uk"
    email_signature_support: str = "ApplyForge Support\nsupport@applyforge.co.uk"
    email_signature_info: str = "ApplyForge\ninfo@applyforge.co.uk"
    # "sync" (tests) | "inline" (thread pool, dev) | "celery" (prod worker)
    email_jobs_backend: str = "inline"
    # Max lifecycle (tips/nudge) emails per person per 7 days. 0 = no cap.
    email_lifecycle_weekly_cap: int = 2
    # Footer legal line (registered name · address · company number). Leave blank
    # until the company is registered — the footer simply omits it.
    email_company_legal: str = ""
    # Public URL of this API, for RFC 8058 one-click unsubscribe. Optional.
    api_public_url: str | None = None
    # Public base URL of the consumer app (links in candidate emails).
    consumer_app_url: str = "https://applyforge.co.uk"

    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore")

    @property
    def resend_api_key_value(self) -> str:
        return self.resend_api_key.get_secret_value() if self.resend_api_key else ""

    @property
    def resend_webhook_secret_value(self) -> str:
        return self.resend_webhook_secret.get_secret_value() if self.resend_webhook_secret else ""

    @property
    def stripe_secret_key_value(self) -> str:
        return self.stripe_secret_key.get_secret_value() if self.stripe_secret_key else ""

    @property
    def stripe_webhook_secret_value(self) -> str:
        return self.stripe_webhook_secret.get_secret_value() if self.stripe_webhook_secret else ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [c.strip() for c in self.cors_origins.split(",") if c.strip()]

    @property
    def ai_api_key_value(self) -> str:
        return self.ai_api_key.get_secret_value() if self.ai_api_key else ""

    @property
    def s3_secret_key_value(self) -> str:
        return self.s3_secret_key.get_secret_value() if self.s3_secret_key else ""

    @property
    def uses_s3_storage(self) -> bool:
        """True when all S3 credentials are present (credential-driven, not env-driven).

        The storage service itself uses this same logic: S3 is used whenever
        the four required vars are all set, regardless of ENV or STORAGE_BACKEND.
        """
        return all([
            self.s3_endpoint_url,
            self.s3_access_key,
            self.s3_secret_key,
            self.s3_bucket,
        ])

    @model_validator(mode="after")
    def _require_s3_settings_when_used(self) -> "Settings":
        # No-op: validation is now credential-driven (uses_s3_storage above).
        # The storage service will log a warning and fall back to local if
        # credentials are incomplete. No startup crash needed.
        return self


settings = Settings()
