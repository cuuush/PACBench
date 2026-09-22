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

def _resolve_memcal_src() -> Path:
    """Memcal checkout location without hardcoded user paths (§42 isolation).

    Precedence: MEMCAL_SRC env var, else sibling-checkout convention
    (<PACBench-parent>/memcal, e.g. ~/code/memcal next to ~/code/PACBench).
    Never a hardcoded /Users/... path so Linux/CI/other checkouts work.
    """
    env = os.environ.get("MEMCAL_SRC", "").strip()
    if env:
        return Path(env).expanduser()
    return REPO.parent / "memcal"


MEMCAL_SRC = _resolve_memcal_src()
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
    from memcal import archive, config, db, gate
    return archive, config, db, gate


def _scratch_roots() -> list[Path]:
    roots = []
    for cand in (tempfile.gettempdir(), "/tmp", "/var/tmp", "/private/tmp"):
        try:
            roots.append(Path(cand).resolve())
        except OSError:
            continue
    # PACBench-owned scratch dirs (both gitignored): run artifacts and homes.
    for owned in (ARTIFACTS, REPO / "runs"):
        try:
            roots.append(owned.resolve())
        except OSError:
            roots.append(owned)
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


def read_run_usage(conn: sqlite3.Connection, run_id: int | None) -> dict | None:
    """Structured provider spend for one dream pass, or None when unreadable.

    Reads the pass's own `runs` row (written by memcal's ``_finish``): prompt
    / completion tokens are always recorded (0 is an observed zero, e.g. a
    nothing-new pass); ``requests`` is None when memcal did not record it.
    Callers keep tokens None (unknown, never zero) when this returns None.
    """
    if run_id is None:
        return None
    try:
        row = conn.execute(
            "SELECT prompt_tokens, completion_tokens, cached_tokens, cost_usd,"
            " requests, failed_calls FROM runs WHERE id = ?", (run_id,)).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    prompt, comp, cached, cost, requests, failed = row
    return {"prompt_tokens": int(prompt or 0),
            "completion_tokens": int(comp or 0),
            "cached_tokens": int(cached or 0),
            "cost_usd": cost,
            "requests": None if requests is None else int(requests),
            "failed_calls": None if failed is None else int(failed)}


class MemcalAdapter(ProviderAdapter):
    """Hermes + Memcal, backed by a scratch MEMCAL_HOME. Never ~/.memcal."""

    name = "memcal"

    #: Memcal HAS native scheduled work (nightly dream), so maintenance
    #: applies: a skipped pass means the provider was not fully exercised
    #: (clarification 2026-09-21 changes nothing here — it protects
    #: providers with no such lifecycle, e.g. lexical).

    def __init__(self, home: Path, *, dream_model: str | None = None):
        check_scratch_home(home)
        self.home = home
        self.src = MEMCAL_SRC
        self.dream_model = dream_model
        self.namespace: str | None = None
        self._archived = 0
        self._redirected_turns = 0
        self._model_calls = 0
        # §35 provider-side spend, accumulated from each dream pass's own
        # `runs` row (see maintenance). None until a pass has been observed —
        # unknown stays unknown, never zero.
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._latency_s = 0.0
        self._maintenance: dict = {"state": "not-run"}

    # -- lifecycle (§44 C: reset) -------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        _, _, db, _ = _memcal()
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
        self._input_tokens = None
        self._output_tokens = None
        self._latency_s = 0.0
        self._maintenance = {"state": "not-run"}

    # -- ingestion (§44 C; §§29/31/42) ---------------------------------------
    def ingest(self, observations: list[dict], collected_at: str,
               group_flags: dict[tuple[str, str], bool] | None = None) -> Usage:
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
        archive, _, _, gate = _memcal()
        from memcal import textclean
        conn = self._conn()
        # Pin memcal's native clock to the fictional collection time for the
        # duration of ingest (same MEMCAL_TODAY seam the turn env uses):
        # horizon checks, write precedence, and created_at must see the
        # benchmark clock, never the host clock. Restored afterwards so no
        # test pollution leaks out (regression-tested).
        prev_pin = os.environ.get("MEMCAL_TODAY")
        os.environ["MEMCAL_TODAY"] = collected_at
        try:
            collection_id = archive.open_collection(conn, mode="pacbench")
            count = 0
            spooled = 0
            gated_out = 0
            for obs in evidence:
                # Live-source fidelity (sources/base.py deliver): strip
                # non-spoken placeholders the way the real ingesters do,
                # then run the product's own gate before archiving — a
                # rejected line is archived ungated (search/indexing still
                # sees it, like deliver()) but never spooled. The gated
                # bit is what brief/detail readers filter on; dropping
                # rejected rows from archive would manufacture a store
                # the product never builds. "text"/"email" are NOT
                # PASS_ALL streams, so content gating genuinely applies
                # to them (unlike imessage/whatsapp/groupme, full pass).
                clean = textclean.spoken_text(obs["text"])
                if not clean:
                    gated_out += 1
                    continue
                # Group detection is corpus-authored provenance (§8), shared
                # with the Hindsight adapter via the runner (fair, §29):
                # 3+ distinct non-owner senders on the thread = group chat
                # (memcal's own imessage/whatsapp rule, members > 2).
                _gf = group_flags or {}
                _is_group = bool(_gf.get(
                    (str(obs["channel"]), str(obs["thread"])), False))
                verdict = gate.gate_message(
                    clean, person=obs["sender"],
                    from_me=from_owner_flag(obs),
                    channel=obs["channel"], is_group=_is_group)
                if not verdict:
                    gated_out += 1
                # entity_for's `person` is the exchange counterpart: for
                # owner turns the recipient (the thread partner), otherwise
                # the author — so a conversation keys as ONE bundle (§8).
                # The thread id is lowercased first: the corpus uses
                # 'devon' vs 'Devon' for the same conversation across
                # turns, and without normalization the bundle splits in
                # two (observed 2026-09-22: person:Devon +
                # thread:text:Devon instead of one entity). Case is not
                # thread identity — the product lowercases handles for
                # the same reason (identity.normalize).
                sender = str(obs["sender"])
                owner = from_owner_flag(obs) or sender.lower() == "me"
                other = None if owner else sender
                thread_key = str(obs["thread"] or "").lower()
                row_id = archive.append(
                    conn, channel=obs["channel"], external_id=obs["source_id"],
                    ts=obs["written_at"], text=clean, thread=thread_key,
                    handle=None if owner else sender, person=sender,
                    from_me=owner, collection_id=collection_id,
                    gated=bool(verdict), gate_reason=verdict.reason)
                conn.commit()
                if row_id is not None:
                    count += 1
                    # Dream's work queue: live source ingesters spool every
                    # gated line via gate.entity_for (sources/base.py); the
                    # adapter previously archived without spooling, so
                    # benchmark texts never reached distillation — the brief
                    # stayed structurally empty (observed 2026-09-21).
                    # entity_for ALSO needs the normalized thread: it keys
                    # thread:{channel}:{thread} verbatim, so the raw-case
                    # thread would re-split the just-unified bundle.
                    if verdict and archive.within_horizon(obs["written_at"]):
                        archive.spool_add(
                            conn, row_id, gate.entity_for(
                                person=other, thread=thread_key,
                                channel=obs["channel"], is_group=_is_group))
                        conn.commit()
                        spooled += 1
            report = SimpleNamespace(channel="pacbench", read=len(evidence),
                                     archived=count, passed=spooled, muted=0,
                                     too_old=gated_out,
                                     error=None, notes=[f"pacbench ingest @ {collected_at}"],
                                     more=False)
            archive.record_source(conn, collection_id, report)
        finally:
            if prev_pin is None:
                os.environ.pop("MEMCAL_TODAY", None)
            else:
                os.environ["MEMCAL_TODAY"] = prev_pin
            conn.close()
        self._archived += count
        self._redirected_turns += redirected
        latency = round(time.monotonic() - started, 2)
        self._latency_s += latency
        return Usage(
            model_calls=0, latency_s=latency,
            notes=(f"archived {count}/{len(evidence)}; "
                   f"spooled {spooled}/{count} for dream; "
                   f"gated_out {gated_out}; "
                   f"user_turns_redirected {redirected}",))

    # -- scheduled processing (§44 C) -----------------------------------------
    def maintenance(self, at: str) -> Usage:
        """Scheduled background work: one memcal nightly-dream pass.

        Provider-side spend comes from the pass's own `runs` row (prompt /
        completion tokens, provider request count) and is accumulated into
        usage(); a pass that ran with nothing new reports observed zeros. If
        dream cannot run here (e.g. no model credentials in the scratch
        home), the skip is recorded — never converted into a passing run (§42)
        — and tokens stay None (unknown, never zero).
        """
        started = time.monotonic()
        try:
            archive, config, _, _ = _memcal()
            from memcal.dream import run as dream_run
            cfg = config.load(self.home)
            conn = self._conn()
            try:
                events: list[dict] = []

                def progress(event: str, data: dict) -> None:
                    events.append({"event": event,
                                   **{k: str(v)[:200] for k, v in data.items()}})

                result = dream_run.dream(conn, cfg, mode="nightly",
                                           model=self.dream_model,
                                           progress=progress)
                run_id = getattr(result, "run_id", None)
                spent = read_run_usage(conn, run_id)
                if spent is None:
                    self._maintenance = {
                        "state": "complete", "at": at,
                        "model": self.dream_model, "run_id": run_id,
                        "usage": "unreadable",
                        "events": events[-20:]}
                    latency = round(time.monotonic() - started, 2)
                    self._latency_s += latency
                    return Usage(
                        model_calls=0, latency_s=latency,
                        notes=("maintenance: complete but run-usage row "
                               "unreadable (tokens unknown)",))
                calls = spent["requests"] if spent["requests"] is not None else 0
                prompt, comp = spent["prompt_tokens"], spent["completion_tokens"]
                self._model_calls += calls
                self._input_tokens = (self._input_tokens or 0) + prompt
                self._output_tokens = (self._output_tokens or 0) + comp
                self._maintenance = {
                    "state": "complete", "at": at,
                    "model": self.dream_model, "run_id": run_id,
                    "usage": spent, "events": events[-20:]}
                latency = round(time.monotonic() - started, 2)
                self._latency_s += latency
                unrecorded = (" (request count unrecorded)"
                              if spent["requests"] is None else "")
                return Usage(
                    model_calls=calls, input_tokens=prompt,
                    output_tokens=comp, latency_s=latency,
                    notes=(f"maintenance: complete (run #{run_id}: {calls} "
                           f"provider requests{unrecorded}, {prompt} in / "
                           f"{comp} out)",))
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
        """Report background-work completion (§42: honest, never a fake pass).

        Only a finished dream pass reads ready. A skip means the provider was
        NOT fully exercised (no consolidation ran); ``not-run`` means no pass
        has happened yet. Both read not-ready so a reviewer can never mistake
        either for a healthy background pipeline — ``live.maintain`` already
        treats both as incomplete for the same reason.
        """
        state = self._maintenance.get("state")
        if state == "complete":
            return Readiness(ready=True, detail="dream pass finished")
        if state == "skipped":
            return Readiness(
                ready=False,
                detail=f"dream pass skipped: "
                       f"{self._maintenance.get('reason', '')}")
        return Readiness(ready=False, detail="dream pass has not run yet")

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

    def server_entry(self) -> str:
        """How this adapter's transport is launched (audit surface).

        Memcal needs no server (plugin-only seam) — returns "" so the
        interpreter-pinning audit (hindsight_adapter.server_entry) has a
        uniform surface across adapters.
        """
        return ""

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
        """Cumulative observable cost/usage; unknown stays unknown, never zero.

        Dream tokens stay None until a dream pass has been observed; provider
        request counts accumulate per completed pass (0 with no pass yet).
        """
        notes = (f"archived {self._archived}; "
                 f"user_turns_redirected {self._redirected_turns}")
        if self._input_tokens is not None:
            notes += (f"; dream {self._model_calls} requests, "
                      f"{self._input_tokens} in / {self._output_tokens} out")
        return Usage(
            model_calls=self._model_calls,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            latency_s=round(self._latency_s, 2),
            cost_usd=None,
            notes=(notes,))

    def env(self) -> dict:
        return {"MEMCAL_HOME": str(self.home), "MEMCAL_SRC": str(self.src)}


def from_owner_flag(obs: dict) -> bool:
    return bool(obs.get("from_owner"))
