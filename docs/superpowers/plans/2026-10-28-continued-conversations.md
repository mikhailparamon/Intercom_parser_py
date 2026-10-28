# Продолженные обращения — Implementation Plan

> **For agentic workers:** Use the approved design in `docs/superpowers/specs/2026-10-28-continued-conversations-design.md`. Tasks are performed inline in this session with tests first.

**Goal:** Export conversations created, updated, or containing any part created in the selected UTC period.

**Architecture:** Expand Intercom Search into a paginated candidate superset using nested OR/AND filters on created_at and updated_at. Retrieve each candidate, locally apply the three exact criteria, and keep storing all available history. Mark the manifest partial when a candidate's parts are truncated and activity cannot be fully determined.

**Tech Stack:** Python 3.11, requests, pytest, Intercom REST API v2.17.

---

### Task 1: Search candidates by created and updated timestamps

**Files:**
- Modify: `src/intercom_exporter/client.py`
- Test: `tests/test_client.py`

- [x] Add a test asserting that `search_conversations(start, end)` sends an OR query with (a) created_at >= start AND created_at < end, and (b) created_at < end AND updated_at >= start.
- [x] Run `python -m pytest tests/test_client.py -q`; verify the request-body assertion fails against the current created_at-only query.
- [x] Implement the two nested filter groups, retaining `per_page: 150`, starting_after pagination, and repeated-cursor protection.
- [x] Run `python -m pytest tests/test_client.py -q` and verify it passes.

### Task 2: Apply the three local selection criteria

**Files:**
- Modify: `src/intercom_exporter/exporter.py`
- Test: `tests/test_exporter_storage.py`

- [x] Add tests for an older conversation updated inside the period, an older conversation with a part created inside the period but updated after the period, and an older conversation whose update and parts are both outside the period.
- [x] Add boundary tests for a part at start (include) and a part at end (exclude); retain all parts in output for included conversations.
- [x] Run `python -m pytest tests/test_exporter_storage.py -q`; verify the new selection tests fail against the current creation-only exporter.
- [x] Implement inclusion as `created_in_period or updated_in_period or any(part.created_at_in_period)` after retrieving candidate details.
- [x] Deduplicate repeated candidate IDs before retrieving their details.
- [x] Run `python -m pytest tests/test_exporter_storage.py -q` and verify it passes.

### Task 3: Report uncertain selection when history is truncated

**Files:**
- Modify: `src/intercom_exporter/exporter.py`
- Test: `tests/test_exporter_storage.py`
- Modify: `README.md`

- [x] Add a test where a pre-period conversation has more parts than retrieve returns, no visible in-period parts, and updated_at outside the period; assert manifest status is partial and records the uncertainty.
- [x] Verify this test fails before implementation.
- [x] Mark the manifest partial and add a limitation when truncation prevents confirming whether an old candidate had an in-period part.
- [x] Update README with the union criteria, UTC half-open semantics, full-history retention, candidate-search limitation, and 500-part cap.
- [x] Run the full suite with `python -m pytest -q` and `python -m compileall -q main.py src tests`.
- [x] Review `git diff --check`, verify no `.env` or data exports are staged, then commit the feature.
