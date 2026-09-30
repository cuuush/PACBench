"""Hash and verify the messages frozen before provider runs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_bytes(bundle: dict) -> bytes:
    """Deterministic bytes for hashing (never includes the hash field)."""
    stripped = {k: v for k, v in bundle.items() if k != "bundle_hash"}
    return json.dumps(stripped, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def bundle_hash(bundle: dict) -> str:
    return hashlib.sha256(canonical_bytes(bundle)).hexdigest()


def write_bundle(bundle: dict, path: Path | str) -> str:
    """Attach the content hash and write. Returns the hash."""
    out = dict(bundle)
    out["bundle_hash"] = bundle_hash(bundle)
    Path(path).write_text(json.dumps(out, indent=2, sort_keys=True,
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
    return out["bundle_hash"]


def load_bundle(path: Path | str, expected_hash: str) -> dict:
    """Load and verify. Fail closed: missing file, bad JSON, no hash
    field, or hash mismatch all raise (never regenerate, never proceed)."""
    path = Path(path)
    if not path.is_file():
        raise RuntimeError(
            f"frozen bundle missing: {path} (never silently rerender)")
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"frozen bundle unparsable: {path}: {exc}")
    actual = bundle.get("bundle_hash", "")
    if not actual or actual != bundle_hash(bundle):
        raise RuntimeError(
            f"frozen bundle hash mismatch: {path} "
            f"(file claims {actual[:12] if actual else 'none'})")
    if actual != expected_hash:
        raise RuntimeError(
            f"frozen bundle is not the pinned artifact: {path} "
            f"(got {actual[:12]}, want {expected_hash[:12]})")
    return bundle


def messages_for(bundle: dict, life_id: str) -> list[dict]:
    try:
        life = bundle["lives"][life_id]
        if "messages" in life:
            return life["messages"]
        return life["observations"]  # saved 0.2 bundles retain their hashes
    except KeyError:
        raise RuntimeError(
            f"life {life_id!r} not in frozen bundle "
            f"(lives: {sorted(bundle.get('lives', {}))})")


observations_for = messages_for  # old Python callers
