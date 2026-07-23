"""Central configuration (12-factor: everything from env, validated once)."""
from __future__ import annotations

from functools import lru_cache

try:
    from pydantic_settings import BaseSettings
except ImportError:  # pydantic v1 fallback
    from pydantic import BaseSettings  # type: ignore


class Settings(BaseSettings):
    service_name: str = "hermes-data-api"
    version: str = "0.1.0"
    env: str = "dev"                      # dev | staging | prod
    log_level: str = "INFO"
    log_json: bool = True

    # storage
    database_url: str = "sqlite:///./hermes_data.db"

    # auth / rate limits (per API key, per minute)
    api_key_header: str = "Authorization"     # "Bearer <key>"
    rate_limit_per_min: int = 500
    rate_limit_burst: int = 60

    # upstream adapter slots — our OWN resources. Licensed feeds (OPRA options tape,
    # dark pool) plug in here ONLY if we obtain the license; empty = endpoint 501s
    # honestly rather than fabricating data.
    hl_base_url: str = "https://api.hyperliquid.xyz"
    options_flow_upstream: str = ""           # licensed feed URL if/when acquired

    class Config:
        env_prefix = "HERMES_"
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
