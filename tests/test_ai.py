from app.core.config import get_settings
from app.services import ai


def test_openai_client_uses_configured_base_url(monkeypatch):
    get_settings.cache_clear()
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.proxyapi.ru/openai/v1")
    monkeypatch.setattr(ai, "OpenAI", FakeOpenAI)

    ai._openai_client()

    assert captured["api_key"] == "test-key"
    assert captured["base_url"] == "https://api.proxyapi.ru/openai/v1"
    get_settings.cache_clear()
