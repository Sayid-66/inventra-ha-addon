import os

from inventra_backend.config import Settings, get_settings, reset_settings_cache


def test_bring_settings_have_spec_defaults(monkeypatch):
    monkeypatch.delenv("INVENTRA_BRING_TODO_ENTITY_ID", raising=False)
    monkeypatch.delenv("INVENTRA_BRING_RECONCILE_INTERVAL_SECONDS", raising=False)
    reset_settings_cache()
    settings = get_settings()
    assert settings.bring_todo_entity_id == "todo.zuhause"
    assert settings.bring_reconcile_interval_seconds == 600


def test_bring_settings_read_from_env(monkeypatch):
    monkeypatch.setenv("INVENTRA_BRING_TODO_ENTITY_ID", "todo.einkauf")
    monkeypatch.setenv("INVENTRA_BRING_RECONCILE_INTERVAL_SECONDS", "120")
    reset_settings_cache()
    settings = get_settings()
    assert settings.bring_todo_entity_id == "todo.einkauf"
    assert settings.bring_reconcile_interval_seconds == 120
    monkeypatch.delenv("INVENTRA_BRING_TODO_ENTITY_ID", raising=False)
    monkeypatch.delenv("INVENTRA_BRING_RECONCILE_INTERVAL_SECONDS", raising=False)
    reset_settings_cache()


def test_resolver_settings_have_spec_defaults(monkeypatch):
    for env_var in (
        "INVENTRA_RESOLVER_SOURCE_TIMEOUT_SECONDS",
        "INVENTRA_RESOLVER_GLOBAL_DEADLINE_SECONDS",
        "INVENTRA_RESOLVER_CACHE_FOUND_TTL_SECONDS",
        "INVENTRA_RESOLVER_CACHE_NOT_FOUND_TTL_SECONDS",
        "INVENTRA_RESOLVER_CACHE_ERROR_TTL_SECONDS",
        "INVENTRA_RESOLVER_RESULT_TTL_SECONDS",
        "INVENTRA_RESOLVER_MAX_CONCURRENT_REQUESTS",
    ):
        monkeypatch.delenv(env_var, raising=False)
    reset_settings_cache()

    settings = get_settings()

    assert settings.resolver_source_timeout_seconds == 3.0
    assert settings.resolver_global_deadline_seconds == 6.0
    assert settings.resolver_cache_found_ttl_seconds == 30 * 24 * 3600
    assert settings.resolver_cache_not_found_ttl_seconds == 7 * 24 * 3600
    assert settings.resolver_cache_error_ttl_seconds == 300
    assert settings.resolver_result_ttl_seconds == 900
    assert settings.resolver_max_concurrent_requests == 8


def test_resolver_source_timeout_can_be_overridden_from_env(monkeypatch):
    monkeypatch.setenv("INVENTRA_RESOLVER_SOURCE_TIMEOUT_SECONDS", "4.5")
    reset_settings_cache()

    settings = get_settings()

    assert settings.resolver_source_timeout_seconds == 4.5

    monkeypatch.delenv("INVENTRA_RESOLVER_SOURCE_TIMEOUT_SECONDS", raising=False)
    reset_settings_cache()
