from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    openai_api_key: str | None = None
    tavily_api_key: str | None = None
    research_model: str | None = None
    worker_model: str | None = None
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
    estimated_input_cost_per_1m_tokens_usd: float = 0.0
    slo_min_run_success_rate: float = 0.95
    slo_max_external_p95_ms: float = 30_000
    slo_max_retry_rate: float = 0.10
    telemetry_retention_days: int = 30
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
