"""Targeted recall diagnostic for P0-9 cycle-3 anomaly (SPEND-GATED).

Runs ONLY with PACBENCH_TARGETED_RECALL=1 after explicit approval.
Without it, prints the plan and exits 2 (zero spend, zero side effects).

Background: remediation run probe-p09-20260922T150359Z retained all 3 cycles
but cycle-3 recall missed day-3 messages ("car washed", "paperwork"). Two
hypotheses: (a) WRITE-side partial drop, (b) RECALL-side ranking (broad query).

Method (read-only against the EXISTING bank — zero retains, zero writes):
reattach to bank pacbench-probe-p09-20260922T150359Z via ONE reset() on a FRESH
adapter object (safe: fresh objects skip delete_bank and attach to surviving
data; reset is NEVER called twice here), then:
  1. inventory recall ("car pickup Gus Mia", default flavor) — proves the bank
     survived; zero results of any kind → INFRA (bank gone), stop, no conclusion.
  2. targeted recall ("Was the car washed, and what paperwork was mentioned?",
     default + include_source_facts flavors) — coverage-checked with the same
     majority-token rule as the P0-9 harness.

Verdict:
  day-3 covered in targeted → RANKING (write ok; broad query didn't surface it).
  targeted misses but inventory shows other facts → WRITE-SIDE (genuine
  provider-behavior finding: extraction/consolidation dropped day-3 sentences).
  inventory empty → INFRA (bank did not survive reattach; no conclusion drawn).

Bounds: max 3 recall_async (max_tokens=512), scratch home, 900s deadline,
trace runs/targeted-p09c3-<ts>/trace.json (runs/ gitignored). No retains, no
maintenance, no memcal, no large life.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

FLAG = "PACBENCH_TARGETED_RECALL"
DEADLINE_S = 900.0
BANK = "pacbench-probe-p09-20260922T150359Z"
NS = "probe-p09-20260922T150359Z"
INVENTORY_Q = "car pickup Gus Mia"
TARGETED_Q = "Was the car washed, and what paperwork was mentioned?"
DAY3 = ["day3-a: car washed, see you at 6", "day3-b: bringing the paperwork"]


def _plan() -> str:
    return (
        "Targeted-recall diagnostic plan (not executed — set "
        "PACBENCH_TARGETED_RECALL=1):\n"
        f"  reattach to existing bank {BANK} (one reset on a fresh adapter;\n"
        "    never reset twice); 1 inventory + 2 targeted recalls = 3 ops max\n"
        "  verdicts: day-3 covered → RANKING; missed-but-bank-alive → WRITE-SIDE;\n"
        "    bank empty → INFRA"
    )


def main() -> int:
    if os.environ.get(FLAG) != "1":
        print(_plan())
        return 2
    from live_probe_p09_evolution import (_covered, _extract_evidence_ids,
                                          _recall)

    from provider_adapters.hindsight_adapter import HindsightAdapter

    t0 = time.monotonic()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO / "runs" / f"targeted-p09c3-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {"probe": "targeted-p09c3", "started_at": ts, "bank": BANK}
    verdict, reason = "INCONCLUSIVE", "not run"
    adapter = None
    try:
        home = Path(tempfile.mkdtemp(prefix="hs-targeted-"))
        adapter = HindsightAdapter(home)
        # Fresh object: reset() skips delete_bank and ATTACHES to the surviving
        # bank. Never call reset() again on this object (that would drop data).
        adapter.reset(NS)
        assert adapter._bank == BANK, adapter._bank
        # The engine builds its config resolver during initialize(), which the
        # retain path triggers implicitly — a recall-first diagnostic must do it
        # explicitly (else recall_async raises AttributeError: _config_resolver).
        # Read-only: no writes, no bank mutation.
        adapter._loop.run(adapter._engine.initialize())
        if time.monotonic() - t0 > DEADLINE_S:
            raise TimeoutError("deadline exceeded before recalls")
        inv_payload, _, inv_blob = _recall(
            adapter._engine, adapter._loop, adapter._bank, adapter._ctx,
            INVENTORY_Q)
        trace["inventory"] = {"query": INVENTORY_Q, "payload": inv_payload}
        inv_ids = _extract_evidence_ids(
            inv_payload if isinstance(inv_payload, dict)
            else json.loads(inv_payload))
        n_inv = inv_ids.get("n_results") or 0
        if n_inv == 0:
            verdict = "INFRA"
            reason = "bank did not survive reattach (inventory empty)"
        else:
            t_synth, _, t_synth_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank, adapter._ctx,
                TARGETED_Q)
            t_raw, t_ids, t_raw_blob = _recall(
                adapter._engine, adapter._loop, adapter._bank, adapter._ctx,
                TARGETED_Q, include_source_facts=True)
            trace["targeted"] = {
                "query": TARGETED_Q,
                "recall_synthesized": t_synth,
                "recall_raw": t_raw,
                "raw_evidence_ids": t_ids,
            }
            combo = (t_raw_blob or "") + "\n" + (t_synth_blob or "")
            if all(_covered(t, combo) for t in DAY3):
                verdict = "RANKING"
                reason = ("day-3 covered by targeted query: written ok, "
                          "broad query didn't surface it")
            else:
                verdict = "WRITE-SIDE"
                reason = ("day-3 absent even targeted with bank alive: "
                          "extraction/consolidation dropped day-3 sentences")
        trace["verdict"] = verdict
        trace["reason"] = reason
    except Exception as exc:
        verdict, reason = "INCONCLUSIVE", f"{type(exc).__name__}: {exc}"[:300]
        trace["verdict"] = verdict
        trace["reason"] = reason
    finally:
        try:
            if adapter is not None:
                adapter.close()
        except Exception:
            pass
        trace["elapsed_s"] = round(time.monotonic() - t0, 1)
        (out_dir / "trace.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False) + "\n")
    print(f"verdict={verdict} reason={reason} trace={out_dir / 'trace.json'}")
    return 0 if verdict in ("RANKING", "WRITE-SIDE", "INFRA") else 1


if __name__ == "__main__":
    raise SystemExit(main())
