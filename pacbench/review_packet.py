"""Human review packet builder (criteria 1 + 5).

Assembles everything a reviewer (Chris, co-author) needs to sign off a
generated life BEFORE any pilot touches it: truth, checkpoints with
cited evidence texts, support windows, validator output, contradiction
dispositions, QA screens, measured difficulty, and a seeded ambient
sample for the needle-vs-hay style review.

Works on the draft corpus (no bundle) and on Spark renders (bundle):
``python3 -m pacbench.review_packet --life race-week --out
runs/review-packet-race-week-draft.md``. Packets are gitignored review
artifacts, never corpus. Deterministic: seeded sampling, no model.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from . import gen_qa
from .contradictions import KNOWN, scan_contradictions
from .corpus import get_life, MANIFESTS
from .difficulty import (
    evidence_age_hours,
    relevant_thread_size,
    required_channels,
    support_batches,
    thread_sizes,
)
from .freeze import PINNED_HASH
from . import CORPUS_VERSION
from .validate import validate_life

AMBIENT_N = 30
EVIDENCE_SAMPLE_N = 10
SAMPLER_SEED = 20260921


def _texts(life, bundle: dict | None) -> dict[str, str]:
    """source_id -> display text (rendered when bundled, else draft)."""
    if bundle is None:
        return {o.source_id: o.text for o in life.observations}
    recs = bundle["records"]
    out = {}
    for o in life.observations:
        lid = o.source_id.split("/")[-1]
        rec = recs.get(lid)
        if rec is not None and rec.get("status") == "ok":
            out[o.source_id] = rec["rendered"]
        else:
            out[o.source_id] = o.text + " [UNRENDERED]"
    return out


def build_packet(life_id: str, bundle: dict | None = None) -> str:
    life = get_life(life_id)
    manifest = MANIFESTS[life_id]
    texts = _texts(life, bundle)
    by_id = {o.source_id: o for o in life.observations}
    rng = random.Random(f"{SAMPLER_SEED}-{life_id}")
    lines: list[str] = []

    bundled = bundle is not None
    n_ok = 0
    recomputed: set = set()
    if bundled:
        n_ok = sum(1 for r in bundle["records"].values()
                   if r.get("status") == "ok")
        # Added atoms are RECOMPUTED with the current screens (they improve;
        # the bundle keeps its at-render-time record as the audit trail).
        recomputed = set()
        for o in life.observations:
            rec = bundle["records"].get(o.source_id.split("/")[-1])
            if rec is not None and rec.get("status") == "ok":
                recomputed.update(
                    gen_qa.added_atoms(rec["rendered"], o.text))
    lines.append(f"# Review packet: {life_id} "
                 f"({'SPARK-RENDERED' if bundled else 'DRAFT'})")
    lines.append("")
    lines.append(f"- lineage: {life.lineage} | bet: {life.bet} | role: {life.role}")
    lines.append(f"- corpus: version {CORPUS_VERSION}, hash {PINNED_HASH[:12]}")
    lines.append(f"- scale: {len(life.observations)} obs / "
                 f"{len(life.checkpoints)} checkpoints / {len(life.truth)} truth")
    if bundled:
        lines.append(f"- render: model {bundle['model']}, temp "
                     f"{bundle['temperature']}, prompter {bundle['prompter']} "
                     f"(sha {bundle['prompt_sha']}), {n_ok} ok")
        failed = [k for k, r in bundle["records"].items()
                  if r.get("status") != "ok"]
        lines.append(f"- failed beats: {failed if failed else 'none'}")
        lines.append(f"- distinct added atoms (current screens): "
                     f"{sorted(recomputed) if recomputed else 'none'}")
    else:
        lines.append("- render: NONE — drafts verbatim (style review N/A)")

    errors = validate_life(life, manifest)
    lines.append(f"- structural validation: "
                 f"{'CLEAN' if not errors else 'ERRORS: ' + '; '.join(errors)}")
    report = gen_qa.noise_report(life, manifest)
    lines.append(f"- measured support: {report['supported']}/"
                 f"{report['total']} = {report['ratio']} "
                 f"({'in' if report['in_acceptance_band'] else 'OUT OF'} "
                 f"2–10% band)")
    lines.append("")

    lines.append("## Truth")
    for t in life.truth:
        lines.append(f"- {t.id}: {t.statement}")
        lines.append(f"  established_by: {', '.join(t.established_by)}")
    lines.append("")

    lines.append("## Checkpoints (with cited evidence)")
    for cp in life.checkpoints:
        lines.append(f"### {cp.id.split('/')[-1]} [{cp.mode}] @ {cp.at}")
        lines.append(f"Q: {cp.question}")
        for slot in (*cp.slots, *cp.supported):
            lines.append(
                f"- slot {slot.id} ({slot.kind.name}/{slot.norm.name}): "
                f"expected={slot.expected!r} "
                f"forbidden={list(slot.forbidden) or '-'} "
                f"error={slot.error or '-'}")
            for sid in slot.supported_by:
                lines.append(f"  [{sid}] {texts.get(sid, '?MISSING?')}")
        for trap in cp.forbidden_options:
            lines.append(
                f"- TRAP {trap.id}: {trap.expected!r} (error={trap.error})")
    lines.append("")

    lines.append("## Support windows (manifest → texts)")
    for entry in manifest.entries:
        lines.append(f"- {entry.fact_id}: {entry.note}")
        for sid in entry.observation_ids:
            o = by_id.get(sid)
            when = o.collected_at if o else "?"
            lines.append(f"  [{sid} @ {when}] {texts.get(sid, '?MISSING?')}")
    lines.append("")

    lines.append("## Contradiction dispositions (this life)")
    cands = scan_contradictions(life)
    lines.append(f"- scan candidates: {len(cands)}")
    for cand in cands:
        disp = KNOWN.get(cand.key(), "UNDISPOSITIONED")
        lines.append(f"- [{cand.obs_id}] matched={list(cand.matched)} "
                     f"vs {cand.checkpoint_id}/{cand.slot_id}: {disp}")
    lines.append("")

    lines.append("## Measured difficulty (frozen corpus)")
    lines.append(f"- thread sizes: {thread_sizes(life)}")
    for cp in life.checkpoints:
        short = cp.id.split("/")[-1]
        lines.append(
            f"- {short}: relevant_thread={relevant_thread_size(life, cp)} "
            f"batches={support_batches(life, cp)} "
            f"age_h={evidence_age_hours(life, cp)} "
            f"channels={sorted(required_channels(life, cp))}")
    lines.append("")

    lines.append("## Ambient sample (needle-vs-hay material, seeded)")
    supported = {sid for e in manifest.entries for sid in e.observation_ids}
    filler = [o for o in life.observations
              if o.source_id not in supported]
    sample = rng.sample(filler, min(AMBIENT_N, len(filler)))
    for o in sample:
        lines.append(f"- [{o.channel}/{o.sender}] {texts[o.source_id]}")
    lines.append("")

    lines.append("## Evidence style sample (draft → render)")
    evidence = [o for o in life.observations
                if o.source_id in supported]
    for o in rng.sample(evidence, min(EVIDENCE_SAMPLE_N, len(evidence))):
        lines.append(f"- [{o.source_id}]")
        lines.append(f"  draft : {o.text}")
        if bundled:
            lines.append(f"  render: {texts[o.source_id]}")
    lines.append("")

    lines.append("## Reviewer sign-off (all required before pilots)")
    lines.append("- [ ] needle-vs-hay: evidence voice ≈ filler voice (no quiz-sheet tells)")
    lines.append("- [ ] extra facts: added-atoms list reviewed; no invented dates/times/names")
    lines.append("- [ ] failed beats (if any): dispositioned or re-rendered")
    lines.append("- [ ] support: every checkpoint answerable from cited texts, traps fair")
    lines.append("- [ ] difficulty: depth/noise/half-life coverage as specified")
    lines.append("- [ ] rubric: expected values literal in cited evidence (validator-clean)")
    lines.append("- [ ] SIGN-OFF (name/date): ")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a review packet.")
    parser.add_argument("--life", required=True)
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    bundle = None
    if args.bundle:
        bundle = json.loads(Path(args.bundle).read_text())
    packet = build_packet(args.life, bundle)
    Path(args.out).write_text(packet)
    print(f"wrote {args.out} ({len(packet)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
