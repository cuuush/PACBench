"""Provider-blind exposure ledger (plan.md P0-6).

At every collection boundary the runner offers one evidence batch to the
provider. This module digests exactly what was OFFERED — before any
provider packaging — so a Memcal run and a Hindsight run over the same
life/configuration can prove they saw the same public evidence.
Different provider packaging is allowed; different evidence is not.

Canonical form: each offered observation projected to the 10 public
fields below, batch order preserved (message order is evidence, P0-4).
Provider-private fields (native document ids, op-ids, MEMCAL_TODAY,
gate verdicts, entity keys) must never reach this digest: projection
drops them by construction, and :func:`check_offered_batch` fails
closed if they ever appear in an offered batch.

Pure module: no provider imports, no I/O, offline-testable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

#: The 10 public evidence fields digested at every collection boundary
#: (plan.md P0-6: source identity, channel, thread, sender, text,
#: written time, arrival time, collected/discovery time, plus the two
#: authorship-relevant public observation fields: ``from_owner``
#: (source-role bit) and ``structured`` (genuine source payload, e.g.
#: an iCal row). Both ride along in the offered payloads and are now
#: pinned here too, so a source-role flip moves the digest and fails
#: the equivalence gate — not just the frozen-bundle hash (§5).
EXPOSURE_DIGEST_FIELDS = (
    "source_id",
    "channel",
    "thread",
    "sender",
    "text",
    "written_at",
    "arrived_at",
    "collected_at",
    "from_owner",
    "structured",
)

#: Provider-private keys that must never appear in an offered batch or
#: the digest input: native document ids, operation ids, the memcal
#: ingestion-day pin, dispatch-gate verdicts, provider entity keys.
PROVIDER_PRIVATE_FIELDS = frozenset({
    "document_id",
    "op_id",
    "op_ids",
    "operation_id",
    "operation_ids",
    "MEMCAL_TODAY",
    "memcal_today",
    "gate_verdict",
    "gate_verdicts",
    "entity_key",
    "entity_keys",
})


def check_offered_batch(batch: list[dict]) -> None:
    """Fail closed if an offered batch carries provider-private fields.

    Offered payloads are ``Observation.public()`` (or frozen-bundle
    rows): public by construction. A private key here means provider
    packaging leaked into the evidence side of the boundary — refuse
    before the adapter ever sees the batch.
    """
    for obs in batch:
        bad = sorted(PROVIDER_PRIVATE_FIELDS.intersection(obs))
        if bad:
            raise RuntimeError(
                f"offered batch is not provider-blind: observation "
                f"{obs.get('source_id', '?')!r} carries private "
                f"fields {bad}")


def exposure_canonical(batch: list[dict]) -> list[dict]:
    """Project one offered batch to the 10-field canonical form.

    Batch order is preserved (order is evidence). Only the
    :data:`EXPOSURE_DIGEST_FIELDS` keys survive — anything else,
    including provider-private fields, is dropped, so the digest can
    never fingerprint provider packaging.
    """
    return [{k: obs.get(k) for k in EXPOSURE_DIGEST_FIELDS}
            for obs in batch]


def exposure_digest(batch: list[dict]) -> str:
    """Stable sha256 hex over one offered batch's canonical form."""
    canonical = exposure_canonical(batch)
    raw = json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _boundary_key(entry: dict, idx: int) -> str:
    return str(entry.get("batch_day")
               or entry.get("collected_at")
               or f"batch-{idx}")


def compare_exposure(manifest_a: dict, manifest_b: dict) -> dict:
    """Compare two run manifests' per-boundary exposure ledgers.

    Returns ``{"match": bool, "boundaries": [keys...], "mismatches":
    [...]}``. Anything but an exact per-boundary match — different
    boundary count, different boundary keys, different observation
    sets/order, different digests, or a boundary with no digest at all
    (pre-P0-6 manifest) — is a mismatch entry. Never silently matches:
    use :func:`pacbench.report.check_exposure_equivalence` to fail
    closed on ``match == False``.
    """
    batches_a = manifest_a.get("import_batches", [])
    batches_b = manifest_b.get("import_batches", [])
    mismatches: list[dict] = []
    if len(batches_a) != len(batches_b):
        mismatches.append({
            "boundary": "(ledger)",
            "reason": "boundary count differs",
            "detail": f"{len(batches_a)} vs {len(batches_b)}",
        })
    boundaries: list[str] = []
    for idx, (ea, eb) in enumerate(zip(batches_a, batches_b)):
        key = _boundary_key(ea, idx)
        boundaries.append(key)
        for field in ("batch_day", "collected_at", "observations"):
            if ea.get(field) != eb.get(field):
                mismatches.append({
                    "boundary": key,
                    "reason": f"{field} differs",
                    "detail": f"{ea.get(field)!r} vs {eb.get(field)!r}",
                })
        da, db = ea.get("exposure_digest"), eb.get("exposure_digest")
        if not da or not db:
            mismatches.append({
                "boundary": key,
                "reason": "missing exposure digest (pre-P0-6 manifest?)",
                "detail": f"{str(da)[:12]!r} vs {str(db)[:12]!r}",
            })
        elif da != db:
            mismatches.append({
                "boundary": key,
                "reason": "exposure digest differs (different evidence offered)",
                "detail": f"{da[:12]} vs {db[:12]}",
            })
    return {"match": not mismatches,
            "boundaries": boundaries,
            "mismatches": mismatches}
