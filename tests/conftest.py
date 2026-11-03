import pytest


@pytest.fixture(autouse=True)
def _no_slack_from_real_env(monkeypatch):
    """Изолирует тесты от реального .env: автоотправка в Slack по умолчанию выключена.

    Тесты автоотправки и загрузки настроек переопределяют monkeypatch повторно.
    """
    from intercom_exporter import config

    monkeypatch.setattr(config, "load_slack_settings", lambda *args, **kwargs: None)
