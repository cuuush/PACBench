"""P0-9 live probe: multi-day Hindsight evolving-conversation test (SPEND-GATED).

Runs ONLY with PACBENCH_LIVE_PROBE_P09=1 after explicit spend approval.
Without it, prints the execution plan and exits 2 (zero spend, zero side effects).

Bounds (hard):
  - fixed fixture: 6-message Gus/Mia conversation, 3 collection cycles (2+2+2),
    imported from tests.test_conversation_evolution (no drift, no large life);
  - exactly 3 retain_batch_async calls + 6 native recalls (per cycle: 1 default
    synthesized + 1 raw-evidence with include_source_facts=True) = 9 ops;
  - scratch embedded-PG home (tempdir) + bank pacbench-probe-p09-*; the adapter
    itself refuses live homes, and this script asserts the probe bank prefix;
  - never touches ~/.hindsight, never touches a normal provider namespace;
  - overall deadline 1500 s; raw trace (inputs, usage notes, submission log,
    both recall flavors per cycle, verdict) written to
    runs/probe-p09-<ts>/trace.json (runs/ is gitignored);
  - recalls use max_tokens=512; the raw path leaves prefer_observations at its
    default; the adapter is NEVER modified (architectural freeze) — the
    submission log replicates its grouping rule read-only via
    pacbench.source_documents.

Design: each recall checkpoint performs TWO recall flavors:
  (a) recall_synthesized: default synthesized path (current call shape);
  (b) recall_raw: raw-evidence path with include_source_facts=True.
Both payloads are recorded separately in the trace; document/observation
identifiers found in the raw payload (result ids, document_ids, metadata
source_ids / logical_document, source_fact_ids, source_facts keys) are
extracted as same-doc association evidence.

Submission log: per cycle the script recomputes (read-only) the submitted
document_ids via source_documents.logical_document_key plus the per-packet
update_mode (append for conversational, replace for singleton — the same rule
the adapter applies), with event_dates and texts. This proves PACBench-side
key stability (one key, all cycles) and delta-only submission (no earlier
message resent in a later cycle).

Verdict:
  - PASS: one stable key all cycles + delta-only submission log + at every
    cycle both flavors show all-so-far with no future leak.
  - SYNTHESIS-LIMITATION: ingestion PASS (stable key, delta-only, raw evidence
    complete) but the synthesized text omits expected facts — a synthesis
    finding recorded separately, not an ingestion failure.
  - INCONCLUSIVE: any ingest retained 0/2 (known stochastic empty-id drops),
    a recall returns nothing relevant (uninformative), or raw evidence is
    absent despite retain success (provider-behavior finding, reason recorded).
  - FAIL: future-message leak (either flavor), wrong association, submission
    key drift / resend of earlier messages, op errors.
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

FLAG = "PACBENCH_LIVE_PROBE_P09"
DEADLINE_S = 1500.0
QUERY = "what have Gus and Mia said about the car pickup?"


def _plan() -> str:
    return (
        "P0-9 live probe plan (not executed — set PACBENCH_LIVE_PROBE_P09=1):\n"
        "  inputs: 6-message Gus/Mia car-pickup conversation, 3 cycles (2+2+2),\n"
        "    from tests.test_conversation_evolution.CYCLES\n"
        "  ops: 3 retain_batch_async (update_mode=append, stable conversation doc)\n"
        "    + 6 native recall_async (per cycle: 1 synthesized default +\n"
        "    1 raw-evidence include_source_facts=True, max_tokens=512)\n"
        "    = 9 provider ops\n"
        "  per cycle: submission log (logical_document_key + update_mode +\n"
        "    event_dates + texts) proves stable key + delta-only\n"
        "  invariant: stable doc id; deltas associate; no dupes; all recoverable;\n"
        "    chronology intact; no future leak at any cycle\n"
        "  PASS / SYNTHESIS-LIMITATION / FAIL / INCONCLUSIVE: see module docstring"
    )


def _chrono_key(obs: dict) -> tuple:
    """Read-only mirror of the adapter's chronological sort key.

    (written_at parsed to naive UTC, source_id tiebreak.) Only used to
    replicate submission ordering for the log — never sent anywhere.
    """
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
        "source_fact_keys": list(source_facts.keys()),
        "source_fact_texts": [(v.get("text") or "")[:1000] for v in sf_vals],
        "source_fact_document_ids": [v["document_id"] for v in sf_vals
                                     if v.get("document_id")],
        "source_facts_truncated": dumped.get("source_facts_truncated"),
    }


def _covered(text: str, blob: str) -> bool:
    """Message-content coverage for extracted-fact recall payloads.

    Recall returns EXTRACTED FACTS ("Mia plans to pick up the car ..."), never
    verbatim source texts ("day1-b: pickup after 5pm works"), so verbatim
    substring checks misread success as absence (2026-09-22 remediation trace).
    A message counts as present when a majority of its distinctive tokens
    (alphanumeric, len>=3, case/space-insensitive) appear in the blob.
    """
    body = re.sub(r"^day\d+-[a-z]:\s*", "", text)
    toks = [w for w in re.findall(r"[a-z0-9]+", body.casefold()) if len(w) >= 3]
    if not toks:
        return False
    norm = blob.casefold().replace(" ", "")
    hits = sum(1 for w in toks if w in norm)
    return hits * 2 >= len(toks)


def _recall(engine, loop, bank, ctx, query, *, include_source_facts=False):
    """One recall in the requested flavor; returns (payload, evidence, blob).

    payload is the truncated JSON kept in the trace; evidence holds the
    extracted identifiers; blob is the full JSON used for presence checks
    (so trace truncation can never cause a false negative).
    """
    kwargs: dict = {"max_tokens": 512, "request_context": ctx}
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


def main() -> int:
    if os.environ.get(FLAG) != "1":
        print(_plan())
        return 2
    sys.path.insert(0, str(REPO))
    from tests.test_conversation_evolution import CYCLES, CONVERSATION

    from pacbench.source_documents import (is_conversational,
                                           logical_document_key)

    from provider_adapters.hindsight_adapter import HindsightAdapter

    t0 = time.monotonic()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO / "runs" / f"probe-p09-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {"probe": "p09-evolution", "started_at": ts,
                   "cycles": [], "recalls": [], "submission_log": []}
    home = Path(tempfile.mkdtemp(prefix="hs-probe-p09-"))
    adapter = HindsightAdapter(home)
    verdict = "INCONCLUSIVE"
    reason = "not run"
    try:
        ns = f"probe-p09-{ts}"
        adapter.reset(ns)
        assert str(adapter._bank).startswith("pacbench-probe-"), adapter._bank
        trace["bank"] = adapter._bank
        stable_key = _key_of(CONVERSATION[0], logical_document_key)
        trace["expected_stable_key"] = stable_key
        day_texts: list[str] = []
        previously_submitted: set[str] = set()
        ok = True
        for i, (newly_exposed, collected_at) in enumerate(CYCLES):
            if time.monotonic() - t0 > DEADLINE_S:
                reason = "overall deadline exceeded"
                break
            usage = adapter.ingest(newly_exposed, collected_at)
            note = usage.notes[0]
            # Submission log: PACBench-side replication (read-only) of what
            # the adapter grouped into this batch's packets.
            ordered, modes = _packetize(newly_exposed, logical_document_key,
                                        is_conversational)
            submitted_ids = [str(m.get("source_id", "?")) for m in ordered]
            entry = {
                "cycle": i + 1,
                "collected_at": collected_at,
                "submitted_source_ids": submitted_ids,
                "submitted_document_ids": sorted(modes),
                "packet_update_modes": dict(modes),
                "event_dates": [m.get("written_at") for m in ordered],
                "texts": [m.get("text") for m in ordered],
            }
            trace["submission_log"].append(entry)
            trace["cycles"].append(
                {"cycle": i + 1, "collected_at": collected_at,
                 "ingested": [o["source_id"] for o in newly_exposed],
                 "usage": note,
                 "adapter_usage": adapter.usage().__dict__,
                 "submission": entry})
            # PASS gates: one stable key + delta-only (no earlier resend).
            if set(modes) != {stable_key}:
                verdict, reason = (
                    "FAIL",
                    f"submission key drift at cycle {i + 1}: "
                    f"{sorted(modes)} != [{stable_key}]")
                ok = False
                break
            newly_ids = {str(o.get("source_id", "?")) for o in newly_exposed}
            if set(submitted_ids) != newly_ids:
                verdict, reason = (
                    "FAIL",
                    f"cycle {i + 1} submission != delta: "
                    f"{sorted(set(submitted_ids))} vs {sorted(newly_ids)}")
                ok = False
                break
            if set(submitted_ids) & previously_submitted:
                verdict, reason = (
                    "FAIL",
                    f"cycle {i + 1} resent earlier messages: "
                    f"{sorted(set(submitted_ids) & previously_submitted)}")
                ok = False
                break
            previously_submitted |= set(submitted_ids)
            day_texts.extend(o["text"] for o in newly_exposed)
            # Packet-counting adapter: note reads "retained X/Y" where Y =
            # packets submitted this call (one joined item per packet, NOT one
            # per message — a per-message "retained 2/2" check went stale with
            # the one-item-per-packet transport and misread "retained 1/1" as
            # failure in the first remediation run; see probe-dispositions).
            m = re.search(r"retained (\d+)/(\d+)", note)
            if not m or int(m.group(1)) != int(m.group(2)) or int(m.group(2)) < 1:
                reason = (f"cycle {i + 1} incomplete retain: {note!r}")
                ok = False
                break
            # Dual-flavor recall checkpoint.
            synth_payload, _, synth_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank,
                adapter._ctx, QUERY)
            raw_payload, raw_ids, raw_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank,
                adapter._ctx, QUERY, include_source_facts=True)
            same_doc = stable_key in (
                list(raw_ids.get("result_document_ids") or [])
                + list(raw_ids.get("metadata_logical_documents") or [])
                + list(raw_ids.get("source_fact_document_ids") or []))
            # Coverage (not verbatim substring) in both flavors — see _covered.
            raw_has_all = all(_covered(t, raw_blob) for t in day_texts)
            synth_has_all = all(_covered(t, synth_blob) for t in day_texts)
            trace["recalls"].append({
                "after_cycle": i + 1, "query": QUERY,
                "payload": synth_payload,  # compat: synthesized flavor
                "recall_synthesized": synth_payload,
                "recall_raw": raw_payload,
                "raw_evidence_ids": raw_ids,
                "raw_has_all": raw_has_all,
                "synthesized_has_all": synth_has_all,
                "same_doc_association_evidence": same_doc})
            # No-future-leak (either flavor) + recoverability at every cycle.
            # Coverage-based both ways: future messages must NOT reach the bar.
            future = [o["text"] for cyc, _ in CYCLES[i + 1:]
                      for o in cyc]
            if any(_covered(f, synth_blob) for f in future):
                verdict, reason = (
                    "FAIL", f"future leak at cycle {i + 1} (synthesized)")
                ok = False
                break
            if any(_covered(f, raw_blob) for f in future):
                verdict, reason = (
                    "FAIL", f"future leak at cycle {i + 1} (raw evidence)")
                ok = False
                break
            if not raw_has_all:
                verdict, reason = (
                    "INCONCLUSIVE",
                    f"cycle {i + 1} raw evidence absent despite retain "
                    f"success ({note}) — provider-behavior finding")
                ok = False
                break
            if not synth_has_all:
                verdict, reason = (
                    "SYNTHESIS-LIMITATION",
                    f"ingestion PASS through cycle {i + 1} (stable key, "
                    f"delta-only, raw evidence complete) but synthesized "
                    f"text omits expected facts — synthesis noted separately")
                ok = False
                break
        else:
            if ok:
                verdict, reason = (
                    "PASS",
                    "3/3 cycles delta-ok, no leak, all recoverable in both "
                    f"flavors; stable key {stable_key}")
        trace["verdict"] = verdict
        trace["reason"] = reason
        trace["adapter_usage_final"] = adapter.usage().__dict__
    except Exception as exc:  # transport/config failure, never a PASS
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
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
