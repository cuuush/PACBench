"""Hindsight provider adapter for PACBench live runs (criterion 3).

Provider-specific code lives here, OUTSIDE the ``pacbench`` package, so the
generic corpus/grader/runner never imports Hindsight.

Native lifecycle (nothing Memcal-shaped is forced onto it):

- Store: embedded PostgreSQL (``hindsight_api.pg0``) + ``MemoryEngine``
  with synchronous task backend (embedded use) and local embeddings —
  no Docker, no server daemon, no API key for vectors.
- Internal LLM: OpenAI-compatible endpoint from the environment
  (``HINDSIGHT_API_LLM_*``; the approved dev value points at the
  existing META gateway with a Spark model). Fail-closed when absent.
- Ingest: ONE ``retain_batch_async`` call per ingest batch (SMART
  day-batching): the runner groups a day's evidence into one transport
  batch, and the adapter sends it as ONE batch call — day 1's 100
  messages = 1 call, day 2's 100 new same-sender messages = 1 more
  call, delta-only, never cumulative re-ingest. The engine extracts
  per item in parallel inside that call (wall-clock win, one
  transaction) — batching saves round-trips, not model calls; day-level
  savings come from the delta cursor. Within the call, evidence is
  grouped by ``logical_document_key`` into conversation packets: one
  stable conversation ``document_id`` per conversational source with
  per-packet ``update_mode="append"`` for the day's newly exposed
  messages (a day-2 ``replace`` under a conversation id would wipe day
  1 — the replace-wipes-day-1 trap, P0-3 §2); email singletons keep
  per-message ids with ``update_mode="replace"`` (honest upsert).
  Each packet rides as exactly ONE content item (vendor docs: a full
  conversation is retained as a single item; the server refuses batches
  that share one document_id across items): the packet's messages
  serialized chronologically by ``written_at`` and joined as
  ``sender (written_at): text`` lines — verbatim, no summaries, no
  benchmark-event packetizing. The packet context names the thread and
  its senders; the earliest authoring ``written_at`` rides as
  ``event_date`` — Hindsight's native time anchor, not wall-clock
  (criterion 2: relative-date resolution against the fictional clock is
  an open verification item, probed like the memcal clock probe, not
  assumed).
- Scheduled work: ``run_graph_maintenance_job`` (relink/prune) — the
  native consolidation pass, analogue of memcal's dream, not a copy.
- Retrieval: Hindsight MCP tools (retain/recall/reflect) consumed by
  the isolated Hermes via ``hermes mcp`` — no plugin shim. Hindsight
  has no automatic brief context; Hermes calls recall explicitly.
  That capability difference is Hindsight's native shape and stays
  visible in retrieval(), never papered over.

Contract coverage mirrors the memcal adapter: §§28/29/31 (native tools
only, public observations only, user turns redirected), §42 (scratch
only, hidden keys refused, honest readiness), §44 C (reset, ingest,
maintenance, retrieval, cleanup, usage).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts"

from pacbench.adapters import ProviderAdapter, Readiness, Usage  # noqa: E402
from pacbench.resume import (  # noqa: E402
    _normalize_ts,
    ingest_boundary,
    same_operation_id,
)
from pacbench.source_documents import (  # noqa: E402
    SOURCE_DOCUMENT_MAPPING_VERSION,
    is_conversational,
    logical_document_key,
)

#: Channels that are live user turns, not ingested evidence (§31 analogue:
#: Hermes retains its own turns through the MCP tools at question time).
USER_TURN_CHANNELS = ("hermes", "agent")

#: Payload keys that must never cross the adapter boundary (§§29/42).
FORBIDDEN_OBS_KEYS = ("expected", "slots", "supported", "forbidden",
                      "answer_key", "support_manifest", "truth")

#: Hindsight MCP tools Hermes may call (native tool names).
HINDSIGHT_TOOL_ALLOWLIST = ("retain", "recall", "reflect")

META_GATEWAY_URL = "https://api.meta.ai/v1"
DEFAULT_INTERNAL_MODEL = "muse-spark-1.3"

#: Conversation-packet document identity (plan.md P0-2; P0-3 §§2-3).
#: Stable logical document IDs derived solely from public source
#: provenance (``pacbench.source_documents.logical_document_key``):
#: conversational channels share one id per conversation
#: (``{channel}:{thread}``); singleton channels (email) keep one id per
#: message (``email:{message-local-id}``). Stable across collection
#: cycles, resumes, repetitions, and checkpoint execution; encodes no
#: life/checkpoint/story/grading/timestamp-batch identity.
DOCUMENT_ID_SCHEME = (
    "logical-document-key-v1 "
    "(conversational: {channel}:{thread}; singleton: {channel}:{message-local-id})"
)

#: Per-packet update mode (P0-3 §2): conversational deltas APPEND to the
#: stable conversation document (replace would wipe earlier days);
#: singleton documents upsert in place.
UPDATE_MODE_CONVERSATIONAL = "append"
UPDATE_MODE_SINGLETON = "replace"

#: Packetization: each ingest batch is grouped by logical_document_key
#: into conversation packets; each packet rides as exactly ONE content
#: item (joined ``sender (written_at): text`` transcript) in ONE
#: retain_batch_async call per ingest call (SMART day-batching
#: transport is unchanged — grouping only, never re-ingest).
PACKETIZATION = (
    "one-joined-item-per-conversation-packet: group by logical_document_key, "
    "chronological within packet, one retain_batch_async per ingest call"
)

#: Idempotent operational retry identity (collected-at-definition.md):
#: simulated collection boundary, never wall-clock ingested_at.
OPERATION_ID_STRATEGY = "uuid5(NAMESPACE_URL, '{bank}:{collected_at-boundary}')"


def adapter_config() -> dict:
    """Adapter-config facts feeding the run-manifest digest (plan.md P0-2).

    ``memcal_recipe_version`` is filled in by ``pacbench.live`` (which
    owns the memcal checkout pin); this module reports the five
    Hindsight-side fields.
    """
    return {
        "source_document_mapping_version": SOURCE_DOCUMENT_MAPPING_VERSION,
        "hindsight_document_id_scheme": DOCUMENT_ID_SCHEME,
        "hindsight_update_mode": (
            f"{UPDATE_MODE_CONVERSATIONAL}:conversational/"
            f"{UPDATE_MODE_SINGLETON}:singleton"
        ),
        "packetization": PACKETIZATION,
        "operation_id_strategy": OPERATION_ID_STRATEGY,
    }


def adapter_config_digest(config: dict | None = None) -> str:
    """Stable hex digest of an adapter-config dict (manifests + refusal)."""
    cfg = config if config is not None else adapter_config()
    canonical = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_scratch_home(home: Path) -> Path:
    """Resolve `home` and refuse anything that is not scratch (§42)."""
    resolved = Path(os.path.realpath(os.path.expanduser(str(home))))
    live = Path.home() / ".hindsight"
    if resolved == live or live in resolved.parents:
        raise RuntimeError(f"refusing live HINDSIGHT_HOME: {home}")
    roots = []
    for cand in (tempfile.gettempdir(), "/tmp", "/var/tmp", "/private/tmp"):
        try:
            roots.append(Path(cand).resolve())
        except OSError:
            continue
    for owned in (ARTIFACTS, REPO / "runs"):
        try:
            roots.append(owned.resolve())
        except OSError:
            roots.append(owned)
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise RuntimeError(
        f"HINDSIGHT_HOME must be scratch (tmp or PACBench/artifacts/): {home}")


def _parse_event_date(written_at: str | None) -> datetime | None:
    if not written_at:
        return None
    try:
        return datetime.fromisoformat(written_at)
    except ValueError:
        return None


def _chrono_key(obs: dict) -> tuple:
    """Chronological sort key: (written_at, source_id) (P0-4).

    Time order wins over ``source_id`` order — lexicographic ids are a
    benchmark-authoring convention, never chronology. The ``source_id``
    tiebreak keeps same-instant messages deterministic. Aware stamps
    are normalized to naive UTC so mixed clocks never break compare.
    """
    dt = _parse_event_date(obs.get("written_at"))
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (dt if dt is not None else datetime.min,
            str(obs.get("source_id", "?")))


def _packet_context(channel: str, thread: str, senders: list[str],
                    group_flags: dict[tuple[str, str], bool] | None) -> str:
    """Extraction context for one conversation packet (CONTRACT §§8/29).

    Same thread identity both providers see: ``"<channel> thread
    <thread> from <sender, ...>"`` (packet senders in chronological
    first-appearance order), plus ``" (group chat)"`` when the
    runner's shared group flags mark the thread a group — the same
    signal memcal's gate/entity_for receives. Context shapes
    extraction (per the retain doc), so a group thread must read as
    one conversation to the extractor, not N disconnected DMs.
    """
    ctx = f"{channel} thread {thread} from {', '.join(senders)}"
    if (group_flags or {}).get((channel, thread)):
        ctx += " (group chat)"
    return ctx


class _LoopRunner:
    """One background event loop for all engine coroutines.

    The adapter's public surface is synchronous (ProviderAdapter); the
    engine is async with loop-bound pools, so every coroutine runs on
    this single owned loop via run_coroutine_threadsafe.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever,
                                        daemon=True)
        self._thread.start()

    def run(self, coro):
        assert self._loop is not None, "loop not started"
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(
            timeout=600)

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._thread = None
        self._loop = None


class HindsightAdapter(ProviderAdapter):
    """Hermes + Hindsight, backed by embedded PG in a scratch home."""

    name = "hindsight"

    #: Hindsight HAS native scheduled work (graph maintenance), so
    #: maintenance applies and readiness gates on it.
    maintenance_applicable = True

    def __init__(self, home: Path, *, llm_model: str | None = None,
                 engine_factory=None, pg_factory=None):
        check_scratch_home(home)
        self.home = home
        self.llm_model = llm_model or os.environ.get(
            "HINDSIGHT_API_LLM_MODEL", DEFAULT_INTERNAL_MODEL)
        self._engine_factory = engine_factory
        self._pg_factory = pg_factory
        self.namespace: str | None = None
        self._bank: str | None = None
        self._loop = _LoopRunner()
        self._pg = None
        self._engine = None
        self._ctx = None
        self._retained = 0
        self._redirected_turns = 0
        self._retain_calls = 0
        self._retain_in_tokens: int | None = None
        self._retain_out_tokens: int | None = None
        self._latency_s = 0.0
        self._maintenance: dict = {"state": "not-run"}

    def _llm_config(self) -> SimpleNamespace:
        key = os.environ.get("HINDSIGHT_API_LLM_API_KEY", "").strip()
        if not key:
            # Repo credential precedent (cf. creds.load_driver): the PACBench
            # .env META credential backs the internal LLM unless an explicit
            # HINDSIGHT_API_LLM_API_KEY overrides it. Still fail-closed when
            # no key exists anywhere.
            try:
                from pacbench.creds import DEFAULT_ENV
                from pacbench.generation import load_config
                _, key = load_config(DEFAULT_ENV)
                key = (key or "").strip()
            except Exception:
                key = ""
        if not key:
            raise RuntimeError(
                "HINDSIGHT_API_LLM_API_KEY is not set. Point it at an "
                "OpenAI-compatible chat endpoint (dev: the META credential) "
                "with HINDSIGHT_API_LLM_BASE_URL "
                f"(default {META_GATEWAY_URL}) and HINDSIGHT_API_LLM_MODEL "
                f"(default {DEFAULT_INTERNAL_MODEL}).")
        return SimpleNamespace(
            provider=os.environ.get("HINDSIGHT_API_LLM_PROVIDER", "openai"),
            api_key=key,
            base_url=os.environ.get("HINDSIGHT_API_LLM_BASE_URL",
                                    META_GATEWAY_URL),
            model=os.environ.get("HINDSIGHT_API_LLM_MODEL", self.llm_model))

    def reset(self, namespace: str) -> None:
        """Start a fresh embedded-PG instance + engine for one namespace.

        H2: reset is rerun-idempotent — it drops the bank's data first
        (delete_bank when an engine is already bound) so a same-namespace
        rerun cannot silently double-ingest against surviving rows.
        """
        check_scratch_home(self.home)
        if self._engine is not None and self._bank is not None:
            try:
                self._loop.run(self._engine.delete_bank(
                    self._bank, request_context=self._ctx))
            except Exception:
                pass  # fresh bank or already gone: reset still starts clean
        self.close()
        llm = self._llm_config()  # fail-closed before spending anything
        self._loop.start()
        from hindsight_api.pg0 import EmbeddedPostgres
        pg_name = f"pacbench-{namespace}"
        self._pg = (self._pg_factory(pg_name) if self._pg_factory
                    else EmbeddedPostgres(name=pg_name))
        self._loop.run(self._pg.start())
        db_url = self._loop.run(self._pg.get_uri())
        if self._engine_factory is not None:
            self._engine = self._engine_factory(db_url, llm)
        else:
            from hindsight_api.engine.embeddings import LocalSTEmbeddings
            from hindsight_api.engine.task_backend import SyncTaskBackend
            from hindsight_api.mcp_tools import MemoryEngine
            self._engine = MemoryEngine(
                db_url=db_url, embeddings=LocalSTEmbeddings(),
                memory_llm_provider=llm.provider,
                memory_llm_api_key=llm.api_key,
                memory_llm_model=llm.model,
                memory_llm_base_url=llm.base_url,
                task_backend=SyncTaskBackend(),
                skip_llm_verification=True)
        from hindsight_api.mcp_tools import RequestContext
        self._bank = f"pacbench-{namespace}"
        self._ctx = RequestContext(internal=True,
                                   allowed_bank_ids=[self._bank])
        self.namespace = namespace
        self._retained = 0
        self._redirected_turns = 0
        self._retain_calls = 0
        self._retain_in_tokens: int | None = None
        self._retain_out_tokens: int | None = None
        self._latency_s = 0.0
        self._maintenance = {"state": "not-run"}

    def close(self) -> None:
        try:
            self.stop_server()
        except Exception:
            pass
        try:
            if self._pg is not None:
                self._loop.run(self._pg.stop())
        except Exception:
            pass
        finally:
            self._pg = None
            self._engine = None
            self._ctx = None
            self._loop.stop()

    def ingest(self, observations: list[dict],
               collected_at: str,
               group_flags: dict[tuple[str, str], bool] | None = None) -> Usage:
        started = time.monotonic()
        for obs in observations:
            for key in FORBIDDEN_OBS_KEYS:
                if key in obs:
                    raise RuntimeError(
                        f"refusing non-public payload field {key!r} "
                        f"in observation {obs.get('source_id', '?')}")
        # Canonical ingest boundary (operation-id robustness): derive
        # the boundary from the actual supplied observations (canonical
        # chronological max over the batch's collected_at, via the shared
        # resume helper so runner and adapter can never drift), then
        # assert the caller-supplied collected_at agrees AFTER passing it
        # through the SAME normalizer — trailing space, ``+00:00``-vs-naive,
        # and offset respellings of one instant must never fork identity.
        # Any disagreement — or any batch with no derivable boundary —
        # fails closed: a wrong boundary would mint a wrong operation
        # identity and break idempotent resume.
        derived_boundary = ingest_boundary(observations)
        try:
            caller_boundary = _normalize_ts(collected_at)
        except ValueError as exc:
            raise ValueError(
                f"refusing ingest with unparsable collected_at boundary "
                f"{collected_at!r}: boundary must be ISO-8601") from exc
        if caller_boundary != derived_boundary:
            raise ValueError(
                f"refusing ingest with inconsistent collected_at boundary: "
                f"caller supplied {collected_at!r} (canonical "
                f"{caller_boundary!r}) but batch contents derive "
                f"{derived_boundary!r} (canonical chronological max over "
                f"{len(observations)} observation(s))")
        evidence = [o for o in observations
                    if o.get("channel") not in USER_TURN_CHANNELS]
        redirected = len(observations) - len(evidence)
        if self._engine is None:
            raise RuntimeError("adapter not reset: call reset() first")
        count = 0
        empty_ids = 0
        # Conversation packets (P0-2+P0-4; P0-3 §§2-4): group this ingest
        # batch by logical_document_key. Conversational sources share one
        # stable conversation document_id with update_mode="append" for
        # the day's newly exposed messages; email singletons keep
        # per-message ids with update_mode="replace" (honest upsert).
        # Each packet rides as exactly ONE content item: the packet's
        # messages serialize chronologically by written_at (deterministic
        # source_id tiebreak) and join as "sender (written_at): text"
        # lines — verbatim text/sender/timestamps/order/thread, no
        # summaries.
        #
        # SUPERSEDED (one-item-per-packet rewrite): the per-message-items
        # scheme (one item per message sharing the packet's document_id).
        # Vendor docs retain a full conversation as a single item, and
        # the server refuses batches that share one document_id across
        # items — multi-item packets returned empty. Before that, the
        # older per-message scheme (document_id = source_id,
        # engine-default update_mode "replace") was honest only while
        # each document held exactly one message; under
        # conversation-level identity a day-2 "replace" would wipe day 1
        # instead of appending (P0-3 §2 replace-wipes-day-1 trap).
        #
        # Transport is unchanged (ruling §1 + SMART day-batching): ONE
        # retain_batch_async per INGEST CALL — the caller (_batched_by
        # _collection in live.py) already groups a day's evidence into one
        # transport batch, and ingest() never re-splits: day 1's 100
        # messages = 1 call, day 2's 100 new same-sender messages = 1 more
        # call, delta-only via live.py's obs_idx cursor, never cumulative
        # re-ingest. Deterministic operation_id per INGEST CALL supports
        # idempotent OPERATIONAL retry only; semantic empties are
        # recorded, never retried (§2). The op-id stays
        # uuid5(bank:canonical-boundary) on the SIMULATED boundary —
        # the canonical boundary derived above from batch contents
        # (chronological max collected_at, caller agreement asserted
        # after the same normalization) — never wall-clock
        # ingested_at (collected-at-definition.md). ONE id per ingest
        # call (not per packet): the whole transport batch shares the
        # canonical bank:boundary identity via the shared
        # resume.same_operation_id helper, so a same-batch retry mints
        # the same identity automatically.
        #
        # Cost model (verified against the installed engine source):
        # batching wins WALL-CLOCK (one orchestrator pass, parallel
        # fact extraction, one embedding batch, one transaction), not
        # model calls — the engine still extracts per item. Day-level
        # savings come from live.py's delta cursor (ingest_until only
        # sends newly-exposed observations), never from re-sending
        # cumulative history. return_usage=True surfaces TokenUsage so
        # unknown stays unknown instead of unmeasured.
        #
        # Count semantics: counts are PACKETS (items), not messages — the
        # usage note reads "retained X/Y" where Y is packets submitted
        # and X packets with non-empty id returns.
        packets: dict[str, list[dict]] = {}
        for o in evidence:
            # Tolerate minimal fake payloads the way the old per-message
            # path did (o.get with "?" defaults); real corpus payloads
            # always carry all three. Unknown channels still fail closed
            # inside logical_document_key/is_conversational.
            key_obs = {"channel": o.get("channel", "?"),
                       "thread": o.get("thread", "?"),
                       "source_id": o.get("source_id", "?")}
            packets.setdefault(logical_document_key(key_obs), []).append(o)
        contents = []
        for doc_id in sorted(packets):
            members = packets[doc_id]
            conversational = is_conversational(
                str(members[0].get("channel", "?")))
            mode = (UPDATE_MODE_CONVERSATIONAL if conversational
                    else UPDATE_MODE_SINGLETON)
            ordered = sorted(members, key=_chrono_key)
            channel = str(ordered[0].get("channel", "?"))
            thread = str(ordered[0].get("thread", "?"))
            senders = list(dict.fromkeys(
                str(o.get("sender", "?")) for o in ordered))
            lines = [f"{o.get('sender', '?')} ({o.get('written_at')}): "
                     f"{o['text']}" for o in ordered]
            event_date = None
            for o in ordered:
                event_date = _parse_event_date(o.get("written_at"))
                if event_date is not None:
                    break
            contents.append(
                {"content": "\n".join(lines),
                 "context": _packet_context(channel, thread, senders,
                                            group_flags),
                 "event_date": event_date,
                 "metadata": {
                     "source_ids": [str(o.get("source_id", "?"))
                                    for o in ordered],
                     "thread": thread,
                     "senders": senders,
                     "written_at_start": ordered[0].get("written_at"),
                     "written_at_end": ordered[-1].get("written_at"),
                     "logical_document": doc_id,
                 },
                 "document_id": doc_id,
                 "update_mode": mode})
        if contents:
            per_item = self._loop.run(
                self._engine.retain_batch_async(
                    self._bank, contents, request_context=self._ctx,
                    return_usage=True,
                    operation_id=same_operation_id(
                        (self._bank, derived_boundary))))
            self._retain_calls += 1
            if isinstance(per_item, tuple):
                per_item, token_usage = per_item[0], per_item[1]
            else:
                token_usage = None
            if per_item and isinstance(per_item[0], str):
                per_item = [[ids] for ids in per_item]
            for ids in per_item or []:
                if ids:
                    count += 1
                else:
                    empty_ids += 1
        else:
            token_usage = None
        self._retained += count
        self._redirected_turns += redirected
        latency = round(time.monotonic() - started, 2)
        self._latency_s += latency
        # TokenUsage telemetry (return_usage=True): unknown stays unknown —
        # a missing/zero usage object records "unreported", never 0.
        in_tok: int | None = None
        out_tok: int | None = None
        if token_usage is not None:
            try:
                in_tok = int(token_usage.input_tokens or 0)
                out_tok = int(token_usage.output_tokens or 0)
            except Exception:
                in_tok = out_tok = None
        usage_note = (f"retained {count}/{len(contents)} "
                      f"({empty_ids} empty-id returns, recorded not retried); "
                      f"user_turns_redirected {redirected}")
        if in_tok is None:
            usage_note += "; retain TokenUsage unreported (unknown, never 0)"
        else:
            usage_note += f"; retain tokens {in_tok} in / {out_tok} out"
            assert in_tok is not None and out_tok is not None
            self._retain_in_tokens = (self._retain_in_tokens or 0) + in_tok
            self._retain_out_tokens = (self._retain_out_tokens or 0) + out_tok
        return Usage(
            model_calls=self._retain_calls, latency_s=latency,
            input_tokens=in_tok, output_tokens=out_tok,
            notes=(usage_note,))

    # -- scheduled processing (§44 C: native graph maintenance) --------------
    def maintenance(self, at: str) -> Usage:
        """One native graph-maintenance pass (relink/prune) over the bank.

        Hindsight's analogue of memcal's dream: consolidation the
        provider itself defines, not benchmark logic. Skips record honestly.
        """
        started = time.monotonic()
        if self._engine is None:
            raise RuntimeError("adapter not reset: call reset() first")
        try:
            from hindsight_api.engine.graph_maintenance import (
                run_graph_maintenance_job)
            result = self._loop.run(run_graph_maintenance_job(
                self._engine, self._bank, self._ctx,
                operation_id=f"pacbench-{at}"))
            self._maintenance = {"state": "complete", "at": at,
                                 "result": str(result)[:500]}
        except Exception as exc:
            self._maintenance = {"state": "skipped", "at": at,
                                 "reason": f"{type(exc).__name__}: {exc}"[:300]}
        latency = round(time.monotonic() - started, 2)
        self._latency_s += latency
        return Usage(model_calls=0, latency_s=latency,
                     notes=(f"maintenance: {self._maintenance['state']}",))

    # -- readiness (§44 C; §42: honest, never a fake pass) ------------------
    def readiness(self) -> Readiness:
        state = self._maintenance.get("state")
        if state == "complete":
            return Readiness(ready=True,
                             detail="graph maintenance pass finished")
        if state == "skipped":
            return Readiness(
                ready=False,
                detail="graph maintenance skipped: "
                       f"{self._maintenance.get('reason', '')}")
        return Readiness(ready=False, detail="maintenance has not run yet")

    # -- retrieval integration (§44 C; §28: MCP-native) ---------------------
    def retrieval(self) -> dict:
        return {
            "provider": self.name,
            "tool_allowlist": list(HINDSIGHT_TOOL_ALLOWLIST),
            "mcp_tools": list(HINDSIGHT_TOOL_ALLOWLIST),
            "plugin_available": False,
            "mcp_server": True,
            "bank_scoped": True,
            "transport": "MCP via `hermes mcp add` "
                         "(harness installs the server spec)",
            "server_entry": self.server_entry(),
            "server_note": ("adapter.start_server shares the run's PG; "
                            "bank-pinned /mcp/<bank>/"),
            "automatic_context": "none (native shape: Hermes calls "
                                 "recall/reflect explicitly; no brief "
                                 "prefetch — a real capability difference, "
                                 "not a handicap)",
            "server_package": "hindsight_api",
            "env": self.env(),
        }

    def tool_allowlist(self) -> tuple[str, ...]:
        return HINDSIGHT_TOOL_ALLOWLIST

    # -- MCP server (native retrieval transport) -------------------------------
    def mcp_url(self, port: int) -> str:
        if not self._bank:
            raise RuntimeError("adapter not reset: call reset() first")
        return f"http://127.0.0.1:{port}/mcp/{self._bank}/"

    def mcp_server_spec(self, port: int) -> dict:
        return {"name": "hindsight", "url": self.mcp_url(port)}

    def server_entry(self) -> str:
        """How this adapter's retrieval transport is launched (audit surface).

        The ONLY interpreter allowed here is the harness-pinned one —
        ``pacbench.harness.python_bin()`` (same binary that owns the
        hindsight_api install). A bare ``sys.executable`` silently
        inherits whatever interpreter launched the run (3.14 lacks the
        deps → ModuleNotFoundError mid-run); a hardcoded
        ``/usr/local/bin/python3`` rots on the next machine. Pinned in
        code, pinned by test, recorded in the retrieval seam.
        """
        from pacbench import harness as _harness
        return f"{_harness.python_bin()} -m hindsight_api.mcp_local"

    def start_server(self, port: int, timeout_s: float = 60.0) -> str:
        """Launch hindsight-local-mcp against this adapter's PG instance.

        Interpreter is the harness pin (python_bin), never sys.executable.
        """
        if self._engine is None or self._pg is None:
            raise RuntimeError("adapter not reset: call reset() first")
        from pacbench import harness as _harness
        llm = self._llm_config()
        db_url = self._loop.run(self._pg.get_uri())
        env = dict(os.environ)
        env.update({
            "HINDSIGHT_API_DATABASE_URL": db_url,
            "HINDSIGHT_API_PORT": str(port),
            "HINDSIGHT_API_LLM_API_KEY": llm.api_key,
            "HINDSIGHT_API_LLM_PROVIDER": llm.provider,
            "HINDSIGHT_API_LLM_MODEL": llm.model,
            "HINDSIGHT_API_LLM_BASE_URL": llm.base_url,
        })
        self._server = subprocess.Popen(
            [_harness.python_bin(), "-m", "hindsight_api.mcp_local"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port),
                                              timeout=2):
                    return self.mcp_url(port)
            except OSError:
                if self._server.poll() is not None:
                    raise RuntimeError(
                        "hindsight MCP server exited during startup")
                time.sleep(1.0)
        self.stop_server()
        raise RuntimeError("hindsight MCP server did not open "
                           f"127.0.0.1:{port} in time")

    def stop_server(self) -> None:
        server = getattr(self, "_server", None)
        if server is not None:
            try:
                server.terminate()
                server.wait(timeout=15)
            except Exception:
                try:
                    server.kill()
                except Exception:
                    pass
            self._server = None

    # -- cleanup (§44 C; §42) ------------------------------------------------
    def cleanup(self, namespace: str) -> None:
        check_scratch_home(self.home)
        if self.namespace is not None and namespace != self.namespace:
            raise RuntimeError(
                f"cleanup namespace mismatch: {namespace!r} != "
                f"{self.namespace!r}")
        self.close()

    # -- usage reporting (§44 C) ---------------------------------------------
    def usage(self) -> Usage:
        if self._retain_in_tokens is None:
            notes = (f"retained {self._retained}; "
                     f"user_turns_redirected {self._redirected_turns}; "
                     f"retain calls {self._retain_calls} "
                     f"(tokens unknown, never zero)")
        else:
            notes = (f"retained {self._retained}; "
                     f"user_turns_redirected {self._redirected_turns}; "
                     f"retain calls {self._retain_calls}; "
                     f"retain {self._retain_in_tokens} in / "
                     f"{self._retain_out_tokens} out")
        return Usage(
            model_calls=self._retain_calls,
            input_tokens=self._retain_in_tokens,
            output_tokens=self._retain_out_tokens,
            latency_s=round(self._latency_s, 2),
            cost_usd=None,
            notes=(notes,))

    def adapter_config(self) -> dict:
        """Hindsight-side adapter-config facts (plan.md P0-2 digest input).

        Duck-typed hook: ``pacbench.live`` reads this without importing
        any provider module, then adds ``memcal_recipe_version`` and
        hashes the merged dict into the run manifest.
        """
        return adapter_config()

    def env(self) -> dict:
        return {"HINDSIGHT_HOME": str(self.home),
                "HINDSIGHT_BANK": self._bank or "",
                "HINDSIGHT_API_LLM_MODEL": self.llm_model,
                "HINDSIGHT_API_LLM_BASE_URL": META_GATEWAY_URL}
