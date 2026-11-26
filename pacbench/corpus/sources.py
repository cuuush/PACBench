"""Public observations: exactly what every memory system receives, and nothing more.

An `Observation` holds only public fields. There is no hidden data to strip before an
adapter sees one, because the object cannot carry an expectation in the first place —
ground truth lives in `rubric.Checkpoint`, a separate object that never leaves the
grader. That separation is structural, not a convention, so "the answer key leaked into
the corpus" is a class of bug the format makes unrepresentable.

Four clocks are kept distinct on purpose:

- ``written_at``   — when the sender authored the content.
- ``arrived_at``   — when it landed in the source (a late confirmation arrives after it
  was written).
- ``collected_at`` — when the harness is scheduled to expose it to the systems. An
  expectation follows this clock: before collection, no system can be expected to know
  the update; a failed collection after its scheduled moment is a real failure, not an
  excuse to weaken the key.

Channel and thread are provenance, never a tenant boundary — every channel resolves to
the same person's memory. `sender` keeps the source-provided identity; an email from
someone else is never quietly re-attributed to the owner.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

CHANNELS = ("email", "imessage", "text", "whatsapp", "groupme", "slack",
            "hermes", "agent")
#: Canonical channel for direct user conversations with the assistant. ``agent`` is
#: accepted as a historical alias for ``hermes`` so older corpus modules keep loading.
HERMES_CHANNEL = "hermes"
AGENT_ALIAS = "agent"


@dataclass(frozen=True)
class Observation:
    """One immutable record delivered at a collection boundary.

    Frozen so a variant (reorder, batch) that changes *arrival* can never reach back and
    mutate the shared source record — a sparse-variant mutation bug this format refuses
    to inherit.
    """

    source_id: str                 # stable and unique within a life
    channel: str                   # one of CHANNELS
    written_at: str                # ISO-8601; when the content was authored
    arrived_at: str                # ISO-8601; when the source received it
    collected_at: str              # ISO-8601; when the harness exposes it
    sender: str                    # source-provided identity, not normalized to the owner
    thread: str                    # provenance
    text: str
    from_owner: bool = False
    #: Structured payload only when it genuinely exists in the source (e.g. an iCal row),
    #: delivered identically to every adapter. Never a hidden entity id or inferred link.
    structured: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.channel not in CHANNELS:
            raise ValueError(f"unknown channel {self.channel!r}; expected one of {CHANNELS}")

    def public(self) -> dict:
        """The full record an adapter may see. Every field here is public by construction."""
        return asdict(self)


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


def read_spec(path):
    return parse_json_object(Path(path).read_bytes())


def parse_json_object(raw: bytes) -> dict:
    """Parse the same UTF-8 bytes callers verify or bind to a digest."""
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key!r}')
            result[key] = value
        return result
    spec = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
    if not isinstance(spec, dict):
        raise ValueError('Spec must be a JSON object')
    return spec
