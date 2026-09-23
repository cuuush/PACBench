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

from .difficulty import flag_depth_mismatch, measured_difficulty

BUNDLE_VERSION = "0.2.0"

#: Public evidence keys frozen per observation (subset of Observation.public(),
#: §8 provenance + §15 authorship). from_owner/arrived_at/structured were
#: dropped in 0.1.0 — scored runs then lost authorship (memcal from_me always
#: False) and arrival time. 0.2.0 carries them; overlay fail-closes on version.
OBS_KEYS = ("source_id", "text", "channel", "sender", "thread",
            "written_at", "arrived_at", "collected_at",
            "from_owner", "structured")


def check_freezeable(lives: list[Any]) -> None:
    """Build-time validation for the freeze path. Unconditional, no bypass:
    every observation thread must already be canonical (stripped lowercase —
    the freeze never silently merges a case-split thread), no thread may
    carry the same text twice verbatim, and each thread's written timestamps
    must be non-decreasing. Raises :class:`RuntimeError` on any breach."""
    from .authoring import norm_thread
    problems: list[str] = []
    for life in lives:
        by_thread: dict[str, list] = {}
        for o in life.observations:
            if o.thread != norm_thread(o.thread) or not o.thread:
                problems.append(
                    f"{life.id}: {o.source_id} thread {o.thread!r} is not "
                    f"canonical (want {norm_thread(o.thread)!r})")
            by_thread.setdefault(o.thread, []).append(o)
        for thread, obs in by_thread.items():
            seen: dict[str, str] = {}
            for o in obs:
                key = o.text.strip().lower()
                if key in seen:
                    problems.append(
                        f"{life.id}: thread {thread!r} verbatim dupe "
                        f"{o.source_id} == {seen[key]}")
                else:
                    seen[key] = o.source_id
            stamps = [o.written_at for o in obs]
            if stamps != sorted(stamps):
                problems.append(
                    f"{life.id}: thread {thread!r} timestamps out of order")
    if problems:
        raise RuntimeError("freeze refused:\n" + "\n".join(problems[:20]))


def freeze_observations(lives: list[Any]) -> dict:
    """Compile lives into a canonical (unhashed) bundle dict.

    Each life entry carries a freeze-time ``measured`` difficulty block
    (computed from the frozen observations + support graph, never from
    author-declared tags) plus a ``difficulty`` side-by-side of requested
    tags vs measured values with the depth-mismatch flag — so a
    depth-8 request rendering depth 7 is recorded as such and can never
    silently enter the depth-8 bucket.
    """
    check_freezeable(lives)
    frozen: dict[str, Any] = {"bundle_version": BUNDLE_VERSION, "lives": {}}
    for life in lives:
        obs = []
        for o in life.observations:
            d = o.public() if hasattr(o, "public") else dict(o)
            obs.append({k: d.get(k, "") for k in OBS_KEYS})
        obs.sort(key=lambda d: (d["collected_at"], d["source_id"]))
        measured = measured_difficulty(life)
        mismatch = flag_depth_mismatch(life, measured)
        frozen["lives"][life.id] = {
            "lineage": getattr(life, "lineage", ""),
            "observations": obs,
            "measured": measured,
            "difficulty": {
                "requested_tags": list(getattr(life, "difficulty_tags", ())
                                       or ()),
                "measured_update_depth": measured["measured_update_depth"],
                "depth_mismatch": mismatch["has_mismatch"],
                "depth_detail": mismatch["mismatches"],
            },
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
