import json

import pytest

from intercom_exporter.config import load_slack_settings
from intercom_exporter.notify_slack import SlackNotifyError, build_slack_summary, send_dashboard


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


def _write_dashboard(tmp_path, data=None):
    path = tmp_path / "dashboard.html"
    path.write_text("<html>dash</html>", encoding="utf-8")
    if data is not None:
        path.with_suffix(".json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _summary_data():
    return {
        "kpis": {"day_total": 111, "week_total": 887},
        "daily": [
            {"date": "2026-10-29", "count": 90},
            {"date": "2026-10-30", "count": 95},
            {"date": "2026-10-31", "count": 80},
            {"date": "2026-11-01", "count": 90},
            {"date": "2026-11-02", "count": 100},
            {"date": "2026-11-03", "count": 50},
            {"date": "2026-11-04", "count": 30},
        ],
        "day_categories": [
            {"name": "Проблемы подключения VPN", "count": 35, "share": 31.5},
            {"name": "Оплата и управление подпиской", "count": 24, "share": 21.6},
            {"name": "Доступ к сайтам и сервисам", "count": 12, "share": 10.8},
            {"name": "Прочие обращения", "count": 11, "share": 9.9},
        ],
        "week_categories": [
            {"name": "Проблемы подключения VPN", "count": 337, "share": 38.0},
            {"name": "Оплата и управление подпиской", "count": 176, "share": 19.8},
        ],
    }


def test_build_slack_summary_formats_day_and_week_stats():
    text = build_slack_summary(_summary_data())

    assert text.splitlines() == [
        "За сутки: 111 обращений всего (Новых за 03.11: 50 шт., -50.0% к 02.11)",
        "Топ-3 категорий за сутки: 1. Проблемы подключения VPN — 35 (31.5%) "
        "2. Оплата и управление подпиской — 24 (21.6%) "
        "3. Доступ к сайтам и сервисам — 12 (10.8%)",
        "За неделю: 887 обращений всего",
        "Топ-3 категорий за неделю: 1. Проблемы подключения VPN — 337 (38.0%) "
        "2. Оплата и управление подпиской — 176 (19.8%)",
    ]


def test_build_slack_summary_na_when_previous_day_has_zero():
    data = _summary_data()
    data["daily"][-3]["count"] = 0

    assert "(Новых за 03.11: 50 шт., н/д к 02.11)" in build_slack_summary(data)


def test_build_slack_summary_skips_change_line_with_short_daily():
    data = _summary_data()
    data["daily"] = data["daily"][-2:]

    text = build_slack_summary(data)
    assert "(Новых за" not in text
    assert text.splitlines()[0] == "За сутки: 111 обращений всего"


def test_build_slack_summary_without_categories():
    data = _summary_data()
    data["day_categories"] = []

    assert "Топ-3 категорий за сутки: нет данных" in build_slack_summary(data)


def test_send_dashboard_uploads_file_with_summary(tmp_path):
    dashboard = _write_dashboard(tmp_path, _summary_data())
    session = _FakeSession()

    file_id = send_dashboard("xoxb-test", "C123", dashboard, session=session)

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
    comment = kwargs["json"]["initial_comment"]
    assert "За сутки: 111 обращений всего" in comment
    assert "За неделю: 887 обращений всего" in comment


def test_send_dashboard_without_data_json_sends_empty_comment(tmp_path):
    dashboard = _write_dashboard(tmp_path)
    session = _FakeSession()

    send_dashboard("xoxb-test", "C123", dashboard, session=session)

    assert session.calls[2][1]["json"]["initial_comment"] == ""


def test_send_dashboard_raises_on_slack_error(tmp_path):
    dashboard = _write_dashboard(tmp_path)
    session = _FakeSession(upload_error="invalid_auth")

    with pytest.raises(SlackNotifyError, match="invalid_auth"):
        send_dashboard("xoxb-bad", "C123", dashboard, session=session)


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
    # find_latest_run требует срезы last_week/last_day
    (run.parent / "last_week").mkdir()
    (run.parent / "last_day").mkdir()
    (run.parent / "last_week" / "conversations.json").write_text("[]", encoding="utf-8")
    (run.parent / "last_day" / "conversations.json").write_text("[]", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "load_slack_settings",
                        lambda: config.SlackSettings("xoxb-test", "C123"))
    calls = []

    def fake_send(token, channel, dashboard_path):
        calls.append((token, channel, dashboard_path.name))
        return "F123"

    monkeypatch.setattr(notify_slack, "send_dashboard", fake_send)

    assert main(["notify-slack"]) == 0
    assert calls == [("xoxb-test", "C123", "dashboard_03_11_2026.html")]


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
        lambda token, channel, dash: calls.append((token, channel)) or "F123")
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
