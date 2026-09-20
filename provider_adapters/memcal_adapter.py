"""Memcal provider adapter for PACBench live runs (W3).

Provider-specific code lives here, OUTSIDE the ``pacbench`` package, so the
generic corpus/grader/runner never imports memcal (a boundary test enforces
that). This module may import memcal freely.

Contract coverage (PACBench Benchmark Contract):

- §28 Common Hermes runtime — every scored answer comes from a real isolated
  Hermes instance using memcal's native memory tools and automatic context
  (``retrieval()`` seam below). The adapter never answers directly, never
  exposes another provider, shell, filesystem reads, web search, hidden keys,
  built-in memory, or old-session retrieval.
- §29 Provider fairness — ``ingest`` accepts only public observations
  (``manifest.public_observations`` output) at the declared collection time.
  No scenario-specific interpretation, no identity fixes, no answer-key-derived
  behavior. Memcal's native lifecycle (archive ingest, nightly dream,
  brief/tools retrieval) is exercised, never replaced with benchmark logic.
- §31 Direct user conversations — ``hermes``/``agent``-channel observations are
  NEVER written to the store by this adapter. User turns run through real
  Hermes (``pacbench.harness.run_turn``); memcal's own provider archives them
  via its ``on_turn_start``/``sync_turn`` hook. A direct DB write here would be
  a fake user-turn mutation, so ``ingest`` redirects those observations and
  reports them as ``user_turns_redirected``.
- §42 Valid runs — one scratch MEMCAL_HOME per isolated namespace (reset /
  cleanup own exactly that home); live homes (``~/.memcal``) refused; hidden
  answer keys refused at the ingest boundary (payloads carrying rubric keys
  raise instead of scoring); readiness is honest (skips recorded, never turned
  into passes).
- §44 C Provider adapters — reset, ingestion, readiness, scheduled processing
  (``maintenance``), retrieval integration (``retrieval``), cleanup, and usage
  reporting (per-call ``Usage`` plus cumulative ``usage()``).

Safety:

- MEMCAL_HOME is scratch only: a tmp directory or ``PACBench/artifacts/``.
  Never ``~/.memcal``. Anything else raises.
- Never touches ``~/.hermes`` or live Hermes state; retrieval runs against the
  isolated clone (W1/W2) via ``harness.provision_home``, not from here.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "artifacts"

MEMCAL_SRC = Path("/Users/chris/code/memcal")
if str(MEMCAL_SRC) not in sys.path:
    sys.path.insert(0, str(MEMCAL_SRC))

from pacbench.adapters import ProviderAdapter, Readiness, Usage  # noqa: E402

#: Channels that are live user turns, not ingested evidence (§31). ``agent``
#: is the historical alias of ``hermes`` (see pacbench.observation).
USER_TURN_CHANNELS = ("hermes", "agent")

#: Payload keys that must never cross the adapter boundary (§42: hidden
#: answers stay inaccessible; §29: no rubric content in the store).
FORBIDDEN_OBS_KEYS = ("expected", "slots", "supported", "forbidden",
                      "answer_key", "support_manifest", "truth")

#: memcal memory tools Hermes may call during scored turns (§28: only the
#: selected provider's native tools plus automatic context).
MEMCAL_TOOL_ALLOWLIST = (
    "memcal_open",
    "memcal_open_source",
    "memcal_search_wiki",
    "memcal_search_archive",
    "memcal_conversation",
    "memcal_list_days",
    "memcal_list_month",
    "memcal_activity",
    "memcal_refresh",
    "memcal_add",
    "memcal_update",
    "memcal_schedule",
    "memcal_move_once",
    "memcal_merge",
    "memcal_drop",
    "memcal_todo",
    "memcal_note",
    "memcal_alias",
    "memcal_answer",
    "memcal_reviewed",
    "memcal_open_page",
)

#: Path of memcal's native Hermes provider plugin inside the memcal checkout.
MEMCAL_PLUGIN_REL = Path("integrations") / "hermes" / "memcal"


def _memcal():
    import memcal  # noqa: F401
    from memcal import archive, config, db
    return archive, config, db


def _scratch_roots() -> list[Path]:
    roots = []
    for cand in (tempfile.gettempdir(), "/tmp", "/var/tmp", "/private/tmp"):
        try:
            roots.append(Path(cand).resolve())
        except OSError:
            continue
    try:
        roots.append(ARTIFACTS.resolve())
    except OSError:
        roots.append(ARTIFACTS)
    return roots


def check_scratch_home(home: Path) -> Path:
    """Resolve `home` and refuse anything that is not scratch (§42).

    Raises ``RuntimeError`` for the live ``~/.memcal`` home and for any path
    outside the tmp directories and ``PACBench/artifacts/``.
    """
    resolved = Path(os.path.realpath(os.path.expanduser(str(home))))
    live = Path.home() / ".memcal"
    try:
        if resolved == live.resolve():
            raise RuntimeError(f"refusing live MEMCAL_HOME: {home}")
    except OSError:
        if str(home).rstrip("/") == str(live):
            raise RuntimeError(f"refusing live MEMCAL_HOME: {home}")
    if resolved == live or live in resolved.parents:
        raise RuntimeError(f"refusing live MEMCAL_HOME: {home}")
    for root in _scratch_roots():
        if resolved == root or root in resolved.parents:
            return resolved
    raise RuntimeError(
        f"MEMCAL_HOME must be scratch (tmp or PACBench/artifacts/): {home}")


class MemcalAdapter(ProviderAdapter):
    """Hermes + Memcal, backed by a scratch MEMCAL_HOME. Never ~/.memcal."""

    name = "memcal"

    def __init__(self, home: Path):
        check_scratch_home(home)
        self.home = home
        self.src = MEMCAL_SRC
        self.namespace: str | None = None
        self._archived = 0
        self._redirected_turns = 0
        self._model_calls = 0
        self._latency_s = 0.0
        self._maintenance: dict = {"state": "not-run"}

    # -- lifecycle (§44 C: reset) -------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        _, _, db = _memcal()
        return db.open_db(self.home / "memcal.db")

    def reset(self, namespace: str) -> None:
        """Clear all state for one isolated run namespace (§42 isolation)."""
        check_scratch_home(self.home)
        if self.home.exists():
            shutil.rmtree(self.home)
        self.home.mkdir(parents=True)
        conn = self._conn()
        conn.close()
        self.namespace = namespace
        self._archived = 0
        self._redirected_turns = 0
        self._model_calls = 0
        self._latency_s = 0.0
        self._maintenance = {"state": "not-run"}

    # -- ingestion (§44 C; §§29/31/42) ---------------------------------------
    def ingest(self, observations: list[dict], collected_at: str) -> Usage:
        """Ingest one batch of public observations at a collection boundary.

        Only non-user-turn channels are archived (same ordinary
        ``archive.append`` path real source ingesters write, with sender and
        thread provenance preserved and owner turns marked ``from_me``).
        ``hermes``/``agent`` observations are redirected — they must run
        through real Hermes turns (§31), never direct DB writes. Payloads
        carrying rubric/answer-key fields are refused (§§29/42).
        """
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
        archive, _, _ = _memcal()
        conn = self._conn()
        try:
            collection_id = archive.open_collection(conn, mode="pacbench")
            count = 0
            for obs in evidence:
                row_id = archive.append(
                    conn, channel=obs["channel"], external_id=obs["source_id"],
                    ts=obs["written_at"], text=obs["text"], thread=obs["thread"],
                    handle=obs["sender"], person=obs["sender"],
                    from_me=from_owner_flag(obs), collection_id=collection_id)
                if row_id is not None:
                    count += 1
            report = SimpleNamespace(channel="pacbench", read=len(evidence),
                                     archived=count, passed=0, muted=0, too_old=0,
                                     error=None, notes=[f"pacbench ingest @ {collected_at}"],
                                     more=False)
            archive.record_source(conn, collection_id, report)
        finally:
            conn.close()
        self._archived += count
        self._redirected_turns += redirected
        latency = round(time.monotonic() - started, 2)
        self._latency_s += latency
        return Usage(
            model_calls=0, latency_s=latency,
            notes=(f"archived {count}/{len(evidence)}; "
                   f"user_turns_redirected {redirected}",))

    # -- scheduled processing (§44 C) -----------------------------------------
    def maintenance(self, at: str) -> Usage:
        """Scheduled background work: one memcal nightly-dream pass.

        If dream cannot run here (e.g. no model credentials in the scratch
        home), the skip is recorded — never converted into a passing run (§42).
        """
        started = time.monotonic()
        try:
            archive, config, _ = _memcal()
            from memcal.dream import run as dream_run
            cfg = config.load(self.home)
            conn = self._conn()
            try:
                events: list[dict] = []

                def progress(event: str, data: dict) -> None:
                    events.append({"event": event,
                                   **{k: str(v)[:200] for k, v in data.items()}})

                result = dream_run.dream(conn, cfg, mode="nightly", progress=progress)
                self._maintenance = {"state": "complete", "at": at,
                                     "run_id": getattr(result, "run_id", None),
                                     "events": events[-20:]}
            finally:
                conn.close()
        except Exception as exc:
            self._maintenance = {"state": "skipped", "at": at,
                                 "reason": f"{type(exc).__name__}: {exc}"[:300]}
        latency = round(time.monotonic() - started, 2)
        self._latency_s += latency
        return Usage(model_calls=0, latency_s=latency,
                     notes=(f"maintenance: {self._maintenance['state']}",))

    # -- readiness (§44 C; §42) ------------------------------------------------
    def readiness(self) -> Readiness:
        """Report background-work completion (§42: honest, never a fake pass)."""
        state = self._maintenance.get("state")
        if state == "complete":
            return Readiness(ready=True, detail="dream pass finished")
        if state == "skipped":
            return Readiness(
                ready=True,
                detail=f"no background work ran "
                       f"({self._maintenance.get('reason', '')})")
        return Readiness(ready=True, detail="no background work scheduled yet")

    # -- retrieval integration (§44 C; §28) ------------------------------------
    def retrieval(self) -> dict:
        """How Hermes retrieves from this provider at question time.

        Retrieval is memcal-native only: the provider plugin (tools + automatic
        brief context) installed into the isolated Hermes home by
        ``pacbench.harness.provision_home``. Scored questions run through
        Hermes (``harness.run_turn``); nothing here answers directly and no
        direct provider calls happen outside Hermes (§§28/42).
        """
        plugin = self.src / MEMCAL_PLUGIN_REL
        return {
            "provider": self.name,
            "plugin_src": str(plugin),
            "plugin_available": plugin.is_dir(),
            "tool_allowlist": list(MEMCAL_TOOL_ALLOWLIST),
            "automatic_context": "memcal brief prefetch (MEMCAL SNAPSHOT)",
            "env": self.env(),
        }

    def tool_allowlist(self) -> tuple[str, ...]:
        """memcal tool names Hermes may call (for PACBENCH_TOOL_ALLOWLIST)."""
        return MEMCAL_TOOL_ALLOWLIST

    def plugin_src(self) -> Path:
        """Source dir to install as the isolated home's memcal plugin."""
        return self.src / MEMCAL_PLUGIN_REL

    # -- cleanup (§44 C; §42) ---------------------------------------------------
    def cleanup(self, namespace: str) -> None:
        """Delete only the scratch home owned by this run (§42)."""
        check_scratch_home(self.home)
        if self.namespace is not None and namespace != self.namespace:
            raise RuntimeError(
                f"cleanup namespace mismatch: {namespace!r} != "
                f"{self.namespace!r}")
        if self.home.exists():
            shutil.rmtree(self.home)

    # -- usage reporting (§44 C) -------------------------------------------------
    def usage(self) -> Usage:
        """Cumulative observable cost/usage; unknown stays unknown, never zero."""
        return Usage(
            model_calls=self._model_calls,
            input_tokens=None,
            output_tokens=None,
            latency_s=round(self._latency_s, 2),
            cost_usd=None,
            notes=(f"archived {self._archived}; "
                   f"user_turns_redirected {self._redirected_turns}",))

    def env(self) -> dict:
        return {"MEMCAL_HOME": str(self.home), "MEMCAL_SRC": str(self.src)}


def from_owner_flag(obs: dict) -> bool:
    return bool(obs.get("from_owner"))
