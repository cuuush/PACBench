"""P0-10 live probe: adversarial Hindsight temporal/recency test (SPEND-GATED).

Runs ONLY with PACBENCH_LIVE_PROBE_P10=1 after explicit spend approval.
Without it, prints the execution plan and exits 2 (zero spend, zero side effects).

Bounds (hard):
  - fixed fixture: 2 lake-cabin facts ~6.5 months apart in ONE batch
    (near-identical real ingest times), imported from tests.test_temporal_probe;
  - exactly 1 retain_batch_async + 4 native recalls (with/without
    question_date control x synthesized/raw-evidence) = 5 provider ops;
  - scratch embedded-PG home (tempdir) + bank pacbench-probe-p10-*; adapter
    refuses live homes; script asserts the probe bank prefix;
  - never touches ~/.hindsight, never touches a normal provider namespace;
  - overall deadline 900 s (guarded before the recall block, mirroring P0-9);
    raw trace written to runs/probe-p10-<ts>/trace.json (runs/ is gitignored);
  - recalls use max_tokens=512; raw paths leave prefer_observations at its
    default; the adapter is NEVER modified (architectural freeze) — the
    submission log replicates its grouping rule read-only via
    pacbench.source_documents.

Design: recall WITH question_date (supported control) is the graded assertion;
recall WITHOUT it is recorded as characterization data (does wall-clock timing
change the outcome when the control is absent?). Each checkpoint performs TWO
recall flavors:
  (a) recall_synthesized: default synthesized path;
  (b) recall_raw: raw-evidence path with include_source_facts=True.
Both payloads are recorded separately; document/observation identifiers in the
raw payload are extracted as same-doc association evidence. Ranking is graded
primarily on RAW evidence order (results-list order); synthesized text order
is recorded secondary.

Submission log: the single batch's submitted document_ids recomputed
(read-only) via source_documents.logical_document_key plus the per-packet
update_mode, with event_dates and texts — proving PACBench-side key stability
and that both facts crossed in one delta batch.

Verdict:
  - PASS: with-control RAW evidence ranks October fact above March fact
    (uncontrolled agrees or is uninformative — no wall-clock effect observed).
  - PASS-WITH-DOCUMENTED-WALL-CLOCK-DEPENDENCY: with-control RAW ranking
    correct, but without-control RAW ranking differs (March first) — the
    supported controls fix ranking; the wall-clock dependency is documented.
  - FAIL: with-control RAW ranks March first (wall-clock beat simulated age
    despite supported controls — controls cannot fix ranking), submission
    key anomaly, or op errors.
  - INCONCLUSIVE: ingest retained 0/2 (stochastic empty-id drop), with-control
    RAW evidence lacks either fact (uninformative / provider-behavior
    finding), or ranking indeterminate.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

FLAG = "PACBENCH_LIVE_PROBE_P10"
DEADLINE_S = 900.0
QUERY = "when does the lake cabin rental end?"


def _plan() -> str:
    return (
        "P0-10 live probe plan (not executed — set PACBENCH_LIVE_PROBE_P10=1):\n"
        "  inputs: 2 lake-cabin facts (Mar 10 vs Oct 5 end dates), one batch,\n"
        "    from tests.test_temporal_probe (FACT_OLD/FACT_NEW/COLLECTED_AT)\n"
        "  ops: 1 retain_batch_async + 4 native recalls (with/without\n"
        "    question_date=2026-09-19T00:00:00 x synthesized-default +\n"
        "    raw-evidence include_source_facts=True, max_tokens=512)\n"
        "    = 5 provider ops\n"
        "  submission log (logical_document_key + update_mode + event_dates +\n"
        "    texts) proves one-batch, stable-key ingest\n"
        "  invariant: with supported event_date+question_date controls, RAW\n"
        "    evidence ranking follows simulated age (Oct above Mar);\n"
        "    synthesized text order recorded secondary\n"
        "  PASS / PASS-WITH-DOCUMENTED-WALL-CLOCK-DEPENDENCY / FAIL /\n"
        "    INCONCLUSIVE: see module docstring"
    )


def _chrono_key(obs: dict) -> tuple:
    """Read-only mirror of the adapter's chronological sort key."""
    dt = None
    try:
        dt = datetime.fromisoformat(obs.get("written_at") or "")
    except ValueError:
        dt = None
    if dt is not None and dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return (dt if dt is not None else datetime.min,
            str(obs.get("source_id", "?")))


def _key_of(obs: dict, logical_document_key) -> str:
    """Key input mirror: the adapter keys on channel/thread/source_id only."""
    return logical_document_key({
        "channel": obs.get("channel", "?"),
        "thread": obs.get("thread", "?"),
        "source_id": obs.get("source_id", "?"),
    })


def _packetize(messages: list[dict], logical_document_key,
               is_conversational) -> tuple[list[dict], dict]:
    """Read-only replication of the adapter's grouping rule (never modifies it).

    Returns (ordered submitted items, {doc_id: update_mode}).
    """
    packets: dict[str, list[dict]] = {}
    for m in messages:
        packets.setdefault(_key_of(m, logical_document_key), []).append(m)
    ordered: list[dict] = []
    modes: dict[str, str] = {}
    for doc_id in sorted(packets):
        members = packets[doc_id]
        conversational = is_conversational(str(members[0].get("channel", "?")))
        modes[doc_id] = "append" if conversational else "replace"
        ordered.extend(sorted(members, key=_chrono_key))
    return ordered, modes


def _full_retain(note: str) -> bool:
    """True iff a packet-counting usage note shows every packet retained.

    The adapter reports "retained X/Y" where Y = packets submitted this call
    (one joined item per packet, not one per message). A per-message check
    ("retained 2/2" for a 1-packet batch) misreads "retained 1/1" as failure.
    """
    m = re.search(r"retained (\d+)/(\d+)", note)
    return bool(m) and int(m.group(1)) == int(m.group(2)) and int(m.group(2)) >= 1


def _extract_evidence_ids(dumped: dict) -> dict:
    """Document/observation identifiers present in one raw recall payload."""
    results = dumped.get("results") or []
    source_facts = dumped.get("source_facts") or {}
    results = [r for r in results if isinstance(r, dict)]
    sf_vals = [v for v in source_facts.values() if isinstance(v, dict)]
    return {
        "n_results": len(results),
        "n_source_facts": len(source_facts),
        "result_ids": [r["id"] for r in results if r.get("id")],
        "result_document_ids": [r["document_id"] for r in results
                                if r.get("document_id")],
        "result_fact_types": [r["fact_type"] for r in results
                              if r.get("fact_type")],
        "result_source_fact_ids": [
            fid for r in results for fid in (r.get("source_fact_ids") or [])],
        "metadata_source_ids": [
            r["metadata"]["source_id"] for r in results
            if isinstance(r.get("metadata"), dict)
            and r["metadata"].get("source_id")],
        "metadata_logical_documents": [
            r["metadata"]["logical_document"] for r in results
            if isinstance(r.get("metadata"), dict)
            and r["metadata"].get("logical_document")],
        "result_texts": [(r.get("text") or "")[:1000] for r in results],
        "result_texts_full": [(r.get("text") or "") for r in results],
        "source_fact_keys": list(source_facts.keys()),
        "source_fact_texts": [(v.get("text") or "")[:1000] for v in sf_vals],
        "source_fact_document_ids": [v["document_id"] for v in sf_vals
                                     if v.get("document_id")],
        "source_facts_truncated": dumped.get("source_facts_truncated"),
    }


def _recall(engine, loop, bank, ctx, query, *, question_date=None,
            include_source_facts=False):
    """One recall in the requested flavor; returns (payload, evidence, blob).

    payload is the truncated JSON kept in the trace; evidence holds the
    extracted identifiers plus full ordered result texts (for RAW ranking);
    blob is the full JSON used for presence checks.
    """
    kwargs: dict = {"max_tokens": 512, "request_context": ctx}
    if question_date is not None:
        kwargs["question_date"] = question_date
    if include_source_facts:
        kwargs["include_source_facts"] = True
    # prefer_observations deliberately left at its default.
    res = loop.run(engine.recall_async(bank, query, **kwargs))
    try:
        dumped = res.model_dump()
    except Exception:
        flat = str(res)[:6000]
        return flat, {"parse": "model_dump-unavailable"}, flat
    full_blob = json.dumps(dumped, ensure_ascii=False)
    payload = full_blob[:6000]
    return payload, _extract_evidence_ids(dumped), full_blob


_MONTHS = frozenset(
    "january february march april may june july august september october "
    "november december".split())


def _temporal_covers(fact_text: str, candidate: str) -> bool:
    """Month-and-day anchored coverage for temporal fact texts.

    The two probe facts differ ONLY by month + day (shared carrier phrase
    "the lake cabin rental ends"), so majority-token coverage would cross-
    match. Require the fact's month name AND day number as whole words in
    the candidate (extraction paraphrases keep both, e.g. "ends on
    October 5, 2026").
    """
    low = (candidate or "").lower()
    toks = re.findall(r"[a-z0-9]+", fact_text.lower())
    anchors = [t for t in toks
               if t in _MONTHS or t.isdigit()]
    if not anchors:
        return False
    return all(re.search(rf"\b{re.escape(a)}\b", low) for a in anchors)


def _fact_month(fact_text: str) -> str | None:
    for t in re.findall(r"[a-z]+", fact_text.lower()):
        if t in _MONTHS:
            return t
    return None


def _raw_positions(result_texts_full: list[str], old_t: str, new_t: str):
    """Ordered RAW ranking positions of the two facts (None when absent).

    Coverage-based, NOT verbatim substring: recall returns EXTRACTED FACTS
    ("Lake cabin rental ends on October 5, 2026 | When: ..."), never the raw
    source sentences, so `fact_text in result` can never match.
    """
    def pos(t: str):
        for i, rt in enumerate(result_texts_full):
            if _temporal_covers(t, rt or ""):
                return i
        return None
    return pos(old_t), pos(new_t)


def _order_in_blob(blob: str, old_t: str, new_t: str) -> str:
    """Secondary synthesized-text order: new-first / old-first / indeterminate.

    Month-anchored: the facts differ by month, so order by first occurrence
    of each fact's month token (verbatim full-text matching is impossible on
    extracted-fact prose).
    """
    low = (blob or "").lower()
    mo, mn = _fact_month(old_t), _fact_month(new_t)
    if not mo or not mn:
        return "indeterminate"
    try:
        io, ino = low.index(mo), low.index(mn)
    except ValueError:
        return "indeterminate"
    return "new-first" if ino < io else "old-first"


def main() -> int:
    if os.environ.get(FLAG) != "1":
        print(_plan())
        return 2
    from tests.test_temporal_probe import (COLLECTED_AT, FACT_NEW, FACT_OLD,
                                           QUERY_TIMESTAMP)

    from pacbench.source_documents import (is_conversational,
                                           logical_document_key)

    from provider_adapters.hindsight_adapter import HindsightAdapter

    t0 = time.monotonic()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO / "runs" / f"probe-p10-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {"probe": "p10-temporal", "started_at": ts}
    home = Path(tempfile.mkdtemp(prefix="hs-probe-p10-"))
    adapter = HindsightAdapter(home)
    verdict = "INCONCLUSIVE"
    reason = "not run"
    try:
        ns = f"probe-p10-{ts}"
        adapter.reset(ns)
        assert str(adapter._bank).startswith("pacbench-probe-"), adapter._bank
        trace["bank"] = adapter._bank
        batch = [dict(FACT_OLD), dict(FACT_NEW)]
        usage = adapter.ingest(batch, COLLECTED_AT)
        trace["ingest_usage"] = usage.notes[0]
        # Submission log: PACBench-side replication (read-only) of the batch.
        ordered, modes = _packetize(batch, logical_document_key,
                                    is_conversational)
        submitted_ids = [str(m.get("source_id", "?")) for m in ordered]
        trace["submission_log"] = {
            "collected_at": COLLECTED_AT,
            "submitted_source_ids": submitted_ids,
            "submitted_document_ids": sorted(modes),
            "packet_update_modes": dict(modes),
            "event_dates": [m.get("written_at") for m in ordered],
            "texts": [m.get("text") for m in ordered],
        }
        expected_ids = {str(m.get("source_id", "?")) for m in batch}
        if set(submitted_ids) != expected_ids:
            verdict, reason = (
                "FAIL",
                f"submission != single delta batch: "
                f"{sorted(set(submitted_ids))} vs {sorted(expected_ids)}")
        # Two-fact, two-packet batch (fixtures carry distinct threads since the
        # one-item-per-packet rule): expect exactly the two stable keys.
        expected_keys = {logical_document_key(FACT_OLD),
                         logical_document_key(FACT_NEW)}
        if set(modes) != expected_keys:
            verdict, reason = (
                "FAIL",
                f"submission key anomaly: got {sorted(modes)} "
                f"(expected {sorted(expected_keys)})")
        elif not _full_retain(usage.notes[0]):
            reason = f"incomplete retain: {usage.notes[0]!r}"
        elif time.monotonic() - t0 > DEADLINE_S:
            reason = "overall deadline exceeded before recalls"
        else:
            qd = datetime.fromisoformat(QUERY_TIMESTAMP)
            old_t, new_t = FACT_OLD["text"], FACT_NEW["text"]
            c_synth, _, c_synth_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank, adapter._ctx,
                QUERY, question_date=qd)
            c_raw, c_ids, c_raw_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank, adapter._ctx,
                QUERY, question_date=qd, include_source_facts=True)
            u_synth, _, u_synth_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank, adapter._ctx,
                QUERY)
            u_raw, u_ids, u_raw_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank, adapter._ctx,
                QUERY, include_source_facts=True)
            stable_key = _key_of(FACT_OLD, logical_document_key)
            trace["expected_stable_key"] = stable_key
            c_old, c_new = _raw_positions(
                list(c_ids.get("result_texts_full") or []), old_t, new_t)
            u_old, u_new = _raw_positions(
                list(u_ids.get("result_texts_full") or []), old_t, new_t)
            c_synth_order = _order_in_blob(c_synth_blob, old_t, new_t)
            u_synth_order = _order_in_blob(u_synth_blob, old_t, new_t)
            trace["recalls"] = {
                "with_control": {
                    "question_date": QUERY_TIMESTAMP,
                    "recall_synthesized": c_synth,
                    "recall_raw": c_raw,
                    "raw_evidence_ids": {
                        k: v for k, v in c_ids.items()
                        if k != "result_texts_full"},
                    "raw_ranking": {"old_pos": c_old, "new_pos": c_new},
                    "synthesized_order": c_synth_order,
                },
                "without_control": {
                    "question_date": None,
                    "recall_synthesized": u_synth,
                    "recall_raw": u_raw,
                    "raw_evidence_ids": {
                        k: v for k, v in u_ids.items()
                        if k != "result_texts_full"},
                    "raw_ranking": {"old_pos": u_old, "new_pos": u_new},
                    "synthesized_order": u_synth_order,
                },
            }
            # Compat: synthesized flavors under the old key names.
            trace["recall_with_control"] = c_synth
            trace["recall_without_control"] = u_synth
            # Presence gate over ordered RAW result texts (coverage-based —
            # verbatim matching is impossible on extracted-fact prose).
            if c_old is None and c_new is None:
                reason = ("with-control RAW evidence uninformative "
                          "(neither fact covered in ordered results)")
            elif c_old is None or c_new is None:
                reason = ("with-control RAW ranking indeterminate "
                          "(a fact is present only outside the ordered "
                          "results, e.g. source_facts map) — "
                          "provider-behavior finding")
            elif c_new < c_old:
                extra = ""
                if c_synth_order != "new-first":
                    extra += ("; synthesized text order differs "
                              f"({c_synth_order}) — secondary, noted")
                if (u_old is not None and u_new is not None
                        and not (u_new < u_old)):
                    verdict = "PASS-WITH-DOCUMENTED-WALL-CLOCK-DEPENDENCY"
                    reason = ("with-control RAW ranks October above March; "
                              "without-control RAW ranks March first — "
                              "supported controls fix ranking; wall-clock "
                              "dependency documented" + extra)
                else:
                    verdict = "PASS"
                    reason = ("with-control RAW ranking follows simulated "
                              "age (October above March)")
                    if u_old is None or u_new is None:
                        reason += ("; without-control RAW uninformative "
                                   "(controls add information)")
                    reason += extra
            else:
                verdict = "FAIL"
                reason = ("with-control RAW ranks March above October: "
                          "wall-clock beat supported controls — controls "
                          "cannot fix ranking")
        trace["verdict"] = verdict
        trace["reason"] = reason
        trace["adapter_usage_final"] = adapter.usage().__dict__
    except Exception as exc:
        verdict, reason = "FAIL", f"{type(exc).__name__}: {exc}"[:300]
        trace["verdict"] = verdict
        trace["reason"] = reason
    finally:
        try:
            adapter.close()
        except Exception:
            pass
        trace["elapsed_s"] = round(time.monotonic() - t0, 1)
        (out_dir / "trace.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False) + "\n")
    print(f"verdict={verdict} reason={reason} trace={out_dir / 'trace.json'}")
    return 0 if verdict in ("PASS",
                            "PASS-WITH-DOCUMENTED-WALL-CLOCK-DEPENDENCY") else 1


if __name__ == "__main__":
    raise SystemExit(main())
