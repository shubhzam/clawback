from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    # all settings can be overridden with CLAWBACK_* env vars or a .env file
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CLAWBACK_", extra="ignore")

    storage_dir: Path = BASE_DIR / "storage"
    database_url: str = ""
    portal_root: Path | None = None
    erp_ledger_path: Path | None = None
    policies_dir: Path = BASE_DIR / "policies"

    # llm_provider is "mock" (deterministic fallbacks only) or "anthropic"
    llm_provider: str = "mock"
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_s: float = 30.0

    worker_threads: int = 4
    cors_origins: list[str] = ["http://localhost:3000"]

    # decision thresholds used by the orchestrator
    min_dispute_confidence: float = 0.80
    auto_dispute_max_cents: int = 2_500_000

    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.storage_dir / 'clawback.db'}"

    def resolved_portal_root(self) -> Path:
        return self.portal_root or self.storage_dir / "portals"

    def resolved_erp_ledger(self) -> Path:
        return self.erp_ledger_path or self.storage_dir / "erp" / "ledger.jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
