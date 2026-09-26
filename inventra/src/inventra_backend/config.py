import os
from functools import lru_cache
from pydantic import BaseModel, Field


class Settings(BaseModel):
    db_path: str = os.environ.get("INVENTRA_DB_PATH", "/data/inventra.db")
    api_port: int = int(os.environ.get("INVENTRA_API_PORT", "8000"))
    ingress_port: int = int(os.environ.get("INVENTRA_INGRESS_PORT", "8099"))
    ingress_proxy_ip: str = os.environ.get("INVENTRA_INGRESS_PROXY_IP", "172.30.32.2")
    pairing_code_ttl_seconds: int = int(os.environ.get("INVENTRA_PAIRING_TTL", "300"))
    bring_todo_entity_id: str = Field(
        default_factory=lambda: os.environ.get("INVENTRA_BRING_TODO_ENTITY_ID", "todo.zuhause")
    )
    bring_reconcile_interval_seconds: int = Field(
        default_factory=lambda: int(os.environ.get("INVENTRA_BRING_RECONCILE_INTERVAL_SECONDS", "600"))
    )
    resolver_source_timeout_seconds: float = Field(
        default_factory=lambda: float(os.environ.get("INVENTRA_RESOLVER_SOURCE_TIMEOUT_SECONDS", "3.0"))
    )
    resolver_global_deadline_seconds: float = Field(
        default_factory=lambda: float(os.environ.get("INVENTRA_RESOLVER_GLOBAL_DEADLINE_SECONDS", "6.0"))
    )
    resolver_cache_found_ttl_seconds: int = Field(
        default_factory=lambda: int(os.environ.get("INVENTRA_RESOLVER_CACHE_FOUND_TTL_SECONDS", str(30 * 24 * 3600)))
    )
    resolver_cache_not_found_ttl_seconds: int = Field(
        default_factory=lambda: int(os.environ.get("INVENTRA_RESOLVER_CACHE_NOT_FOUND_TTL_SECONDS", str(7 * 24 * 3600)))
    )
    resolver_cache_error_ttl_seconds: int = Field(
        default_factory=lambda: int(os.environ.get("INVENTRA_RESOLVER_CACHE_ERROR_TTL_SECONDS", "300"))
    )
    resolver_result_ttl_seconds: int = Field(
        default_factory=lambda: int(os.environ.get("INVENTRA_RESOLVER_RESULT_TTL_SECONDS", "900"))
    )
    resolver_max_concurrent_requests: int = Field(
        default_factory=lambda: int(os.environ.get("INVENTRA_RESOLVER_MAX_CONCURRENT_REQUESTS", "8"))
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test-only: clears the lru_cache so env-var overrides take effect
    between tests."""
    get_settings.cache_clear()
