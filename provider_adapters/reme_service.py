"""Run native ReMe HTTP service with a read-only resource completion observer.

ReMe exposes this result hook for embedding hosts. It records completed native
jobs; it neither calls the job nor writes cards or indexing state. The shipped
resource watcher remains enabled and owns ingestion.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid


def record_resource_result(directory, *, job_name, response, kwargs, metadata):
    if job_name != "auto_resource":
        return
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = response.model_dump(mode="json")
    value = {"protocol": "reme-native-resource-observer-v1",
             "observed_at": datetime.now(timezone.utc).isoformat(),
             "job_name": job_name, "changes": kwargs.get("changes", []),
             "response": result, "hook_metadata": metadata,
             "processing_forced": False}
    value["response_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()
    identity = uuid.uuid4().hex
    temporary = directory / (identity + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.chmod(0o600)
    temporary.rename(directory / (identity + ".json"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--receipts", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    from reme import Application
    from reme.config import resolve_app_config
    config = resolve_app_config(workspace_dir=str(args.workspace.resolve()),
        service={"host": "127.0.0.1", "port": args.port})
    app = Application(**config)

    async def observe(**kwargs):
        # The native resource action and index watcher are separate jobs. Wait
        # only for read-only witnesses; do not call reindex/update/flush.
        response = kwargs["response"]
        results = (response.metadata or {}).get("results") or []
        expected = [(item.get("metadata") or {}) for item in results]
        expected = [item for item in expected if item.get("path") and item.get("action") == "added"]
        witnesses = []
        store = app.context.components["file_store"]["default"]
        deadline = asyncio.get_running_loop().time() + 30
        while expected:
            witnesses = []
            for item in expected:
                nodes = await store.get_nodes([item["path"]])
                if len(nodes) == 1:
                    node = nodes[0]
                    witnesses.append({"path": node.path, "chunk_ids": list(node.chunk_ids),
                        "front_matter": node.front_matter.model_dump(mode="json")})
            if (len(witnesses) == len(expected) and all(w["chunk_ids"] and
                    w["front_matter"].get("source_resource") == item.get("source_resource")
                    for w, item in zip(witnesses, expected))):
                break
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(.1)
        metadata = {**kwargs["metadata"], "observed_index_nodes": witnesses,
                    "index_observation_complete": bool(expected) and len(witnesses) == len(expected)
                        and all(w["chunk_ids"] and w["front_matter"].get("source_resource") == item.get("source_resource")
                                for w, item in zip(witnesses, expected))}
        record_resource_result(args.receipts, **{**kwargs, "metadata": metadata})

    app.context.metadata["qwenpaw_memory_result_hook"] = observe
    app.run_app()


if __name__ == "__main__":
    main()
