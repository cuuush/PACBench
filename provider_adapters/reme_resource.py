"""Deliver unmodified documents to ReMe's native watched resource directory."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import time

from pacbench.stop import check_stop
from provider_adapters.reme_memos_documents import document


def inside(workspace, value):
    if not isinstance(value, str) or not value:
        raise ValueError("Unsafe native ReMe artifact path")
    path = workspace / value
    if not path.resolve().is_relative_to(workspace.resolve()) or path.is_symlink():
        raise ValueError("Unsafe native ReMe artifact path")
    return path


def ingest(source, root, *, timeout=600):
    text, manifest, digest = document(Path(source))
    root = Path(root)
    config = json.loads((root / "reme-resource-config.json").read_text())
    workspace = Path(config["workspace"])
    receipts = Path(config["receipts"])
    if workspace.resolve() != (root / "reme-workspace").resolve() or receipts.resolve() != (root / "reme-native-resource-results").resolve():
        raise ValueError("ReMe native store is outside this run")
    day = datetime.fromisoformat(manifest["at"]).date().isoformat()
    relative = f"resource/{day}/source-{digest}.txt"
    destination = inside(workspace, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Resource already exists; uncertain ingestion must not be resubmitted")
    baseline = {p.name for p in receipts.glob("*.json")}
    # Rename publishes one complete file to the unmodified resource watcher.
    temporary = destination.with_suffix(".pending")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(text)
    temporary.rename(destination)
    result = {"interface": "ReMe native auto_resource watcher", "ready": False,
        "ingestion_condition": "native-resource-document-extraction-v1",
        "document_sha256": digest, "source_ids": manifest["source_ids"],
        "resource_path": relative, "manual_job_invocations": 0,
        "native_defaults": True, "source_at": manifest["at"]}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_stop()
        for path in receipts.glob("*.json"):
            if path.name in baseline:
                continue
            receipt = json.loads(path.read_text())
            matches = [item for item in receipt.get("response", {}).get("metadata", {}).get("results", [])
                       if item.get("path") == relative]
            if not matches:
                continue
            if len(matches) != 1:
                raise ValueError("Duplicate native resource result")
            item = matches[0]
            metadata = item.get("metadata") or {}
            result.update(native_result=receipt, native_result_file=path.name)
            if item.get("success") is not True or receipt.get("hook_metadata", {}).get("index_observation_complete") is not True:
                result["readiness"] = "Native extraction or observed search indexing did not complete"
                return result
            note = inside(workspace, metadata.get("path", ""))
            expected_link = f"[[{relative}]]"
            if (metadata.get("source_resource") != expected_link or not note.is_file() or
                    expected_link not in note.read_text() or destination.read_text() != text):
                raise ValueError("Native resource/card provenance or source bytes differ")
            result.update(ready=True, card_path=metadata["path"],
                card_sha256=hashlib.sha256(note.read_bytes()).hexdigest(),
                card_text=note.read_text(),
                stored_source_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                source_readback_exact=True,
                readiness="Native resource action succeeded; source-linked generated card and actual indexed chunk IDs witnessed")
            return result
        time.sleep(.2)
    result["readiness"] = "Native resource processing deadline exceeded; no resubmission"
    return result
