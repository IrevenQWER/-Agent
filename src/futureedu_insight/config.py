from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_prefix="APP_",
        extra="ignore",
    )

    env: Literal["development", "test", "production"] = "development"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_path: Path = PROJECT_ROOT / "runtime" / "futureedu.db"
    cases_path: Path = PROJECT_ROOT / "data" / "cases"
    chroma_path: Path = PROJECT_ROOT / "runtime" / "chroma"
    report_prompt_path: Path = PROJECT_ROOT / "prompts" / "report_generation.yaml"
    learning_data_backend: Literal["local", "mcp_stdio"] = "local"

    model_provider: Literal["deterministic", "ollama", "openai"] = "deterministic"
    model_name: str = "demo"
    model_base_url: str = "http://127.0.0.1:11434"
    model_api_key: str = ""
    model_timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    max_report_retries: int = Field(default=2, ge=0, le=5)
    min_case_relevance: float = Field(default=0.35, ge=0, le=1)
    final_case_count: int = Field(default=3, ge=1, le=10)
    profile_rules_version: str = "1.0.0"
    prompt_version: str = "1.0.0"

    data_completeness_weight: float = 0.4
    retrieval_score_weight: float = 0.3
    evidence_quality_weight: float = 0.3

    def ensure_runtime_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.chroma_path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_directories()
    return settings
