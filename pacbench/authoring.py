"""PACBench life authoring: the structured plan a fictional life is built from.

A `LifePlan` is the human-reviewable authoring source of truth for one life. It
describes the meaningful stories — people, threads, state transitions, difficulty
settings, required evidence placements, noise budgets, and hidden checkpoints — but
it never contains finished message text beyond plain drafts.

A generation model (Spark) only writes *surface language*: each `BeatPlan` carries a
`draft` that already states the fact plus atomic `must_include` tokens that define
its meaning. Rendering goes through :mod:`pacbench.render`, which rejects any
rewrite that drops a required token. The model therefore does the tedious surface
work while the plan keeps the answer key. With no generator, the draft ships
verbatim, so the corpus still builds free, offline, and reproducibly.

Nothing here is scored-run visible: :func:`build_life` returns the public `Life`
(evidence + hidden rubric) alongside a `SupportManifest` that stays on the
authoring side of the boundary (see :mod:`pacbench.manifest`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .life import Life, TruthFact
from .manifest import SupportEntry, SupportManifest
from .observation import CHANNELS, Observation
from .render import Beat, Generate, entails, render
from .rubric import Checkpoint


class AuthoringError(RuntimeError):
    """A life plan that cannot be built as specified."""


@dataclass(frozen=True)
class Person:
    """Someone who appears in the life. Identity is what the evidence says it is."""

    name: str
    relation: str = ""  # e.g. "organizer of Eric's poker game", "owner's mom"


@dataclass(frozen=True)
class ThreadPlan:
    """A channel thread the beats play out in. Provenance, not a tenant boundary."""

    id: str
    channel: str
    participants: tuple[str, ...] = ()


@dataclass(frozen=True)
class BeatPlan:
    """One meaningful message. `draft` states the fact plainly; `must_include` are
    the atomic tokens a rendering must preserve verbatim."""

    local_id: str
    thread: str
    sender: str
    day: int                    # offset from the plan's week_start (may be negative
                                # for older history)
    clock: str                  # "HH:MM" on that day
    draft: str
    fact_id: str = ""
    must_include: tuple[str, ...] = ()
    register: str = "casual"
    from_owner: bool = False
    arrival_delay_min: int = 0
    collection_delay_min: int = 0
    structured: dict | None = None


@dataclass(frozen=True)
class LifePlan:
    """The complete authoring plan for one life."""

    id: str
    bet: str
    role: str
    week_start: str             # ISO date of scored-day 0 (a Monday)
    checkpoints: tuple[Checkpoint, ...]
    #: §39 story family, passed through to Life.lineage.
    lineage: str = ""
    truth: tuple[TruthFact, ...] = ()
    people: tuple[Person, ...] = ()
    threads: tuple[ThreadPlan, ...] = ()
    beats: tuple[BeatPlan, ...] = ()
    title: str = ""
    horizon_days: int = 7
    difficulty_tags: tuple[str, ...] = ()
    control_of: str | None = None
    noise_budget: int = 0       # informative: how many filler beats the life wants


def _stamp(day_start: date, day: int, clock: str, delay_min: int = 0) -> str:
    hour, minute = (int(p) for p in clock.split(":"))
    moment = datetime(day_start.year, day_start.month, day_start.day,
                      hour, minute) + timedelta(days=day, minutes=delay_min)
    return moment.isoformat(timespec="seconds")


def build_life(plan: LifePlan, generate: Generate | None = None,
               ) -> tuple[Life, SupportManifest]:
    """Render a plan into a `Life` plus its support manifest.

    `generate` is the optional Spark rewrite hook (surface language only). With
    `None`, drafts ship verbatim — deterministic and offline.
    """
    try:
        week_start = date.fromisoformat(plan.week_start)
    except ValueError as exc:
        raise AuthoringError(f"plan {plan.id!r}: bad week_start {plan.week_start!r}") from exc
    threads = {t.id: t for t in plan.threads}
    seen: set[str] = set()
    observations: list[Observation] = []
    by_fact: dict[str, list[str]] = {}
    for beat in plan.beats:
        if beat.local_id in seen:
            raise AuthoringError(f"plan {plan.id!r}: duplicate beat {beat.local_id!r}")
        seen.add(beat.local_id)
        thread = threads.get(beat.thread)
        if thread is None:
            raise AuthoringError(f"plan {plan.id!r}: beat {beat.local_id!r} "
                                 f"names unknown thread {beat.thread!r}")
        if thread.channel not in CHANNELS:
            raise AuthoringError(f"plan {plan.id!r}: unknown channel {thread.channel!r}")
        rendered = render(Beat(channel=thread.channel, sender=beat.sender,
                               draft=beat.draft, must_include=beat.must_include,
                               register=beat.register), generate=generate)
        missing = entails(rendered, Beat(channel=thread.channel, sender=beat.sender,
                                         draft=beat.draft,
                                         must_include=beat.must_include))
        if missing:
            raise AuthoringError(f"plan {plan.id!r}: beat {beat.local_id!r} draft itself "
                                 f"drops required tokens {missing}")
        source_id = f"{plan.id}/{beat.local_id}"
        written = _stamp(week_start, beat.day, beat.clock)
        observations.append(Observation(
            source_id=source_id, channel=thread.channel,
            written_at=written,
            arrived_at=_stamp(week_start, beat.day, beat.clock,
                              beat.arrival_delay_min),
            collected_at=_stamp(week_start, beat.day, beat.clock,
                                beat.arrival_delay_min + beat.collection_delay_min),
            sender=beat.sender, thread=thread.id, text=rendered,
            from_owner=beat.from_owner, structured=beat.structured))
        if beat.fact_id:
            by_fact.setdefault(beat.fact_id, []).append(source_id)
    manifest = SupportManifest(
        life_id=plan.id,
        entries=tuple(SupportEntry(fact_id=fid, observation_ids=tuple(ids))
                      for fid, ids in sorted(by_fact.items())))
    life = Life(id=plan.id, bet=plan.bet, role=plan.role,
                lineage=plan.lineage,
                observations=tuple(observations), checkpoints=plan.checkpoints,
                control_of=plan.control_of, title=plan.title,
                horizon_days=plan.horizon_days,
                difficulty_tags=plan.difficulty_tags, truth=plan.truth)
    return life, manifest
