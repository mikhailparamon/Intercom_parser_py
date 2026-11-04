import json

import pytest

from intercom_exporter.config import load_slack_settings
from intercom_exporter.notify_slack import SlackNotifyError, _extract_summary, send_dashboard


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload or {"ok": True}

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, upload_error=None):
        self.calls = []
        self.upload_error = upload_error

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("files.getUploadURLExternal"):
            payload = {"ok": True, "upload_url": "https://upload.slack.local/put", "file_id": "F123"}
            if self.upload_error:
                payload = {"ok": False, "error": self.upload_error}
            return _FakeResponse(payload)
        if url.endswith("files.completeUploadExternal"):
            return _FakeResponse({"ok": True, "files": [{"id": "F123", "title": "dashboard.html"}]})
        return _FakeResponse()  # POST содержимого на upload_url


def _write_dashboard(tmp_path):
    path = tmp_path / "dashboard.html"
    path.write_text("<html>dash</html>", encoding="utf-8")
    return path


def test_send_dashboard_uploads_file_with_summary(tmp_path):
    dashboard = _write_dashboard(tmp_path)
    report = tmp_path / "report.md"
    report.write_text("# Отчёт\n\n## Последние 7 дней\n\n## Резюме\n\nНеделя: 10 обращений.\n\n## Частота проблем\n",
                      encoding="utf-8")
    session = _FakeSession()

    file_id = send_dashboard("xoxb-test", "C123", dashboard, report, session=session)

    assert file_id == "F123"
    assert len(session.calls) == 3
    url, kwargs = session.calls[0]
    assert url.endswith("files.getUploadURLExternal")
    assert kwargs["headers"]["Authorization"] == "Bearer xoxb-test"
    assert kwargs["data"]["length"] == len("<html>dash</html>".encode())
    assert session.calls[1][0] == "https://upload.slack.local/put"
    assert session.calls[1][1]["data"] == "<html>dash</html>".encode()
    url, kwargs = session.calls[2]
    assert url.endswith("files.completeUploadExternal")
    assert kwargs["json"]["channel_id"] == "C123"
    assert kwargs["json"]["files"] == [{"id": "F123", "title": "dashboard.html"}]
    assert "Неделя: 10 обращений." in kwargs["json"]["initial_comment"]


def test_send_dashboard_raises_on_slack_error(tmp_path):
    dashboard = _write_dashboard(tmp_path)
    session = _FakeSession(upload_error="invalid_auth")

    with pytest.raises(SlackNotifyError, match="invalid_auth"):
        send_dashboard("xoxb-bad", "C123", dashboard, session=session)


def test_extract_summary_returns_first_resume_block(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("# T\n\n## Резюме\n\nСтрока один.\nСтрока два.\n\n## Дальше\n", encoding="utf-8")
    assert _extract_summary(report) == "Строка один. Строка два."


def test_extract_summary_missing_report_returns_empty(tmp_path):
    assert _extract_summary(tmp_path / "нет.md") == ""


def test_load_slack_settings_reads_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_BOT_TOKEN=xoxb-file\nSLACK_CHANNEL_ID=C999\n", encoding="utf-8")

    settings = load_slack_settings(env_file)

    assert settings is not None
    assert settings.bot_token == "xoxb-file"
    assert settings.channel_id == "C999"


def test_load_slack_settings_returns_none_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SLACK_CHANNEL_ID=C999\n", encoding="utf-8")
    assert load_slack_settings(env_file) is None


def test_cli_notify_slack_sends_latest_run(tmp_path, monkeypatch):
    from intercom_exporter import config, notify_slack
    from intercom_exporter.support_analysis import main

    run = tmp_path / "data" / "run1" / "support-analysis"
    run.mkdir(parents=True)
    (run / "dashboard_03_11_2026.html").write_text("<html></html>", encoding="utf-8")
    (run / "report.md").write_text("## Резюме\n\nСводка.\n", encoding="utf-8")
    # find_latest_run требует срезы last_week/last_day
    (run.parent / "last_week").mkdir()
    (run.parent / "last_day").mkdir()
    (run.parent / "last_week" / "conversations.json").write_text("[]", encoding="utf-8")
    (run.parent / "last_day" / "conversations.json").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_slack_settings",
                        lambda: config.SlackSettings("xoxb-test", "C123"))
    calls = []

    def fake_send(token, channel, dashboard_path, report_path):
        calls.append((token, channel, dashboard_path.name, report_path.name))
        return "F123"

    monkeypatch.setattr(notify_slack, "send_dashboard", fake_send)

    assert main(["notify-slack"]) == 0
    assert calls == [("xoxb-test", "C123", "dashboard_03_11_2026.html", "report.md")]


def test_cli_notify_slack_requires_token(tmp_path, monkeypatch, capsys):
    from intercom_exporter import config
    from intercom_exporter.support_analysis import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_slack_settings", lambda: None)

    with pytest.raises(SystemExit):
        main(["notify-slack"])
    assert "SLACK_BOT_TOKEN" in capsys.readouterr().err


def _write_run(tmp_path, ids=("a", "b")):
    directory = tmp_path / "run" / "last_week"
    directory.mkdir(parents=True)
    rows = [{"id": cid, "source": {"body": f"Клиент {cid}: не работает VPN"},
             "conversation_parts": {"conversation_parts": []}} for cid in ids]
    week = directory / "conversations.json"
    week.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    day_dir = tmp_path / "run" / "last_day"
    day_dir.mkdir()
    day = day_dir / "conversations.json"
    day.write_text(json.dumps(rows[:1], ensure_ascii=False), encoding="utf-8")
    return week, day


def _fill_agent_results(manifest):
    for entry in manifest["chunks"]:
        chunk = json.loads(open(entry["chunk_path"], encoding="utf-8").read())
        items = [{
            "id": conv["id"], "category": "Проблемы подключения VPN",
            "subcategory": "s", "problem_summary": "s", "severity": None,
            "severity_reason": "—", "confidence": "high", "evidence": "",
        } for conv in chunk["conversations"]]
        with open(entry["result_path"], "w", encoding="utf-8") as handle:
            json.dump({"classifications": items}, handle, ensure_ascii=False)


def _merge_run(tmp_path, monkeypatch):
    from intercom_exporter import config, notify_slack
    from intercom_exporter.agents_orchestration import prepare_agents_bundle

    week, day = _write_run(tmp_path)
    out = tmp_path / "run" / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_agent_results(manifest)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_slack_settings",
                        lambda: config.SlackSettings("xoxb-test", "C123"))
    calls = []
    monkeypatch.setattr(
        notify_slack, "send_dashboard",
        lambda token, channel, dash, report: calls.append((token, channel)) or "F123")
    return calls


def test_cli_merge_agents_auto_sends_slack_when_configured(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import main

    calls = _merge_run(tmp_path, monkeypatch)

    assert main(["merge-agents", "--week", str(tmp_path / "run/last_week/conversations.json"),
                 "--day", str(tmp_path / "run/last_day/conversations.json")]) == 0
    assert calls == [("xoxb-test", "C123")]


def test_cli_merge_agents_no_notify_flag_skips_slack(tmp_path, monkeypatch):
    from intercom_exporter.support_analysis import main

    calls = _merge_run(tmp_path, monkeypatch)

    assert main(["merge-agents", "--week", str(tmp_path / "run/last_week/conversations.json"),
                 "--day", str(tmp_path / "run/last_day/conversations.json"),
                 "--no-notify"]) == 0
    assert calls == []


def test_cli_merge_agents_skips_slack_without_settings(tmp_path, monkeypatch):
    from intercom_exporter import config, notify_slack
    from intercom_exporter.agents_orchestration import prepare_agents_bundle
    from intercom_exporter.support_analysis import main

    week, day = _write_run(tmp_path)
    out = tmp_path / "run" / "support-analysis"
    manifest = prepare_agents_bundle(week, day, out, workers=2)
    _fill_agent_results(manifest)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_slack_settings", lambda: None)
    calls = []
    monkeypatch.setattr(
        notify_slack, "send_dashboard",
        lambda *a: calls.append(a) or "F123")

    assert main(["merge-agents", "--week", str(week), "--day", str(day)]) == 0
    assert calls == []
