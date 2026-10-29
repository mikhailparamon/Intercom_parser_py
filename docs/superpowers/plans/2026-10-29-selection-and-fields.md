# Selection and Important Fields Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add CLI selection modes for new/updated/all conversations and compact/full output modes, with compact output the default.

**Architecture:** Keep the existing API candidate search and full detail retrieval, then apply the requested selection mode locally using conversation timestamps and part timestamps. Normalize into either the current full schema or the compact analysis schema. Each run atomically replaces the period's JSONL so a different selection or field mode cannot leave stale rows from an earlier run; retain the standalone JsonlStore.merge behavior for its existing callers.

**Tech Stack:** Python 3.11, argparse, pytest, JSONL.

---

### Task 1: Add exporter selection and compact projection

**Files:**
- Modify: `tests/test_exporter_storage.py`
- Modify: `src/intercom_exporter/exporter.py`

- [x] Add tests for `new` selecting only created-in-period records; `updated` selecting only pre-period records whose updated_at or part created_at is in-period; `all` retaining both classes.
- [x] Run `python -m pytest tests/test_exporter_storage.py -q`; verify the tests fail because the exporter has no selection argument.
- [x] Add `selection="all"` and `fields="important"` keyword arguments to `ConversationExporter.run`; preserve current selection for `all`, and apply the date definitions from the approved spec for `new` and `updated`.
- [x] Add tests asserting compact rows contain top-level `id`, `title`, `created_at`, `updated_at`, `source.body`, and part `part_type`, `created_at`, author type and body, while excluding other metadata.
- [x] Run those tests and verify they fail before adding compact projection.
- [x] Add a compact normalization branch; retain the existing complete normalization as the `all` branch.
- [x] Run `python -m pytest tests/test_exporter_storage.py -q` and verify passes.

### Task 2: Make each filtered export reflect exactly the current run

**Files:**
- Modify: `tests/test_exporter_storage.py`
- Modify: `src/intercom_exporter/storage.py`
- Modify: `src/intercom_exporter/exporter.py`

- [x] Add an exporter test that pre-populates the same period's JSONL with a conversation excluded by the current selection and verifies it is absent after export.
- [x] Run the targeted test and verify it fails because `merge` keeps prior unrelated records.
- [x] Add an atomic `JsonlStore.replace(records)` path using the existing temporary-file and `os.replace` approach; use it for exporter runs while leaving `merge` semantics unchanged.
- [x] Run storage and exporter tests; verify they pass and same-run duplicate IDs remain deduplicated.

### Task 3: Expose modes in CLI and manifest

**Files:**
- Modify: `tests/test_config_cli.py`
- Modify: `src/intercom_exporter/cli.py`
- Modify: `src/intercom_exporter/exporter.py`

- [x] Add dry-run tests for default `all`/`important`, explicit `new`/`updated`, and invalid option values.
- [x] Run `python -m pytest tests/test_config_cli.py -q`; verify option tests fail before CLI changes.
- [x] Add `--selection {new,updated,all}` and `--fields {important,all}` with defaults `all` and `important`; include their values in dry-run output and pass them into exporter.
- [x] Store selection and fields in manifest.
- [x] Run CLI and exporter tests; verify passes.

### Task 4: Document output schemas and verify the full change

**Files:**
- Modify: `README.md`

- [x] Document both CLI options, definitions of each selection mode, compact default schema, full schema, manifest fields, and atomic replacement behavior on rerun.
- [x] Run `python -m pytest -q` and `python -m compileall -q main.py src tests`.
- [x] Run `git diff --check`; inspect staged/unstaged files and ensure no `.env` or data outputs are included.
