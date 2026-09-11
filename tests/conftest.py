import pytest

from deployd.config import Settings, get_app_registry, get_settings


@pytest.fixture(autouse=True)
def isolate_runtime_settings(monkeypatch):
    # Installer tests run as the repo owner; the service .env may be root-owned.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    get_app_registry.cache_clear()
    yield
    get_settings.cache_clear()
    get_app_registry.cache_clear()
