from datetime import datetime, timezone

from intercom_exporter.activity import MEANINGFUL_PART_TYPES, has_meaningful_activity


def _ts(value):
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


START = _ts("2026-11-01T00:00:00Z")
END = _ts("2026-11-03T00:00:00Z")


def _conv(created_at, parts=(), updated_at=None):
    return {
        "created_at": created_at,
        "updated_at": updated_at,
        "conversation_parts": {"conversation_parts": list(parts)},
    }


def _part(part_type, created_at):
    return {"part_type": part_type, "created_at": created_at}


def test_created_inside_window_counts():
    assert has_meaningful_activity(_conv(_ts("2026-11-02T10:00:00Z")), START, END) is True


def test_created_outside_without_parts_does_not_count():
    assert has_meaningful_activity(_conv(_ts("2026-07-08T10:00:00Z")), START, END) is False


def test_updated_at_alone_does_not_count():
    conv = _conv(_ts("2026-07-08T10:00:00Z"), updated_at=_ts("2026-11-02T15:00:00Z"))
    assert has_meaningful_activity(conv, START, END) is False


def test_bulk_reassignment_does_not_count():
    conv = _conv(_ts("2026-07-08T10:00:00Z"),
                 [_part("bulk_reassignment", _ts("2026-11-02T15:00:00Z"))],
                 updated_at=_ts("2026-11-02T15:00:00Z"))
    assert has_meaningful_activity(conv, START, END) is False


def test_user_comment_counts():
    conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part("comment", _ts("2026-11-02T19:14:54Z"))])
    assert has_meaningful_activity(conv, START, END) is True


def test_meaningful_part_types_set():
    assert MEANINGFUL_PART_TYPES == {"comment", "open", "note", "close"}


def test_each_meaningful_type_counts_and_system_types_do_not():
    for good in ("comment", "open", "note", "close"):
        conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part(good, _ts("2026-11-02T12:00:00Z"))])
        assert has_meaningful_activity(conv, START, END) is True, good
    for bad in ("bulk_reassignment", "assignment", "conversation_attribute_updated_by_admin",
                "snoozed", "timer_unsnooze", ""):
        conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part(bad, _ts("2026-11-02T12:00:00Z"))])
        assert has_meaningful_activity(conv, START, END) is False, bad


def test_part_outside_window_does_not_count():
    conv = _conv(_ts("2026-07-08T10:00:00Z"), [_part("comment", _ts("2026-10-31T23:59:59Z"))])
    assert has_meaningful_activity(conv, START, END) is False


def test_unix_timestamps_supported():
    conv = {"created_at": _ts("2026-11-02T10:00:00Z"), "conversation_parts": []}
    assert has_meaningful_activity(conv, START, END) is True
