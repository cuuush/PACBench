"""Discriminating experiment: replace vs append on a brand-new document (SPEND-GATED).

Runs ONLY with PACBENCH_LIVE_DISCRIM=1 after explicit spend approval.
Without it, prints the plan and exits 2 (zero spend, zero side effects).

Question: why did P0-9r1 and P0-10r1 both return per-item empty ids with zero
token usage in ~2.4s, while smoke (per-message replace, no return_usage) retained
5/8 with ~28s/extraction? Prime suspect: append-on-new-document interaction.

Design (≤2 paid ops, 0 recalls — the empty-vs-nonempty signal IS the discriminator):
  arm A (smoke shape): 1 email-channel observation → singleton → update_mode=replace,
      fresh bank pacbench-discrim-a-*.
  arm B (probe shape): 1 text-channel observation → conversational → update_mode=append
      on a brand-new doc id, fresh bank pacbench-discrim-b-*.
Same text, same event_date, same collected_at shape; only channel/mode differ.

Verdict:
  A retains + B empties → trigger isolated to append-on-new-doc (adapter bootstrap
      fix: replace for cycle 1, append thereafter).
  both empty → trigger elsewhere (engine content filter/gateway); deeper investigation.
  both retain → original empties stochastic; resume probe order.
  either errors → INCONCLUSIVE (infra), no conclusion drawn.

Bounds: scratch tempdir homes, probe bank-prefix asserts, 900s deadline,
trace runs/discrim-<ts>/trace.json (runs/ gitignored). No large life, no Hermes,
no maintenance, no memcal.
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

FLAG = "PACBENCH_LIVE_DISCRIM"
DEADLINE_S = 900.0
TEXT = "the lake cabin rental ends October 5"
COLLECTED_AT = "2026-09-18T12:00:00"


def _plan() -> str:
    return (
        "Discriminating experiment plan (not executed — set PACBENCH_LIVE_DISCRIM=1):\n"
        "  arm A: 1 email obs (replace mode), fresh bank; arm B: 1 text obs\n"
        "    (append mode, new doc), fresh bank. Same text/event_date.\n"
        "  ops: exactly 2 retain_batch_async, 0 recalls\n"
        "  verdict: A-retains+B-empties → append-on-new-doc trigger;\n"
        "    both-empty → engine-side; both-retain → stochastic; error → INCONCLUSIVE"
    )


def _obs(source_id: str, channel: str, thread: str) -> dict:
    return {
        "source_id": source_id,
        "channel": channel,
        "thread": thread,
        "sender": "Mia",
        "text": TEXT,
        "written_at": "2026-09-18T10:00:00",
        "arrived_at": "2026-09-18T10:00:30",
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
    out_dir = REPO / "runs" / f"discrim-{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    trace: dict = {"probe": "discrim-replace-vs-append", "started_at": ts,
                   "arms": {}}
    verdict, reason = "INCONCLUSIVE", "not run"
    adapters = []
    try:
        arms = {
            "A-replace": _obs("discrim/mail-1", "email", "inbox"),
            "B-append-new-doc": _obs("discrim/txt-1", "text", "fam"),
        }
        for arm, obs in arms.items():
            if time.monotonic() - t0 > DEADLINE_S:
                reason = "overall deadline exceeded"
                break
            home = Path(tempfile.mkdtemp(prefix="hs-discrim-"))
            adapter = HindsightAdapter(home)
            adapters.append(adapter)
            ns = f"discrim-{arm}-{ts}".lower().replace("_", "-")
            adapter.reset(ns)
            assert str(adapter._bank).startswith("pacbench-discrim-"), adapter._bank
            usage = adapter.ingest([obs], COLLECTED_AT)
            trace["arms"][arm] = {
                "bank": adapter._bank,
                "usage": usage.notes[0],
                "adapter_usage": usage.__dict__,
            }
        else:
            a = trace["arms"]["A-replace"]["usage"]
            b = trace["arms"]["B-append-new-doc"]["usage"]
            a_ok = a.startswith("retained 1/1")
            b_ok = b.startswith("retained 1/1")
            if a_ok and not b_ok:
                verdict = "APPEND-NEW-DOC-TRIGGER"
                reason = "replace retained 1/1, append-on-new-doc empty"
            elif not a_ok and not b_ok:
                verdict = "ENGINE-SIDE"
                reason = "both arms empty — trigger is not append-specific"
            elif a_ok and b_ok:
                verdict = "STOCHASTIC"
                reason = "both retained — original empties not systematic"
            else:
                verdict = "APPEND-NEW-DOC-TRIGGER"
                reason = f"unexpected split: A={a!r} B={b!r}"
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
    return 0 if verdict in ("APPEND-NEW-DOC-TRIGGER", "ENGINE-SIDE",
                            "STOCHASTIC") else 1


if __name__ == "__main__":
    raise SystemExit(main())
