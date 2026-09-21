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
- Ingest: ``retain_async`` per public observation with the authoring
  ``written_at`` as ``event_date`` — Hindsight's native time anchor,
  not wall-clock (criterion 2: relative-date resolution against the
  fictional clock is an open verification item, probed like the
  memcal clock probe, not assumed).
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
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts"

from pacbench.adapters import ProviderAdapter, Readiness, Usage  # noqa: E402

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
        """Start a fresh embedded-PG instance + engine for one namespace."""
        check_scratch_home(self.home)
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
               collected_at: str) -> Usage:
        started = time.monotonic()
        for obs in observations:
            for key in FORBIDDEN_OBS_KEYS:
                if key in obs:
                    raise RuntimeError(
                        f"refusing non-public payload field {key!r} "
                        f"in observation {obs.get('source_id', '?')}")
        evidence = [o for o in observations
                    if o.get("channel") not in USER_TURN_CHANNELS]
        redirected = len(observations) - len(evidence)
        if self._engine is None:
            raise RuntimeError("adapter not reset: call reset() first")
        count = 0
        empty_ids = 0
        # Native batch path (ruling §1): one retain_batch_async per
        # collection instant; every original message preserved verbatim
        # (text, sender, timestamps, ordering, thread) — no summaries,
        # no packetizing by hidden benchmark events. Deterministic
        # operation_id per packet supports idempotent OPERATIONAL retry
        # only; semantic empties are recorded, never retried (§2).
        groups: dict[str, list[dict]] = {}
        for obs in evidence:
            groups.setdefault(
                str(obs.get("collected_at", collected_at)), []).append(obs)
        for group_at in sorted(groups):
            contents = [
                {"content": o["text"],
                 "context": f"{o.get('channel', '?')} from "
                            f"{o.get('sender', '?')}",
                 "event_date": _parse_event_date(o.get("written_at")),
                 "metadata": {"source_id": str(o.get("source_id", "?")),
                              "thread": str(o.get("thread", "?")),
                              "sender": str(o.get("sender", "?"))},
                 "document_id": str(o.get("source_id", "?"))}
                for o in groups[group_at]
            ]
            per_item = self._loop.run(
                self._engine.retain_batch_async(
                    self._bank, contents, request_context=self._ctx,
                    operation_id=f"{self._bank}:{group_at}"))
            self._retain_calls += 1
            if isinstance(per_item, tuple):
                per_item = per_item[0]
            if per_item and isinstance(per_item[0], str):
                per_item = [[ids] for ids in per_item]
            for ids in per_item or []:
                if ids:
                    count += 1
                else:
                    empty_ids += 1
        self._retained += count
        self._redirected_turns += redirected
        latency = round(time.monotonic() - started, 2)
        self._latency_s += latency
        return Usage(
            model_calls=self._retain_calls, latency_s=latency,
            notes=(f"retained {count}/{len(evidence)} "
                   f"({empty_ids} empty-id returns, recorded not retried); "
                   f"user_turns_redirected {redirected}",))

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
            "server_entry": "python3 -m hindsight_api.mcp_local "
                            "(adapter.start_server shares the run's PG; "
                            "bank-pinned /mcp/<bank>/)",
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

    def start_server(self, port: int, timeout_s: float = 60.0) -> str:
        """Launch hindsight-local-mcp against this adapter's PG instance."""
        if self._engine is None or self._pg is None:
            raise RuntimeError("adapter not reset: call reset() first")
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
            [sys.executable, "-m", "hindsight_api.mcp_local"],
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
        notes = (f"retained {self._retained}; "
                 f"user_turns_redirected {self._redirected_turns}; "
                 f"retain calls {self._retain_calls} "
                 f"(tokens unknown, never zero)")
        return Usage(
            model_calls=self._retain_calls,
            input_tokens=None,
            output_tokens=None,
            latency_s=round(self._latency_s, 2),
            cost_usd=None,
            notes=(notes,))

    def env(self) -> dict:
        return {"HINDSIGHT_HOME": str(self.home),
                "HINDSIGHT_BANK": self._bank or "",
                "HINDSIGHT_API_LLM_MODEL": self.llm_model,
                "HINDSIGHT_API_LLM_BASE_URL": META_GATEWAY_URL}
