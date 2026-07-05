"""Application settings loaded from environment variables.

Mirrors the ``pydantic-settings`` pattern used in ``quant_signals``. Environment
variable names match the House spec (docs/specs) so operators configure one set
of well-known names.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # --- Infrastructure ---
    redis_url: str = Field("redis://localhost:6379/0", validation_alias="QUANT_REDIS_URL")
    database_url: str = Field("", validation_alias="DATABASE_URL")
    api_port: int = Field(8017, validation_alias="API_PORT")
    api_listen_address: str = Field("0.0.0.0", validation_alias="API_LISTEN_ADDRESS")
    max_page_size: int = Field(100, validation_alias="MAX_PAGE_SIZE")
    default_page_size: int = Field(25, validation_alias="DEFAULT_PAGE_SIZE")

    # --- Worker scheduling ---
    poll_interval: int = Field(3600, validation_alias="POLL_INTERVAL")
    heartbeat_ttl: int = Field(300, validation_alias="QP_HEARTBEAT_TTL")
    lock_ttl: int = Field(3600, validation_alias="LOCK_TTL")

    # --- House data source ---
    house_fd_base_url: str = Field(
        "https://disclosures-clerk.house.gov", validation_alias="HOUSE_FD_BASE_URL"
    )
    house_fd_years: str = Field("current", validation_alias="HOUSE_FD_YEARS")
    backfill_years: str = Field("", validation_alias="BACKFILL_YEARS")
    target_filing_types: str = Field("P", validation_alias="TARGET_FILING_TYPES")
    publish_transaction_types: str = Field("purchase", validation_alias="PUBLISH_TRANSACTION_TYPES")
    historical_start_date: str = Field("", validation_alias="HISTORICAL_START_DATE")

    # --- Ollama (vision-capable model REQUIRED, no default) ---
    ollama_url: str = Field("http://localhost:11434", validation_alias="OLLAMA_URL")
    ollama_model: str = Field("", validation_alias="OLLAMA_MODEL")
    ollama_timeout: int = Field(180, validation_alias="OLLAMA_TIMEOUT")

    # --- quant_signals producer ---
    signals_api_url: str = Field("", validation_alias="SIGNALS_API_URL")
    signals_source_name: str = Field("house-disclosures-v1", validation_alias="SIGNALS_SOURCE_NAME")
    signals_timeout: float = Field(30.0, validation_alias="SIGNALS_TIMEOUT")
    signal_market: str = Field("stocks", validation_alias="SIGNAL_MARKET")
    signal_locale: str = Field("us", validation_alias="SIGNAL_LOCALE")
    signal_type: str = Field("watchlist_candidate", validation_alias="SIGNAL_TYPE")
    publish_batch_size: int = Field(50, validation_alias="PUBLISH_BATCH_SIZE")

    # --- HTTP / limits ---
    http_user_agent: str = Field(
        "quant_politicians/0.1 (+https://github.com/mayberryjp/quant_politicians)",
        validation_alias="HTTP_USER_AGENT",
    )
    max_doc_bytes: int = Field(52_428_800, validation_alias="MAX_DOC_BYTES")
    max_doc_pages: int = Field(20, validation_alias="MAX_DOC_PAGES")

    # --- Document retrieval ---
    doc_cache_dir: str = Field("/var/cache/quant_politicians", validation_alias="DOC_CACHE_DIR")
    fetch_batch_size: int = Field(50, validation_alias="FETCH_BATCH_SIZE")
    fetch_max_attempts: int = Field(3, validation_alias="FETCH_MAX_ATTEMPTS")
    fetch_backoff_seconds: float = Field(2.0, validation_alias="FETCH_BACKOFF_SECONDS")

    # --- LLM extraction ---
    extract_batch_size: int = Field(25, validation_alias="EXTRACT_BATCH_SIZE")
    llm_max_attempts: int = Field(3, validation_alias="LLM_MAX_ATTEMPTS")
    render_scale: float = Field(2.0, validation_alias="RENDER_SCALE")

    # --- Senate data source (eFD) ---
    senate_efd_base_url: str = Field("https://efdsearch.senate.gov", validation_alias="SENATE_EFD_BASE_URL")
    senate_report_types: str = Field("", validation_alias="SENATE_REPORT_TYPES")
    senate_filer_types: str = Field("all", validation_alias="SENATE_FILER_TYPES")
    senate_backfill_start_date: str = Field("", validation_alias="SENATE_BACKFILL_START_DATE")
    senate_search_page_size: int = Field(100, validation_alias="SENATE_SEARCH_PAGE_SIZE")
    senate_request_delay: float = Field(2.0, validation_alias="SENATE_REQUEST_DELAY")

    def parsed_target_filing_types(self) -> list[str]:
        return [t.strip().upper() for t in self.target_filing_types.split(",") if t.strip()]

    def parsed_publish_transaction_types(self) -> list[str]:
        return [t.strip().lower() for t in self.publish_transaction_types.split(",") if t.strip()]


settings = Settings()
