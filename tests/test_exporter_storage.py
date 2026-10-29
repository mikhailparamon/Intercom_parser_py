import json
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from intercom_exporter.dates import DateRange
from intercom_exporter.exporter import ConversationExporter, format_duration
from intercom_exporter.storage import JsonlStore


class FakeClient:
    def __init__(self, candidates, details):
        self.candidates = candidates
        self.details = details
        self.get_calls = []

    def search_conversations(self, start, end):
        yield from self.candidates

    def get_conversation(self, conversation_id):
        self.get_calls.append(conversation_id)
        value = self.details[conversation_id]
        if isinstance(value, Exception):
            raise value
        return value


def candidate(cid, created, **extra):
    return {"id": cid, "created_at": created, **extra}


def detail(cid, parts, **extra):
    return {"id": cid, "created_at": 1782864000, "updated_at": 1782950400,
            "conversation_parts": {"conversation_parts": parts, "total_count": len(parts)}, **extra}


def test_format_duration_as_hours_minutes_seconds():
    assert format_duration(5) == "0 ч 0 мин 5 сек"
    assert format_duration(65) == "0 ч 1 мин 5 сек"
    assert format_duration(3600) == "1 ч 0 мин 0 сек"
    assert format_duration(3599.6) == "1 ч 0 мин 0 сек"


def test_export_fetches_details_concurrently_with_bounded_workers(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()

    class SlowClient(FakeClient):
        def __init__(self, candidates, details):
            super().__init__(candidates, details)
            self.lock = threading.Lock()
            self.in_flight = 0
            self.max_in_flight = 0

        def get_conversation(self, conversation_id):
            with self.lock:
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
            try:
                time.sleep(0.03)
                return super().get_conversation(conversation_id)
            finally:
                with self.lock:
                    self.in_flight -= 1

    candidates = [candidate(str(index), start + index) for index in range(8)]
    client = SlowClient(candidates, {item["id"]: detail(item["id"], []) for item in candidates})
    ConversationExporter(client, max_workers=3).run(period, tmp_path)
    rows = json.loads((tmp_path / "2026-07-01_2026-10-01" / "conversations.json").read_text(encoding="utf-8"))

    assert 1 < client.max_in_flight <= 3
    assert [row["id"] for row in rows] == [item["id"] for item in candidates]


def test_export_includes_created_or_updated_and_preserves_out_of_range_parts(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, end = period.epoch_bounds()
    client = FakeClient(
        [candidate("updated", start - 1, updated_at=start + 1), candidate("start", start),
         candidate("closed-later", start + 5, closed_at=end + 10), candidate("at-end", end, updated_at=end + 10)],
        {"updated": detail("updated", [], created_at=start - 1, updated_at=start + 1),
         "start": detail("start", [{"id": "before", "created_at": start - 1}, {"id": "after", "created_at": end + 1}]),
         "closed-later": detail("closed-later", []),
         "at-end": detail("at-end", [], created_at=end, updated_at=end + 10)},
    )
    result = ConversationExporter(client).run(period, tmp_path, fields="all")
    rows = json.loads((tmp_path / "2026-07-01_2026-10-01" / "conversations.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in rows] == ["updated", "start", "closed-later"]
    assert [part["id"] for part in rows[1]["conversation_parts"]["conversation_parts"]] == ["before", "after"]
    assert result["exported_count"] == 3
    assert result["elapsed_seconds"] >= 0


def test_output_is_unicode_pretty_json_and_manifest_has_no_token(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    client = FakeClient([candidate("1", start)], {"1": detail("1", [{"body": "Привет 👋"}])})
    result = ConversationExporter(client).run(period, tmp_path, api_version="2.17")
    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.json"
    assert "Привет" in path.read_text(encoding="utf-8")
    assert path.read_text(encoding="utf-8").startswith("[\n")
    manifest = json.loads((tmp_path / "2026-07-01_2026-10-01" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["api_version"] == "2.17"
    assert manifest["selection"] == "all"
    assert manifest["fields"] == "important"
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
    assert len(json.loads((tmp_path / "2026-07-01_2026-10-01" / "conversations.json").read_text(encoding="utf-8"))) == 1


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


def test_all_fields_normalization_preserves_unfiltered_api_fields():
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
    assert normalized["contacts"]["contacts"][0]["email"] == "private@example.com"
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
    row = json.loads((tmp_path / "2026-10-27_2026-10-28" / "conversations.json").read_text(encoding="utf-8"))[0]
    assert row["source"]["body"] == "opening message"
    assert len(row["conversation_parts"]) == 3


def test_export_includes_created_updated_or_continued_conversations(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, end = period.epoch_bounds()
    candidates = [
        candidate("created", start),
        candidate("updated", start - 100, updated_at=start + 1),
        candidate("continued", start - 100, updated_at=end + 100),
        candidate("unrelated", start - 100, updated_at=end + 200),
    ]
    details = {
        "created": detail("created", []),
        "updated": detail("updated", [{"id": "outside", "created_at": end + 10}], created_at=start - 100, updated_at=start + 1),
        "continued": detail("continued", [
            {"id": "assignment", "part_type": "assignment", "created_at": start},
            {"id": "later", "part_type": "comment", "created_at": end + 10},
        ], created_at=start - 100, updated_at=end + 100),
        "unrelated": detail("unrelated", [{"id": "outside-only", "created_at": end + 10}], created_at=start - 100, updated_at=end + 200),
    }
    result = ConversationExporter(FakeClient(candidates, details)).run(period, tmp_path, fields="all")
    rows = json.loads((tmp_path / "2026-07-01_2026-10-01" / "conversations.json").read_text(encoding="utf-8"))
    assert {row["id"] for row in rows} == {"created", "updated", "continued"}
    continued = next(row for row in rows if row["id"] == "continued")
    assert [part["id"] for part in continued["conversation_parts"]["conversation_parts"]] == ["assignment", "later"]
    assert result["found_count"] == 3


def test_part_period_is_start_inclusive_and_end_exclusive(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, end = period.epoch_bounds()
    candidates = [candidate("at-start", start - 1, updated_at=end + 1),
                 candidate("at-end", start - 1, updated_at=end + 2)]
    details = {
        "at-start": detail("at-start", [{"id": "start", "created_at": start}], created_at=start - 1, updated_at=end + 1),
        "at-end": detail("at-end", [{"id": "end", "created_at": end}], created_at=start - 1, updated_at=end + 2),
    }
    ConversationExporter(FakeClient(candidates, details)).run(period, tmp_path)
    rows = json.loads((tmp_path / "2026-07-01_2026-10-01" / "conversations.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in rows] == ["at-start"]


def test_truncated_candidate_with_unknown_period_activity_marks_partial(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, end = period.epoch_bounds()
    old_parts = [{"id": str(index), "created_at": end + 100 + index} for index in range(500)]
    old_detail = detail("uncertain", old_parts, created_at=start - 100, updated_at=end + 1000,
                        source={"type": "conversation", "created_at": start - 100})
    candidate_item = candidate("uncertain", start - 100, updated_at=end + 1000, count_conversation_parts=502)
    result = ConversationExporter(FakeClient([candidate_item], {"uncertain": old_detail})).run(period, tmp_path)
    manifest = json.loads((tmp_path / "2026-07-01_2026-10-01" / "manifest.json").read_text(encoding="utf-8"))
    assert result["status"] == "partial"
    assert result["error_count"] == 1
    assert manifest["limitations"]


def test_repeated_search_candidate_is_retrieved_once(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    repeated = candidate("same", start)
    client = FakeClient([repeated, repeated], {"same": detail("same", [])})
    result = ConversationExporter(client).run(period, tmp_path)
    assert client.get_calls == ["same"]
    assert result["found_count"] == 1


def test_selection_modes_include_only_the_requested_conversation_types(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    candidates = [
        candidate("new", start + 1),
        candidate("updated", start - 1, updated_at=start + 2),
        candidate("continued", start - 1, updated_at=start + 20),
    ]
    details = {
        "new": detail("new", [], created_at=start + 1, updated_at=start + 3),
        "updated": detail("updated", [], created_at=start - 1, updated_at=start + 2),
        "continued": detail("continued", [{"part_type": "assignment", "created_at": start + 4}],
                            created_at=start - 1, updated_at=start + 20),
    }
    for selection, expected in {
        "new": {"new"},
        "updated": {"updated", "continued"},
        "all": {"new", "updated", "continued"},
    }.items():
        output_root = tmp_path / selection
        ConversationExporter(FakeClient(candidates, details)).run(period, output_root, selection=selection)
        path = output_root / "2026-07-01_2026-10-01" / "conversations.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        assert {row["id"] for row in rows} == expected


def test_updated_selection_includes_part_activity_even_if_updated_at_is_outside_period(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, end = period.epoch_bounds()
    old = candidate("old", start - 10, updated_at=end + 10)
    old_detail = detail("old", [{"part_type": "comment", "created_at": start + 5}],
                        created_at=start - 10, updated_at=end + 10)
    ConversationExporter(FakeClient([old], {"old": old_detail})).run(
        period, tmp_path, selection="updated"
    )
    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.json"
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


def test_important_fields_keep_analysis_text_and_minimal_speaker_context(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    raw = detail("compact", [{"id": "part-id", "part_type": "comment", "created_at": start + 5,
                              "body": "Operator reply", "author": {"id": "private-id", "type": "admin", "name": "Name"},
                              "metadata": {"secret": "omit"}}],
                 created_at=start + 1, updated_at=start + 8,
                 title="VPN issue", state="closed", source={"body": "Customer message", "id": "email-id"},
                 contacts={"contacts": [{"email": "private@example.com"}]})
    ConversationExporter(FakeClient([candidate("compact", start + 1)], {"compact": raw})).run(period, tmp_path)
    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.json"
    row = json.loads(path.read_text(encoding="utf-8"))[0]
    assert row == {
        "id": "compact", "title": "VPN issue", "created_at": "2026-07-01T00:00:01Z",
        "updated_at": "2026-07-01T00:00:08Z", "source": {"body": "Customer message"},
        "conversation_parts": [{"part_type": "comment", "created_at": "2026-07-01T00:00:05Z",
                                 "body": "Operator reply", "author": {"type": "admin"}}],
    }


def test_each_export_run_uses_a_unique_folder_and_preserves_previous_data(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    first = ConversationExporter(FakeClient([candidate("first", start)], {"first": detail("first", [])})).run(
        period, tmp_path
    )
    first_path = Path(first["output_dir"]) / "conversations.json"
    first_content = first_path.read_text(encoding="utf-8")

    second = ConversationExporter(FakeClient([candidate("second", start)], {"second": detail("second", [])})).run(
        period, tmp_path
    )
    second_path = Path(second["output_dir"]) / "conversations.json"

    assert first["output_dir"] != second["output_dir"]
    assert json.loads(first_path.read_text(encoding="utf-8"))[0]["id"] == "first"
    assert json.loads(second_path.read_text(encoding="utf-8"))[0]["id"] == "second"
    assert first_path.read_text(encoding="utf-8") == first_content


def test_unique_output_folder_adds_counter_if_timestamp_name_is_taken(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    started_at = datetime(2026, 10, 29, 12, 30, 45, 123456, tzinfo=timezone.utc)
    base_name = "2026-07-01_2026-10-01"
    (tmp_path / base_name).mkdir()
    timestamp_name = f"{base_name}_run_{started_at.strftime('%Y%m%dT%H%M%S.%fZ')}"
    (tmp_path / timestamp_name).mkdir()

    output_dir = ConversationExporter._create_output_dir(tmp_path, period, started_at)

    assert output_dir.name == f"{timestamp_name}_01"
    assert output_dir.is_dir()


def test_new_selection_ignores_truncated_history_for_old_excluded_conversation(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    old_candidate = candidate("old", start - 10, updated_at=start + 1, count_conversation_parts=501)
    old_detail = detail("old", [{"created_at": start + 1} for _ in range(500)],
                        created_at=start - 10, updated_at=start + 1)
    result = ConversationExporter(FakeClient([old_candidate], {"old": old_detail})).run(
        period, tmp_path, selection="new"
    )
    assert result["status"] == "success"
    assert result["error_count"] == 0
    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.json"
    assert json.loads(path.read_text(encoding="utf-8")) == []


def test_important_normalization_converts_unix_times_to_utc_iso8601():
    raw = {
        "id": "time-test",
        "created_at": 1793102160,
        "updated_at": 1793102557,
        "waiting_since": 1793222052,
        "snoozed_until": 1793105760,
        "statistics": {"last_close_at": 1793102557, "count_conversation_parts": 1},
    }
    parts = [{
        "id": "part-1",
        "created_at": 1793102269,
        "updated_at": 1793102269,
        "notified_at": 1793102270,
        "metadata": {"created_at": 1793102271},
    }]
    normalized = ConversationExporter._normalize(raw, parts, fields="important")
    assert normalized["created_at"] == "2026-10-27T11:56:00Z"
    assert normalized["updated_at"] == "2026-10-27T12:02:37Z"
    assert normalized["conversation_parts"][0]["created_at"] == "2026-10-27T11:57:49Z"


def test_all_fields_writes_pretty_json_and_preserves_complete_api_payload_and_unix_times(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    raw = detail(
        "full",
        [{"id": "part", "created_at": start + 5, "author": {"email": "person@example.com"},
          "new_api_field": {"enabled": True}}],
        created_at=start + 1,
        updated_at=start + 8,
        custom_api_field={"nested": "kept"},
        contacts={"contacts": [{"id": "contact", "email": "person@example.com"}]},
    )
    ConversationExporter(FakeClient([candidate("full", start + 1)], {"full": raw})).run(
        period, tmp_path, fields="all"
    )

    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.json"
    text = path.read_text(encoding="utf-8")
    rows = json.loads(text)
    row = rows[0]
    assert text.startswith("[\n")
    assert row["custom_api_field"] == {"nested": "kept"}
    assert row["contacts"]["contacts"][0]["email"] == "person@example.com"
    assert row["created_at"] == start + 1
    assert row["conversation_parts"]["conversation_parts"][0]["created_at"] == start + 5
    assert row["conversation_parts"]["conversation_parts"][0]["new_api_field"] == {"enabled": True}


def test_important_fields_use_pretty_json_array_and_keep_iso_utc_timestamps(tmp_path):
    period = DateRange(date(2026, 7, 1), date(2026, 10, 1))
    start, _ = period.epoch_bounds()
    raw = detail("compact", [], created_at=start + 1, updated_at=start + 8, custom_api_field="omitted")
    ConversationExporter(FakeClient([candidate("compact", start + 1)], {"compact": raw})).run(
        period, tmp_path, fields="important"
    )
    path = tmp_path / "2026-07-01_2026-10-01" / "conversations.json"
    text = path.read_text(encoding="utf-8")
    row = json.loads(text)[0]
    assert text.startswith("[\n")
    assert row["created_at"] == "2026-07-01T00:00:01Z"
    assert "custom_api_field" not in row
