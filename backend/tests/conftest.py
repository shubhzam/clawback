import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    # every test gets its own database, portal folder and erp ledger
    monkeypatch.setenv("CLAWBACK_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("CLAWBACK_LLM_PROVIDER", "mock")
    from config import get_settings
    from db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    yield tmp_path
    get_settings.cache_clear()
    reset_engine_cache()
