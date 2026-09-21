"""Lexical archive-search baseline for PACBench live runs (§45.5).

The honest first "external provider": a deliberately dumb keyword store behind
the same :class:`pacbench.adapters.ProviderAdapter` contract as memcal. No
model calls anywhere on the provider side (ingest is sqlite writes, retrieval
is term-overlap ranking), no semantic rewriting, no answer-key-derived
behavior, no scenario-specific tuning — the dumbness is what makes it a
baseline. A Memcal win against it on small high-overlap lives is a sanity
check, not a victory (see the pilot REPORT.md caveat).

Provider-specific code lives here, OUTSIDE the ``pacbench`` package, so the
generic corpus/grader/runner never imports a provider (a boundary test
enforces that). This module is stdlib-only.

Contract coverage mirrors the memcal adapter:

- §29 Provider fairness — ``ingest`` accepts only public observations at the
  declared collection time. No interpretation, no identity fixes, no
  answer-key-derived behavior. Retrieval is storage + lexical match only.
- §31 Direct user conversations — ``hermes``/``agent``-channel observations
  are NEVER written to the store by this adapter. They run through real
  Hermes turns; the lexical plugin persists them via its ``sync_turn`` hook.
- §42 Valid runs — one scratch home per isolated namespace (reset / cleanup
  own exactly that home); live homes refused; hidden answer keys refused at
  the ingest boundary; readiness is honest.
- §44 C Provider adapters — reset, ingestion, readiness, scheduled
  processing (``maintenance``, an honest no-op), retrieval integration
  (``retrieval``), cleanup, and usage reporting.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from provider_adapters.memcal_adapter import (
    FORBIDDEN_OBS_KEYS,
    USER_TURN_CHANNELS,
    check_scratch_home,
)

from pacbench.adapters import ProviderAdapter, Readiness, Usage

#: Lexical memory tools Hermes may call during scored turns (§28: only the
#: selected provider's native tools plus automatic context).
LEXICAL_TOOL_ALLOWLIST = (
    "lexical_search",
    "lexical_open",
)

#: The Hermes provider plugin installed into the isolated home by
#: ``pacbench.harness.provision_home`` (sibling directory, copied verbatim).
LEXICAL_PLUGIN_DIR = Path(__file__).resolve().parent / "lexical_plugin"

DB_NAME = "lexical.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    source_id    TEXT PRIMARY KEY,
    channel      TEXT NOT NULL,
    sender       TEXT NOT NULL,
    thread       TEXT NOT NULL,
    written_at   TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    text         TEXT NOT NULL
)
"""


class LexicalAdapter(ProviderAdapter):
    """Keyword-search baseline backed by a scratch sqlite home. No model calls."""

    name = "lexical"
    maintenance_applicable = False

    #: A keyword store has no native scheduled background work
    #: (clarification 2026-09-21): maintenance is not applicable, the
    #: runner records that, and the run stays valid. Readiness below
    #: agrees — ready per the native lifecycle (ingest only).

    def __init__(self, home: Path):
        check_scratch_home(home)
        self.home = home
        self.namespace: str | None = None
        self._archived = 0
        self._redirected_turns = 0
        self._latency_s = 0.0
        self._maintenance: dict = {"state": "not-run"}

    # -- lifecycle (§44 C: reset) -------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.home / DB_NAME)
        conn.execute(SCHEMA)
        return conn

    def reset(self, namespace: str) -> None:
        """Clear all state for one isolated run namespace (§42 isolation)."""
        check_scratch_home(self.home)
        import shutil

        if self.home.exists():
            shutil.rmtree(self.home)
        self.home.mkdir(parents=True)
        conn = self._conn()
        conn.commit()
        conn.close()
        self.namespace = namespace
        self._archived = 0
        self._redirected_turns = 0
        self._latency_s = 0.0
        self._maintenance = {"state": "not-run"}

    # -- ingestion (§44 C; §§29/31/42) ---------------------------------------
    def ingest(self, observations: list[dict], collected_at: str) -> Usage:
        """Ingest one batch of public observations at a collection boundary.

        Non-user-turn channels are stored verbatim with sender/thread/channel
        provenance. ``hermes``/``agent`` observations are redirected — they
        must run through real Hermes turns (§31) and reach the store only via
        the plugin's ``sync_turn`` hook, never direct writes. Payloads
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
        conn = self._conn()
        try:
            count = 0
            for obs in evidence:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO docs(source_id, channel, sender,"
                    " thread, written_at, collected_at, text)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (obs["source_id"], obs["channel"], obs["sender"],
                     obs["thread"], obs["written_at"], obs["collected_at"],
                     obs["text"]))
                count += cur.rowcount
            conn.commit()
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
        """Not applicable: a keyword store has no background work to schedule.

        Records ``not-applicable`` — never ``complete`` (nothing completed)
        and never ``skipped`` (nothing to skip). The runner records the N/A
        and the run stays valid (clarification 2026-09-21).
        """
        started = time.monotonic()
        self._maintenance = {"state": "not-applicable", "at": at,
                             "detail": "lexical store: no scheduled work"}
        latency = round(time.monotonic() - started, 2)
        self._latency_s += latency
        return Usage(model_calls=0, latency_s=latency,
                     notes=("maintenance: not applicable (nothing scheduled)",))

    # -- readiness (§44 C; §42) ------------------------------------------------
    def readiness(self) -> Readiness:
        """Ready per the native lifecycle (clarification 2026-09-21).

        The native lifecycle is ingest-only: there is never background
        work pending, so readiness is always ready once the adapter
        exists. This is not a fake pass — it is what "ready according to
        the supported native lifecycle" means for a hook-less provider.
        """
        return Readiness(
            ready=True,
            detail="scheduled maintenance not applicable; store current as ingested")

    # -- retrieval integration (§44 C; §28) ------------------------------------
    def retrieval(self) -> dict:
        """How Hermes retrieves from this provider at question time.

        Retrieval is keyword search only: the provider plugin (two tools plus
        a stored-message count line) installed into the isolated Hermes home
        by ``pacbench.harness.provision_home``. Scored questions run through
        Hermes (``harness.run_turn``); nothing here answers directly.
        """
        return {
            "provider": self.name,
            "plugin_src": str(LEXICAL_PLUGIN_DIR),
            "plugin_available": LEXICAL_PLUGIN_DIR.is_dir(),
            "tool_allowlist": list(LEXICAL_TOOL_ALLOWLIST),
            "automatic_context": "lexical archive count line (LEXICAL ARCHIVE)",
            "env": self.env(),
        }

    def tool_allowlist(self) -> tuple[str, ...]:
        """Lexical tool names Hermes may call (for PACBENCH_TOOL_ALLOWLIST)."""
        return LEXICAL_TOOL_ALLOWLIST

    def plugin_src(self) -> Path:
        """Source dir to install as the isolated home's lexical plugin."""
        return LEXICAL_PLUGIN_DIR

    # -- cleanup (§44 C; §42) ---------------------------------------------------
    def cleanup(self, namespace: str) -> None:
        """Delete only the scratch home owned by this run (§42)."""
        check_scratch_home(self.home)
        import shutil

        if self.namespace is not None and namespace != self.namespace:
            raise RuntimeError(
                f"cleanup namespace mismatch: {namespace!r} != "
                f"{self.namespace!r}")
        if self.home.exists():
            shutil.rmtree(self.home)

    # -- usage reporting (§44 C) -------------------------------------------------
    def usage(self) -> Usage:
        """Cumulative observable cost/usage.

        Tokens stay None: a lexical store makes no model calls — that None is
        observed truth (every retrieval path is sqlite + term overlap), not a
        gap. Unknown stays unknown, never zero.
        """
        return Usage(
            model_calls=0,
            input_tokens=None,
            output_tokens=None,
            latency_s=round(self._latency_s, 2),
            cost_usd=None,
            notes=(f"archived {self._archived}; "
                   f"user_turns_redirected {self._redirected_turns}; "
                   f"no model calls (lexical retrieval only)",))

    def env(self) -> dict:
        return {"LEXICAL_HOME": str(self.home)}
