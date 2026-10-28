import json
from datetime import date

from intercom_exporter.dates import DateRange
from intercom_exporter.exporter import ConversationExporter
from intercom_exporter.storage import JsonlStore


class FakeClient:
    def __init__(self, candidates, details):
        self.candidates = candidates
        self.details = details

    def search_conversations(self, start, end):
        yield from self.candidates

    def get_conversation(self, conversation_id):
        value = self.details[conversation_id]
        if isinstance(value, Exception):
            raise value
        return value


def candidate(cid, created, **extra):
    return {"id": cid, "created_at": created, **extra}


def detail(cid, parts, **extra):
    return {"id": cid, "created_at": 1782864000, "updated_at": 1782950400,
            "conversation_parts": {"conversation_parts": parts, "total_count": len(parts)}, **extra}


def test_export_filters_strictly_by_created_at_and_preserves_out_of_range_parts(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, end = period.epoch_bounds()
    client = FakeClient(
        [candidate("old", start - 1, updated_at=start + 1), candidate("start", start),
         candidate("closed-later", start + 5, closed_at=end + 10), candidate("at-end", end)],
        {"start": detail("start", [{"id": "before", "created_at": start - 1}, {"id": "after", "created_at": end + 1}]),
         "closed-later": detail("closed-later", [])},
    )
    result = ConversationExporter(client).run(period, tmp_path)
    rows = [json.loads(line) for line in (tmp_path / "2026-07-01_2026-10-01" / "conversations.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in rows] == ["start", "closed-later"]
    assert [part["id"] for part in rows[0]["conversation_parts"]] == ["before", "after"]
    assert result["exported_count"] == 2


def test_output_is_unicode_jsonl_and_manifest_has_no_token(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    client = FakeClient([candidate("1", start)], {"1": detail("1", [{"body": "Привет 👋"}])})
    result = ConversationExporter(client).run(period, tmp_path, api_version="2.17")
    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.jsonl"
    assert "Привет" in path.read_text(encoding="utf-8")
    manifest = json.loads((tmp_path / "2026-07-01_2026-10-01" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["api_version"] == "2.17"
    assert manifest["status"] == "success"
    assert result["error_count"] == 0


def test_partial_export_keeps_success_and_marks_manifest_incomplete(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    client = FakeClient([candidate("ok", start), candidate("fail", start + 1)],
                        {"ok": detail("ok", []), "fail": RuntimeError("token-secret")})
    result = ConversationExporter(client).run(period, tmp_path)
    manifest_text = (tmp_path / "2026-07-01_2026-10-01" / "manifest.json").read_text(encoding="utf-8")
    assert result["error_count"] == 1
    assert "token-secret" not in manifest_text
    assert json.loads(manifest_text)["status"] == "partial"
    assert len((tmp_path / "2026-07-01_2026-10-01" / "conversations.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_same_period_rerun_deduplicates_and_updates_record(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.jsonl"
    store = JsonlStore(path)
    store.merge([{"id": "x", "body": "old"}])
    store.merge([{"id": "x", "body": "new"}, {"id": "y"}])
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"id": "x", "body": "new"}, {"id": "y"}]


def test_parts_limit_is_reported_as_partial(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    client = FakeClient([candidate("big", start, count_conversation_parts=501)],
                        {"big": detail("big", [{"id": str(i)} for i in range(500)])})
    result = ConversationExporter(client).run(period, tmp_path)
    assert result["error_count"] == 1
    manifest = json.loads((tmp_path / "2026-07-01_2026-10-01" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert manifest["limitations"]


from intercom_exporter.exporter import ConversationExporter


def test_normalization_keeps_close_and_assignment_metadata_without_contact_emails():
    raw = {
        "id": "a", "title": "Question", "state": "closed", "open": False,
        "admin_assignee_id": 12, "team_assignee_id": 34,
        "statistics": {"last_close_at": 1234},
        "contacts": {"type": "contact.list", "contacts": [{"id": "c1", "type": "contact", "email": "private@example.com"}]},
        "conversation_parts": [],
    }
    normalized = ConversationExporter._normalize(raw, [])
    assert normalized["title"] == "Question"
    assert normalized["admin_assignee_id"] == 12
    assert "email" not in normalized["contacts"]["contacts"][0]
def test_source_message_is_included_when_checking_api_part_count(tmp_path):
    from datetime import date
    period = DateRange(date(2026, 10, 27), date(2026, 10, 28))
    start, _ = period.epoch_bounds()
    source = {"type": "conversation", "body": "opening message"}
    api_detail = detail("with-source", [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}], source=source)
    client = FakeClient([candidate("with-source", start, count_conversation_parts=4)], {"with-source": api_detail})
    result = ConversationExporter(client).run(period, tmp_path)
    assert result["status"] == "success"
    assert result["error_count"] == 0
    row = json.loads((tmp_path / "2026-10-27_2026-10-28" / "conversations.jsonl").read_text(encoding="utf-8"))
    assert row["source"]["body"] == "opening message"
    assert len(row["conversation_parts"]) == 3
