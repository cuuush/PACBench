"""Public native document APIs for local ReMe and MemOS.

ReMe performs native auto-memory extraction. MemOS's native import stores raw
document traces; retrieval from those traces is source-assisted, not extracted
retention. Neither path fabricates an assistant answer or seeds scored facts.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pacbench.reme_memos_setup import local_endpoint


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def document(source: Path) -> tuple[str, dict, str]:
    text = (source / "messages.txt").read_text(encoding="utf-8")
    manifest = json.loads((source / "source.json").read_text())
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest != manifest["document_sha256"]:
        raise ValueError("Source document differs from frozen manifest")
    if not text.strip() or not manifest.get("source_ids"):
        raise ValueError("Empty source document")
    stamp = datetime.fromisoformat(manifest["at"])
    if stamp.utcoffset() is None:
        raise ValueError("Source clock must be timezone-aware")
    return text, manifest, digest


def payload(provider: str, source: Path) -> tuple[dict, dict]:
    text, manifest, digest = document(source)
    identity = "pacbench-document-" + digest
    evidence = {"document_sha256": digest, "source_ids": manifest["source_ids"],
                "source_at": manifest["at"], "native_id": identity}
    if provider == "reme":
        body = {"request_id": identity, "session_id": identity,
                "date": datetime.fromisoformat(manifest["at"]).date().isoformat(),
                "messages": [{"id": identity, "name": "source-document", "role": "user",
                              "content": text, "created_at": manifest["at"]}]}
        return body, {**evidence, "interface": "ReMe auto_memory", "ingestion_condition": "native-document-extraction-v1"}
    if provider == "memos":
        trace = {"id": identity, "sessionId": identity, "episodeId": identity,
                 "ts": int(datetime.fromisoformat(manifest["at"]).timestamp() * 1000),
                 "userText": text, "agentText": "", "summary": text, "toolCalls": [],
                 # Same neutral import priorities used by MemOS's shipped
                 # raw OpenClaw importer, not benchmark fact/answer weighting.
                 "value": 0.5, "alpha": 0.5, "priority": 0.5,
                 "turnId": int(datetime.fromisoformat(manifest["at"]).timestamp() * 1000)}
        return {"version": 1, "traces": [trace]}, {
            **evidence, "interface": "MemOS /api/v1/import",
            "ingestion_condition": "native-raw-document-import-v1",
            "retrieval_credit": "source-assisted", "extraction_performed": False}
    raise ValueError("Unknown provider")


def request(endpoint: str, path: str, body: dict | None, timeout: float) -> dict:
    base = local_endpoint(endpoint)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = Request(base + path, data=data, headers={"Content-Type": "application/json", "Accept": "application/json"})
    # No automatic retries: an interrupted write may already have executed.
    with build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("Native API returned a non-object result")
    return result


def ingest(provider: str, source: Path, endpoint: str, *, timeout: float = 600) -> dict:
    body, receipt = payload(provider, source)
    receipt = {**receipt, "ready": False}
    try:
        if provider == "reme":
            result = request(endpoint, "/auto_memory", body, timeout)
            receipt["native_result"] = result
            receipt["ready"] = result.get("success") is True
            receipt["readiness"] = "Synchronous native auto_memory action completed; fresh recall still required"
        else:
            result = request(endpoint, "/api/v1/import?ownership=target", body, timeout)
            receipt["native_result"] = result
            acceptable = (result.get("invalid") == 0 and result.get("failed") == 0
                          and result.get("imported", 0) + result.get("duplicates", 0) == 1)
            if acceptable:
                actual = request(endpoint, "/api/v1/memory/trace?id=" + receipt["native_id"], None, timeout)
                receipt['native_readback']=actual
                expected = body["traces"][0]
                receipt["native_readback_exact"] = all(actual.get(key) == expected[key]
                    for key in ("id", "sessionId", "episodeId", "ts", "userText", "agentText", "summary",
                                "value", "alpha", "priority", "turnId"))
                receipt["ready"] = receipt["native_readback_exact"]
            receipt["readiness"] = "Native raw document import plus exact API readback; no extraction claim"
    except Exception as error:
        receipt.update(native_error_class=type(error).__name__, outcome="uncertain-or-failed-no-retry")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=("reme", "memos"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    receipt = ingest(args.provider, args.source, args.endpoint, timeout=args.timeout)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
