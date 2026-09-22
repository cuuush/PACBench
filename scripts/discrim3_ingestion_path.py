"""Three-arm ingestion-path discriminator (SPEND-GATED, NOT YET AUTHORIZED).

Runs ONLY with PACBENCH_LIVE_DISCRIM3=1 after explicit spend approval.
Without it, prints the plan and exits 2 (zero spend, zero side effects).

Question: empty retains correlate 0/6 with two-items-one-doc batches vs 5/6
retention for single-item packets. Three arms separate the mechanisms; content,
timestamps, collected_at, and simulated context identical across arms:

  arm A: two obs, SAME document, ONE grouped ingest (current probe shape).
  arm B: same two obs, SAME document, TWO sequential one-item ingests
         (second appends onto the first).
  arm C: two obs, TWO document keys, same top-level pathway as A
         (one ingest call, two packets).

Ops: 4 retain_batch_async (A=1, B=2, C=1), 0 recalls — ids are the signal.

Verdict table:
  A fails, B+C succeed → multi-item same-document GROUPING path.
  A+B fail, C succeeds → same-document IDENTITY/update semantics.
  A+C fail, B succeeds → multi-item single-INVOCATION (identity-independent).
  all succeed → stochasticity remains the best explanation; resume probe order.
  all fail → broader change; stop paid probing again.
  any error → INCONCLUSIVE (infra), no conclusion drawn.

Bounds: scratch tempdir homes, banks pacbench-discrim3-*, asserts, 1200s
deadline, trace runs/discrim3-<ts>/trace.json (runs/ gitignored). No large
life, no Hermes, no maintenance, no memcal, no recalls.
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

FLAG = "PACBENCH_LIVE_DISCRIM3"
DEADLINE_S = 1200.0
COLLECTED_AT = "2026-09-18T12:00:00"
TEXTS = ("day1-a: the car is ready Friday", "day1-b: pickup after 5pm works")


def _plan() -> str:
    return (
        "Three-arm discriminator plan (not executed — set PACBENCH_LIVE_DISCRIM3=1):\n"
        "  A: 2 obs same doc, 1 ingest; B: same 2 obs same doc, 2 sequential\n"
        "    1-item ingests; C: 2 obs 2 docs, 1 ingest. Same texts/timestamps.\n"
        "  ops: exactly 4 retain_batch_async, 0 recalls\n"
        "  verdicts: A-only-fail → grouping path; A+B-fail → identity/update;\n"
        "    A+C-fail → multi-item invocation; all-pass → stochastic;\n"
        "    all-fail → stop; error → INCONCLUSIVE"
    )


def _obs(source_id: str, thread: str, text: str) -> dict:
    i = TEXTS.index(text)
    return {
        "source_id": source_id,
        "channel": "text",
        "thread": thread,
        "sender": "Gus" if i == 0 else "Mia",
        "text": text,
        "written_at": f"2026-09-18T09:0{i * 5:02d}:00",
        "arrived_at": f"2026-09-18T09:0{i * 5:02d}:30",
        "collected_at": COLLECTED_AT,
        "from_owner": False,
        "structured": None,
    }


def main() -> int:
    if os.environ.get(FLAG) != "1":
        print(_plan())
        return 2
    from provider_adapters.hindsight_adapter import HindsightAdapter

    t0 = time.monotonic()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO / "runs" / f"discrim3-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {"probe": "discrim3-ingestion-path", "started_at": ts,
                   "arms": {}}
    verdict, reason = "INCONCLUSIVE", "not run"
    adapters = []
    try:
        plan: dict = {
            # arm -> list of ingest calls, each a list of obs
            "A-grouped-same-doc": [
                [_obs("d3/a-1", "fam", TEXTS[0]),
                 _obs("d3/a-2", "fam", TEXTS[1])],
            ],
            "B-sequential-same-doc": [
                [_obs("d3/b-1", "fam", TEXTS[0])],
                [_obs("d3/b-2", "fam", TEXTS[1])],
            ],
            "C-grouped-two-docs": [
                [_obs("d3/c-1", "fam-a", TEXTS[0]),
                 _obs("d3/c-2", "fam-b", TEXTS[1])],
            ],
        }
        for arm, calls in plan.items():
            if time.monotonic() - t0 > DEADLINE_S:
                reason = "overall deadline exceeded"
                break
            home = Path(tempfile.mkdtemp(prefix="hs-discrim3-"))
            adapter = HindsightAdapter(home)
            adapters.append(adapter)
            ns = f"discrim3-{arm}-{ts}".lower().replace("_", "-")
            adapter.reset(ns)
            assert str(adapter._bank).startswith("pacbench-discrim3-"), adapter._bank
            arm_notes = []
            for obs_batch in calls:
                usage = adapter.ingest(obs_batch, COLLECTED_AT)
                arm_notes.append(usage.notes[0])
            trace["arms"][arm] = {"bank": adapter._bank, "calls": arm_notes}
        else:
            ok = {arm: all(n.startswith("retained 1/1") or
                           n.startswith("retained 2/2")
                           for n in info["calls"])
                  for arm, info in trace["arms"].items()}
            a, b, c = (ok["A-grouped-same-doc"], ok["B-sequential-same-doc"],
                       ok["C-grouped-two-docs"])
            if not a and b and c:
                verdict = "GROUPING-PATH"
                reason = "only grouped same-doc fails"
            elif not a and not b and c:
                verdict = "IDENTITY-UPDATE"
                reason = "same-doc fails grouped and sequential; two-docs pass"
            elif not a and not c and b:
                verdict = "MULTI-ITEM-INVOCATION"
                reason = "any multi-item invocation fails, identity-independent"
            elif a and b and c:
                verdict = "STOCHASTIC"
                reason = "all arms retained — stochasticity best explanation"
            elif not a and not b and not c:
                verdict = "BROADER-CHANGE"
                reason = "all arms fail — stop paid probing"
            else:
                verdict = "UNEXPECTED-SPLIT"
                reason = f"A={a} B={b} C={c}"
        trace["verdict"] = verdict
        trace["reason"] = reason
    except Exception as exc:
        verdict, reason = "INCONCLUSIVE", f"{type(exc).__name__}: {exc}"[:300]
        trace["verdict"] = verdict
        trace["reason"] = reason
    finally:
        for adapter in adapters:
            try:
                adapter.close()
            except Exception:
                pass
        trace["elapsed_s"] = round(time.monotonic() - t0, 1)
        (out_dir / "trace.json").write_text(
            json.dumps(trace, indent=2, ensure_ascii=False) + "\n")
    print(f"verdict={verdict} reason={reason} trace={out_dir / 'trace.json'}")
    return 0 if verdict in ("GROUPING-PATH", "IDENTITY-UPDATE",
                            "MULTI-ITEM-INVOCATION", "STOCHASTIC",
                            "BROADER-CHANGE", "UNEXPECTED-SPLIT") else 1


if __name__ == "__main__":
    raise SystemExit(main())
