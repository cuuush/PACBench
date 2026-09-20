"""Corpus freezing and versioning.

A frozen corpus is a canonical JSON bundle of every life (observations, hidden
rubrics, world truth) plus a manifest carrying the package version, the corpus
version, a UTC timestamp, and the sha256 of the canonical bytes. The support
manifests are authoring aids and are *excluded* from the bundle — freezing records
what a scored run may see, nothing more.

`PINNED_HASH` records the hash of the released v0.1 corpus. Any content change
moves the hash and fails `test_freeze_hash_pinned` until the pin is deliberately
updated, which is the point: silent corpus drift must be impossible.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone

from . import CORPUS_VERSION, VERSION
from .life import Life

PINNED_HASH = "b8b304ab5620beffb6a0891395706164577fb884b4376aa06dcc22e0a81b7f24"


def canonical(lives: list[Life]) -> bytes:
    """Canonical JSON bytes for a corpus: sorted keys, stable life order."""
    payload = []
    for life in sorted(lives, key=lambda l: l.id):
        payload.append({
            "id": life.id,
            "bet": life.bet,
            "role": life.role,
            "title": life.title,
            "horizon_days": life.horizon_days,
            "difficulty_tags": list(life.difficulty_tags),
            "control_of": life.control_of,
            "observations": [dataclasses.asdict(o)
                             for o in sorted(life.observations,
                                             key=lambda o: o.source_id)],
            "checkpoints": [dataclasses.asdict(c) for c in life.checkpoints],
            "truth": [dataclasses.asdict(t) for t in life.truth],
        })
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def digest(lives: list[Life]) -> str:
    """sha256 of the canonical corpus bytes."""
    return hashlib.sha256(canonical(lives)).hexdigest()


def freeze(lives: list[Life]) -> dict:
    """Build the freeze record (bundle excluded; hash covers it)."""
    bundle_hash = digest(lives)
    return {
        "pacbench_version": VERSION,
        "corpus_version": CORPUS_VERSION,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "lives": sorted(life.id for life in lives),
        "observations": sum(len(life.observations) for life in lives),
        "checkpoints": sum(len(life.checkpoints) for life in lives),
        "sha256": bundle_hash,
    }


def write_freeze(lives: list[Life], bundle_path: str, manifest_path: str) -> dict:
    """Write the canonical bundle and its manifest to disk; return the manifest."""
    record = freeze(lives)
    with open(bundle_path, "wb") as fh:
        fh.write(canonical(lives))
    with open(manifest_path, "w") as fh:
        json.dump(record, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return record


if __name__ == "__main__":
    from .corpus import all_lives

    lives = all_lives()
    record = freeze(lives)
    print(json.dumps(record, indent=2, sort_keys=True))
