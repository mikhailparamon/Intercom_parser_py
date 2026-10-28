import json
import os
import tempfile
from pathlib import Path
from typing import Iterable


class JsonlStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def merge(self, records: Iterable[dict]) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stage_path = None
        final_path = None
        new_ids: set[str] = set()
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=self.path.parent,
                                             prefix=".conversations-", suffix=".tmp", delete=False) as stage:
                stage_path = Path(stage.name)
                for record in records:
                    record_id = str(record["id"])
                    if record_id in new_ids:
                        continue
                    new_ids.add(record_id)
                    stage.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=self.path.parent,
                                             prefix=".merged-", suffix=".tmp", delete=False) as final:
                final_path = Path(final.name)
                with stage_path.open("r", encoding="utf-8") as stage:
                    for line in stage:
                        final.write(line)
                old_ids: set[str] = set()
                if self.path.exists():
                    with self.path.open("r", encoding="utf-8") as previous:
                        for line in previous:
                            try:
                                item = json.loads(line)
                                item_id = str(item["id"])
                            except (json.JSONDecodeError, KeyError, TypeError):
                                raise ValueError("Существующий JSONL повреждён; предыдущая выгрузка сохранена без изменений")
                            if item_id not in new_ids and item_id not in old_ids:
                                final.write(line if line.endswith("\n") else line + "\n")
                                old_ids.add(item_id)
            os.replace(final_path, self.path)
            return len(new_ids)
        finally:
            for temporary in (stage_path, final_path):
                if temporary and temporary.exists():
                    temporary.unlink()

    def write_manifest(self, manifest: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=self.path.parent,
                                             prefix=".manifest-", suffix=".tmp", delete=False) as handle:
                temp_path = Path(handle.name)
                json.dump(manifest, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_path, self.path.parent / "manifest.json")
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink()
