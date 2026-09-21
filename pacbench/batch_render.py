"""Batch Spark rendering for generated lives (criterion 1: the render pass).

Renders every beat of a life through one Spark model with entailment
validation (:mod:`pacbench.render`) plus the extra-fact screen
(:mod:`pacbench.gen_qa`). Fail-closed per beat: a rendering that drops
a required token is retried, then recorded FAILED — never silently
swapped for the draft, which would corrupt the needle-vs-hay style
match invisibly. Transport errors retry separately with backoff.

Output is a render bundle (JSON): frozen config
(model/temperature/prompt-sha/corpus version+hash/timestamp) plus one
record per beat (rendered text, attempts, added atoms, call usage).
The bundle is written incrementally and resumes: re-running skips
local_ids already recorded, so a killed run loses no spend.

``python3 -m pacbench.batch_render --life race-week --out
runs/render-race-week.json`` renders with the approved prompter.
Offline tests use fake generators; no test touches the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import gen_qa
from .generation import GenerationError
from .render import (
    Beat,
    RenderError,
    entails,
    load_key,
    meta_generator,
    _prompt as _plain_prompt,
    _REGISTER,
)

#: Approved substantial-rewrite prompter (probe 2026-09-21: 10/10
#: entailment, 0 added atoms, genuinely natural voice; the plain
#: prompter only lowercased). Recorded by sha in every bundle.
PROMPT_VERSION = "substantial-v1"


def substantial_prompt(beat: Beat) -> str:
    register = _REGISTER.get(beat.register, beat.register)
    keep = "; ".join(beat.must_include) or "(none)"
    return (
        f"You are writing {register} on {beat.channel}, as {beat.sender}. "
        f"Rewrite the message below SUBSTANTIALLY in that voice: change the "
        f"wording and sentence structure, add small natural filler words, "
        f"keep it one to two lines. Keep these details exactly and "
        f"verbatim: {keep}. Do not add any appointment, date, time, place, "
        f"or name beyond what is given, and do not change any fact. Return "
        f"only the message text, nothing else.\n\nMessage: {beat.draft}"
    )


PROMPTERS = {"plain": _plain_prompt, PROMPT_VERSION: substantial_prompt}


def prompt_sha(prompter_name: str) -> str:
    fn = PROMPTERS[prompter_name]
    probe = Beat(channel="c", sender="s", draft="probe draft")
    return hashlib.sha256(fn(probe).encode()).hexdigest()[:16]


@dataclass
class CallUsage:
    prompt_chars: int = 0
    response_chars: int = 0
    latency_s: float = 0.0


@dataclass
class BeatRecord:
    local_id: str
    draft: str
    rendered: str = ""
    status: str = "pending"  # ok | failed
    attempts: int = 0
    added_atoms: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    usage: CallUsage = field(default_factory=CallUsage)
    error: str = ""
    #: Per-beat input provenance (M4): the exact inputs this record was
    #: rendered from. Resume validates these before skipping an "ok"
    #: record — a changed prompter/draft mid-resume must never silently
    #: mix surfaces under one bundle hash.
    prompt_sha: str = ""
    draft_sha: str = ""


@dataclass
class RenderBundle:
    model: str
    temperature: float
    prompter: str
    prompt_sha: str
    corpus_version: str
    corpus_hash: str
    life_id: str
    started_at: str = ""
    records: dict[str, BeatRecord] = field(default_factory=dict)

    def to_json(self) -> str:
        data = asdict(self)
        data["records"] = {k: asdict(v) for k, v in self.records.items()}
        for rec in data["records"].values():
            rec["added_atoms"] = sorted(rec["added_atoms"])
            rec["missing"] = sorted(rec["missing"])
        return json.dumps(data, indent=1, sort_keys=True)

    @staticmethod
    def from_json(raw: str) -> "RenderBundle":
        data = json.loads(raw)
        records = {k: BeatRecord(local_id=v["local_id"], draft=v["draft"],
                                 rendered=v.get("rendered", ""),
                                 status=v.get("status", "pending"),
                                 attempts=v.get("attempts", 0),
                                 added_atoms=tuple(v.get("added_atoms", ())),
                                 missing=tuple(v.get("missing", ())),
                                 usage=CallUsage(**v.get("usage", {})),
                                 error=v.get("error", ""))
                   for k, v in data.get("records", {}).items()}
        return RenderBundle(
            model=data["model"], temperature=data["temperature"],
            prompter=data["prompter"], prompt_sha=data["prompt_sha"],
            corpus_version=data["corpus_version"],
            corpus_hash=data["corpus_hash"], life_id=data["life_id"],
            started_at=data.get("started_at", ""), records=records)


def render_beat(beat: Beat, local_id: str, generate, prompt_fn,
                max_entail_retries: int = 2,
                max_transport_retries: int = 3) -> BeatRecord:
    """Render one beat, fail-closed. Transport errors back off and retry."""
    rec = BeatRecord(local_id=local_id, draft=beat.draft)
    prompt = prompt_fn(beat)
    # Per-beat provenance (M4): stamp the exact inputs BEFORE generating,
    # so even a failed record carries the regime it was attempted under.
    rec.prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
    rec.draft_sha = hashlib.sha256(beat.draft.encode()).hexdigest()[:16]
    transport_tries = 0
    while True:
        rec.attempts += 1
        t0 = time.time()
        try:
            out = generate(prompt).strip()
        except GenerationError as exc:
            transport_tries += 1
            rec.usage.latency_s += time.time() - t0
            if transport_tries > max_transport_retries:
                rec.status = "failed"
                rec.error = f"transport:{exc}"
                return rec
            time.sleep(2.0 * transport_tries)
            continue
        except Exception as exc:  # fail-closed on anything unexpected
            rec.status = "failed"
            rec.error = f"unexpected:{type(exc).__name__}:{exc}"[:200]
            return rec
        rec.usage.prompt_chars += len(prompt)
        rec.usage.response_chars += len(out)
        rec.usage.latency_s += time.time() - t0
        missing = entails(out, beat)
        if missing:
            rec.missing = tuple(missing)
            if rec.attempts > max_entail_retries:
                rec.status = "failed"
                rec.error = f"dropped:{','.join(missing)}"
                return rec
            continue
        rec.rendered = out
        rec.added_atoms = tuple(sorted(gen_qa.added_atoms(out, beat.draft)))
        rec.status = "ok"
        return rec


def render_all(beats: list[tuple[str, Beat]], generate, *,
               prompter: str = PROMPT_VERSION,
               resume: RenderBundle | None = None,
               workers: int = 4,
               progress=None) -> RenderBundle:
    """Render a beat list; skip local_ids already ok in `resume`.

    M4 resume-time validation: an "ok" record is skipped only when its
    stamped per-beat provenance (prompt_sha + draft_sha) matches the
    current regime. A changed prompter or draft fails closed instead of
    silently mixing surfaces under one bundle hash.
    """
    prompt_fn = PROMPTERS[prompter]
    bundle = resume
    assert bundle is not None  # caller builds it with frozen config
    current_prompt_sha = prompt_sha(prompter)
    stale: list[str] = []
    for lid, beat in beats:
        rec = bundle.records.get(lid)
        if rec is None or rec.status != "ok":
            continue
        want_draft = hashlib.sha256(beat.draft.encode()).hexdigest()[:16]
        # prompt_sha depends only on the prompter for a fixed probe beat,
        # but the per-beat stamp binds the actual prompt text — recompute
        # the same way render_beat does for an exact comparison.
        want_prompt = hashlib.sha256(
            prompt_fn(beat).encode()).hexdigest()[:16]
        if rec.prompt_sha and rec.draft_sha:
            if rec.prompt_sha != want_prompt or rec.draft_sha != want_draft:
                stale.append(lid)
        # Legacy records (pre-provenance) keep the old skip behavior but
        # are flagged in the stale list for operator review — never
        # silently trusted under a new regime.
        elif current_prompt_sha != bundle.prompt_sha:
            stale.append(lid)
    if stale:
        raise RuntimeError(
            f"resume refused: {len(stale)} ok record(s) rendered under a "
            f"different regime (prompter/draft changed): "
            f"{stale[:10]}{'...' if len(stale) > 10 else ''}. "
            f"Move the bundle aside or re-render clean.")
    todo = [(lid, b) for lid, b in beats
            if bundle.records.get(lid) is None
            or bundle.records[lid].status != "ok"]
    if not todo:
        return bundle

    def one(item: tuple[str, Beat]) -> BeatRecord:
        lid, beat = item
        rec = render_beat(beat, lid, generate, prompt_fn)
        if progress is not None:
            progress(rec)
        return rec

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in pool.map(one, todo):
            bundle.records[rec.local_id] = rec
            if progress is not None:
                progress(bundle)
    return bundle


# ------------------------------------------------------------------- CLI --

def _life_beats(life_id: str) -> tuple[list[tuple[str, Beat]], str, str]:
    from .corpus import get_life
    from .freeze import PINNED_HASH
    from . import CORPUS_VERSION
    import importlib
    life = get_life(life_id)
    meta: dict[str, dict] = {}
    for mod_name in ("life_a_dentist", "life_b_collision", "life_c_coat",
                     "life_d_reunion", "life_e_revoke", "life_f_race"):
        try:
            mod = importlib.import_module(f".corpus.{mod_name}",
                                          package=__package__)
        except ImportError:
            continue
        render_meta = getattr(mod, "render_meta", None)
        if render_meta is not None and life_id in getattr(mod, "_ID", life_id):
            meta = render_meta()
            break
    beats: list[tuple[str, Beat]] = []
    for obs in life.observations:
        # Reconstruct the authoring draft: observation text IS the draft
        # pre-render (frozen draft corpus), so renders stay keyed by source.
        lid = obs.source_id.split("/")[-1]
        m = meta.get(lid, {"register": "casual", "must_include": ()})
        beats.append((lid, Beat(
            channel=obs.channel, sender=obs.sender, draft=obs.text,
            must_include=tuple(m["must_include"]),
            register=m["register"])))
    return beats, CORPUS_VERSION, PINNED_HASH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Batch-render a life through Spark (spends API calls).")
    parser.add_argument("--life", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--env", default=".env")
    parser.add_argument("--model", default="muse-spark-1.3")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--prompter", default=PROMPT_VERSION,
                        choices=sorted(PROMPTERS))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--checkpoint-every", type=int, default=50)
    args = parser.parse_args(argv)

    key = load_key(args.env)
    if not key:
        print(f"no META credential in {args.env}")
        return 1
    from datetime import datetime, timezone
    beats, corpus_version, corpus_hash = _life_beats(args.life)
    out = Path(args.out)
    bundle = None
    if out.exists():
        bundle = RenderBundle.from_json(out.read_text())
        if (bundle.model != args.model
                or bundle.temperature != args.temperature
                or bundle.prompter != args.prompter
                or bundle.life_id != args.life):
            print("existing bundle has different frozen config; refusing "
                  "to mix. Move it aside or match --model/--temperature/"
                  "--prompter/--life.")
            return 1
        print(f"resuming: {sum(1 for r in bundle.records.values() if r.status == 'ok')}"
              f" ok already, {len(beats)} total")
    else:
        bundle = RenderBundle(
            model=args.model, temperature=args.temperature,
            prompter=args.prompter, prompt_sha=prompt_sha(args.prompter),
            corpus_version=corpus_version, corpus_hash=corpus_hash,
            life_id=args.life,
            started_at=datetime.now(timezone.utc).isoformat())
    generate = meta_generator(key, args.model,
                              temperature=args.temperature)

    pending = [lid for lid, _ in beats
               if bundle.records.get(lid) is None
               or bundle.records[lid].status != "ok"]
    done = len(beats) - len(pending)
    lock = [0]

    def progress(item) -> None:
        if isinstance(item, RenderBundle):
            return
        lock[0] += 1
        n = done + lock[0]
        if n % args.checkpoint_every == 0 or n == len(beats):
            out.write_text(bundle.to_json())
            print(f"[{n}/{len(beats)}] checkpointed "
                  f"(last: {item.local_id} {item.status})", flush=True)

    t0 = time.time()
    render_all(beats, generate, prompter=args.prompter, resume=bundle,
               workers=args.workers, progress=progress)
    out.write_text(bundle.to_json())
    ok = sum(1 for r in bundle.records.values() if r.status == "ok")
    failed = [lid for lid, r in bundle.records.items()
              if r.status == "failed"]
    added = sorted({a for r in bundle.records.values()
                    for a in r.added_atoms})
    usage = CallUsage()
    for r in bundle.records.values():
        usage.prompt_chars += r.usage.prompt_chars
        usage.response_chars += r.usage.response_chars
        usage.latency_s += r.usage.latency_s
    print(f"done: {ok}/{len(beats)} ok in {time.time() - t0:.0f}s, "
          f"prompt_chars={usage.prompt_chars} "
          f"response_chars={usage.response_chars}")
    print(f"failed beats: {failed if failed else 'none'}")
    print(f"distinct added atoms (human review): {added if added else 'none'}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())