"""Повторная семантическая проверка «Прочих» через отдельные задания агентам."""

import hashlib
import json
from pathlib import Path

from .classification_catalog import SCHEMA_VERSION, catalog_prompt, get_subcategories

from .support_analysis import (
    AnalysisError, OTHER_CATEGORY, OTHER_SUBCATEGORIES, _validate_result, _write_json, _write_text,
)


def review_other_classifications(
    classifications: dict[str, dict], payload: dict, output_dir: str | Path,
    taxonomy: list[dict], workers: int, service_id: str | None = None,
    schema_version: int = 1,
) -> dict:
    candidates = [c for c in payload["conversations"] if classifications[c["id"]]["category"] == OTHER_CATEGORY]
    if not candidates:
        return {"review_required": False, "classifications": classifications}

    template_name = "support_analysis_prompt_template_hypertube.md" if service_id == "hypertube" else "support_analysis_prompt_template.md"
    template = Path(__file__).with_name(template_name).read_text(encoding="utf-8")
    policy = template[template.index("## 1. Безопасность и единица анализа"):template.index("## 8. Формат `chatgpt_result.json`")].replace("[[SUBCATEGORY_CATALOG]]", catalog_prompt(service_id or "vpn"))
    protocol = Path(__file__).with_name("other_review_protocol.md").read_text(encoding="utf-8")
    workers = min(workers, len(candidates))
    fingerprint = hashlib.sha256(json.dumps({
        "dataset": payload["dataset_fingerprint"], "taxonomy": taxonomy,
        "classifications": [classifications[c["id"]] for c in candidates], "policy": policy, "protocol": protocol,
        "workers": workers, "schema_version": schema_version,
    }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    output = Path(output_dir)
    work = output / "agents_work" / "other_review" / fingerprint[:20]
    work.mkdir(parents=True, exist_ok=True)
    _write_json(work / "taxonomy.json", {"categories": taxonomy, "subcategories": get_subcategories(service_id or "vpn")})
    _write_text(work / "PROMPT.md", protocol + "\n\n" + policy)
    _write_text(work / "agents_protocol.md", protocol)
    chunks = []
    for index in range(workers):
        conversations = candidates[index::workers]
        chunk_path = work / f"chunk_{index + 1:02d}.json"
        _write_json(chunk_path, {
            "review_fingerprint": fingerprint, "conversation_count": len(conversations),
            "conversations": conversations,
            "previous_classifications": [classifications[c["id"]] for c in conversations],
        })
        chunks.append({
            "worker": index + 1, "chunk_path": str(chunk_path),
            "result_path": str(work / f"result_{index + 1:02d}.json"),
            "conversation_count": len(conversations),
        })
    manifest_path = work / "manifest.json"
    _write_json(manifest_path, {
        "stage": "other_review", "review_fingerprint": fingerprint, "workers": workers,
        "classification_schema_version": schema_version,
        "unique_conversation_count": len(candidates), "work_dir": str(work),
        "prompt_path": str(work / "PROMPT.md"), "taxonomy_path": str(work / "taxonomy.json"), "chunks": chunks,
    })
    ready = sum(Path(entry["result_path"]).is_file() for entry in chunks)
    if ready < workers:
        return {"review_required": True, "review_manifest_path": str(manifest_path),
                "review_count": len(candidates), "review_ready": ready, "review_workers": workers}

    reviewed = dict(classifications)
    decisions = []
    for entry in chunks:
        chunk = json.loads(Path(entry["chunk_path"]).read_text(encoding="utf-8"))
        path = Path(entry["result_path"])
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(f"Невалидный результат повторной проверки: {path}") from exc
        if not isinstance(data, dict) or data.get("review_fingerprint") != fingerprint:
            raise AnalysisError(f"Повторная проверка: неверный отпечаток набора в {path}")
        _, normalized = _validate_result(chunk["conversations"], {"dataset_fingerprint": fingerprint}, {
            "classification_schema_version": schema_version, "service_id": service_id or "vpn",
            "complete": True, "conversation_count": len(chunk["conversations"]), "dataset_fingerprint": fingerprint,
            "categories": taxonomy, "classifications": data.get("classifications"),
            "executive_summary": "Повторная проверка", "recommendations": [],
        })
        by_id = {c["id"]: c for c in chunk["conversations"]}
        reasons = {str(c["id"]): c.get("review_reason") for c in data["classifications"]}
        for item in normalized:
            cid = item["id"]
            reason = reasons[cid]
            if not isinstance(reason, str) or not reason.strip():
                raise AnalysisError(f"Повторная проверка {cid}: не заполнено review_reason")
            if item["category"] == OTHER_CATEGORY:
                allowed = {"Нет содержательного запроса", "Автоматическое, нецелевое сообщение или спам"} if schema_version >= SCHEMA_VERSION else OTHER_SUBCATEGORIES
                if item["subcategory"] not in allowed:
                    raise AnalysisError(f"Повторная проверка {cid}: у «Прочих» нужна конкретная причина из списка {OTHER_SUBCATEGORIES}")
            else:
                conversation = by_id[cid]
                texts = [conversation.get("title", "")] + [m["text"] for m in conversation["messages"] if m["role"] == "customer"]
                quote = " ".join(item["evidence"].split()).casefold()
                if not quote or not any(quote in " ".join(text.split()).casefold() for text in texts):
                    raise AnalysisError(f"Повторная проверка {cid}: evidence не найдено в тексте клиента или заголовке")
            decisions.append({"id": cid, "previous_category": classifications[cid]["category"],
                              "previous_subcategory": classifications[cid].get("subcategory"),
                              "category": item["category"], "subcategory": item["subcategory"],
                              "review_reason": reason.strip(), "evidence": item["evidence"]})
            reviewed[cid] = item
    after_count = sum(c["category"] == OTHER_CATEGORY for c in reviewed.values())
    audit_path = output / "other_review_audit.json"
    _write_json(audit_path, {"review_fingerprint": fingerprint, "before_count": len(candidates),
                             "after_count": after_count, "reclassified_count": len(candidates) - after_count,
                             "decisions": decisions})
    return {"review_required": False, "classifications": reviewed, "other_review_audit_path": str(audit_path)}
