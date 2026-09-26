from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = f"sqlite:///{ROOT / 'data' / 'postule.db'}"
    max_profile_bytes: int = Field(default=1_048_576, gt=0)
    max_cv_bytes: int = Field(default=15_000_000, gt=0)
    llm_provider: Literal["vllm", "ollama", "openai"] = "vllm"
    vllm_base_url: str = "http://127.0.0.1:8000/v1"
    ollama_base_url: str = "http://127.0.0.1:11434"
    model_name: str = ""
    llm_timeout: float = Field(default=60.0, gt=0, le=3600)
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_tokens: int = Field(default=512, gt=0)
    gpu_memory_utilization: float = Field(default=0.85, ge=0.1, le=0.99)
    max_model_len: int = Field(default=16_384, gt=0)
    vllm_host: str = "127.0.0.1"
    vllm_port: int = Field(default=8000, ge=1, le=65535)
    company_search_api_url: str = ""
    company_search_api_key: str = ""
    company_public_lists_path: str = ""
    company_analysis_max_tokens: int = Field(default=1800, ge=256, le=8192)
    contact_min_confidence: int = Field(default=80, ge=0, le=100)
    direct_job_application_threshold: int = Field(default=75, ge=0, le=100)
    contact_scan_cache_days: int = Field(default=7, ge=1, le=365)
    contact_scan_timeout: float = Field(default=8.0, gt=0, le=60)
    contact_scan_max_pages: int = Field(default=10, ge=1, le=25)
    direct_application_min_score: int = Field(default=70, ge=0, le=100)
    spontaneous_company_min_score: int = Field(default=50, ge=0, le=100)
    min_contact_confidence: int = Field(default=80, ge=0, le=100)
    candidate_status: Literal["student", "graduated"] = "graduated"
    gmail_enabled: bool = False
    auto_send_enabled: bool = False
    auto_send_on_discovery: bool = False
    email_dry_run: bool = True
    daily_email_limit: int = Field(default=20, ge=1, le=500)
    min_send_interval_seconds: int = Field(default=45, ge=0, le=86400)
    only_verified_contacts: bool = True
    max_emails_per_company: int = Field(default=1, ge=1, le=20)
    gmail_credentials_path: str = str(ROOT / "secrets" / "gmail_credentials.json")
    gmail_token_path: str = str(ROOT / "secrets" / "gmail_token.json")

    @field_validator("vllm_base_url", "ollama_base_url")
    @classmethod
    def validate_local_url(cls, value: str) -> str:
        parts = urlsplit(value)
        try:
            host = parts.hostname
            parts.port  # Force validation of malformed/out-of-range ports.
        except ValueError as exc:
            raise ValueError("L'URL contient un nom d'hôte ou un port invalide.") from exc
        if parts.scheme not in {"http", "https"} or not host:
            raise ValueError("L'URL doit être une URL HTTP(S) absolue avec un nom d'hôte.")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("L'URL du fournisseur ne doit contenir ni identifiants ni paramètres.")
        return value.rstrip("/")

@lru_cache
def get_settings() -> Settings:
    return Settings()
