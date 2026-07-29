from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    llm_provider: str = "openai"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    openai_api_key: str | None = None
    tavily_api_key: str | None = None
    research_model: str | None = None
    worker_model: str | None = None
    verifier_model: str | None = None
    writer_model: str | None = None

    database_url: str = (
        "postgresql://research:research@localhost:54321/research"
    )

    max_parallel_researchers: int = 3
    max_parallel_verifiers: int = 5
    external_max_attempts: int = 3
    retry_min_wait_seconds: float = 1.0
    retry_max_wait_seconds: float = 10.0
    max_external_requests: int = 100
    max_sources: int = 50
    max_claims: int = 100
    max_tokens: int = 200_000
    max_run_seconds: int = 3_600
    max_input_file_bytes: int = Field(
        default=5 * 1024 * 1024,
        ge=1_024,
        le=25 * 1024 * 1024,
    )
    max_input_text_bytes: int = Field(
        default=100_000,
        ge=1_000,
        le=1_000_000,
    )
    max_input_materials: int = Field(
        default=20,
        ge=1,
        le=100,
    )
    estimated_input_cost_per_1m_tokens_usd: float = 0.0
    slo_min_run_success_rate: float = 0.95
    slo_max_external_p95_ms: float = 30_000
    slo_max_retry_rate: float = 0.10
    telemetry_retention_days: int = 30
    log_level: str = "INFO"
    session_cookie_name: str = "dr_session"
    csrf_cookie_name: str = "dr_csrf"
    session_lifetime_days: int = Field(
        default=30,
        ge=1,
        le=365,
    )
    session_cookie_secure: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
