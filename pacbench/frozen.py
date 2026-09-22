"""Canonical frozen observation bundles (render-once / run-many, §5).

Authoring artifacts (hand-written lives, Spark render bundles) COMPILE
into one canonical JSON format. Scored runs consume ONLY that frozen
bundle: resolve artifact → verify content hash → record version+hash in
the immutable run manifest → fail closed on any mismatch. ``live.py``
must never reach backward into draft text or call Spark, and a missing
bundle is never silently regenerated.

Checkpoints and grading rubric stay in code (the measuring stick);
the bundle carries the public evidence (the thing measured).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

BUNDLE_VERSION = "0.2.0"

#: Public evidence keys frozen per observation (subset of Observation.public(),
#: §8 provenance + §15 authorship). from_owner/arrived_at/structured were
#: dropped in 0.1.0 — scored runs then lost authorship (memcal from_me always
#: False) and arrival time. 0.2.0 carries them; overlay fail-closes on version.
OBS_KEYS = ("source_id", "text", "channel", "sender", "thread",
            "written_at", "arrived_at", "collected_at",
            "from_owner", "structured")


def freeze_observations(lives: list[Any]) -> dict:
    """Compile lives into a canonical (unhashed) bundle dict."""
    frozen: dict[str, Any] = {"bundle_version": BUNDLE_VERSION, "lives": {}}
    for life in lives:
        obs = []
        for o in life.observations:
            d = o.public() if hasattr(o, "public") else dict(o)
            obs.append({k: d.get(k, "") for k in OBS_KEYS})
        obs.sort(key=lambda d: (d["collected_at"], d["source_id"]))
        frozen["lives"][life.id] = {
            "lineage": getattr(life, "lineage", ""),
            "observations": obs,
        }
    return frozen


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


def observations_for(bundle: dict, life_id: str) -> list[dict]:
    try:
        return bundle["lives"][life_id]["observations"]
    except KeyError:
        raise RuntimeError(
            f"life {life_id!r} not in frozen bundle "
            f"(lives: {sorted(bundle.get('lives', {}))})")
