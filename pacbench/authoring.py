"""Build a dialogue from prose and verify it before freezing observations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .observation import CHANNELS, Observation
from .model_calls import ModelOutput

Generate = Callable[[str], str | ModelOutput]


class AuthoringError(RuntimeError):
    """A scene that cannot be built as specified."""


class SceneRenderError(AuthoringError):
    """A scene rendering that could not be parsed into messages."""

    def __init__(self, scene_id: str, output: str, problem: str):
        super().__init__(f"scene {scene_id!r}: {problem}")
        self.scene_id, self.output, self.problem = scene_id, output, problem


class SceneJudgeError(AuthoringError):
    """A scene rendering the narrow judge rejected (or an unusable verdict)."""

    def __init__(self, scene_id: str, problem: str,
                 judgment_path: str | None = None):
        super().__init__(f"scene {scene_id!r}: judge rejected: {problem}")
        self.scene_id, self.problem = scene_id, problem
        #: Audit record for the failed call (None when not recording).
        self.judgment_path = judgment_path


@dataclass(frozen=True)
class ScenePlan:
    """Describe a thread and, optionally, the facts it must establish."""

    scene: str
    test_intent: str = ""
    facts: tuple[str, ...] = ()
    id: str = "scene"
    channel: str = "text"
    thread: str = ""
    at: str = ""
    window_start: str = ""
    window_end: str = ""
    timezone: str = ""
    message_range: tuple[int, int] | None = None
    participants: tuple[str, ...] = ()
    chat_name: str = ""
    email_subject: str = ""
    noise: str = "low"
    messages_per_person: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.participants:
            if (not isinstance(self.participants,(list,tuple)) or len(self.participants)<2 or
                    any(not isinstance(p,str) or not p.strip() for p in self.participants) or
                    len(set(self.participants)) != len(self.participants) or 'Me' not in self.participants):
                raise AuthoringError('participants must be distinct source contacts including Me')
            object.__setattr__(self,'participants',tuple(self.participants))
        if self.chat_name and (not isinstance(self.chat_name,str) or len(self.participants)<3):
            raise AuthoringError('chat_name requires an explicit group roster')
        if self.email_subject and (self.channel != 'email' or not isinstance(self.email_subject, str)):
            raise AuthoringError('email_subject requires an email channel')
        if self.message_range is not None:
            bounds = self.message_range
            if (not isinstance(bounds, (tuple, list)) or len(bounds) != 2 or
                    any(type(n) is not int for n in bounds) or
                    not 2 <= bounds[0] <= bounds[1] <= 30000):
                raise AuthoringError("message_range must be two integers between 2 and 30000")
            object.__setattr__(self, "message_range", tuple(bounds))
        if self.noise not in ('low', 'medium', 'high'):
            raise AuthoringError('noise must be low, medium or high')
        if self.messages_per_person is not None:
            bounds = self.messages_per_person
            if (not self.participants or not isinstance(bounds, (tuple,list)) or len(bounds)!=2 or
                    any(type(n) is not int for n in bounds) or not 1<=bounds[0]<=bounds[1]<=800):
                raise AuthoringError('messages_per_person needs a roster and positive bounds up to 800')
            object.__setattr__(self,'messages_per_person',tuple(bounds))
        if not self.scene.strip():
            raise AuthoringError("scene needs a description")
        if (not isinstance(self.facts, (list, tuple)) or
                any(not isinstance(f, str) or not f.strip() for f in self.facts)):
            raise AuthoringError("facts must be a list of nonempty prose assertions")
        object.__setattr__(self, "facts", tuple(f.strip() for f in self.facts))
        if self.at and self.window_start and self.at != self.window_start:
            raise AuthoringError("at and window_start disagree")
        start = self.at or self.window_start or datetime.now(timezone.utc).replace(
            microsecond=0).isoformat()
        object.__setattr__(self, "window_start", start)
        object.__setattr__(self, "at", start)
        if not self.thread:
            object.__setattr__(self, "thread", self.id)
        if not self.window_end:
            end = datetime.fromisoformat(start) + timedelta(minutes=20)
            if self.timezone:
                end = end.astimezone(ZoneInfo(self.timezone))
            object.__setattr__(self, "window_end", end.isoformat(timespec="seconds"))


#: Prompt version for scene-level authoring.
SCENE_PROMPT_VERSION = "scene-v16"


def scene_prompt(plan: ScenePlan,
                 prior_lines: tuple[tuple[str, ...], ...] = ()) -> str:
    """The generator sees the scene, never the later evaluation intent."""
    required = ("\n\nThe conversation must naturally and unambiguously establish each of "
                "these facts (they are authoring constraints, not lines to copy):\n" +
                "\n".join(f"{i}. {fact}" for i, fact in enumerate(plan.facts, 1))
                if plan.facts else "")
    continuity = (
        "Earlier messages in this same thread, with their dates "
        "(context, not text to repeat):\n" +
        "\n".join((f"{row[0][:10]} {row[1]}: {row[2]}" if len(row) == 3
                   else f"{row[0]}: {row[1]}") for row in prior_lines[-12:]) +
        "\nContinue naturally. People remember the prior exchange and do not "
        "recap settled details unless conversation calls for it. Respect any "
        "gap between those dates and today's date; don't reply as if an old "
        "message just arrived.\n\n"
        if prior_lines else "")
    if plan.email_subject:
        return (f"Write a natural short email exchange. Subject: {plan.email_subject}. "
                f"Participants: {', '.join(plan.participants)}; Me is the owner's speaker label, not a spoken name. "
                f"Date: {plan.window_start[:10]}, local time {plan.window_start[11:16]}–{plan.window_end[11:16]}.\n"
                f"{continuity}Scenario: {plan.scene}{required}\n"
                + (f"Use {plan.message_range[0]}–{plan.message_range[1]} short emails. "
                   if plan.message_range else "Use a short exchange. ") +
                "The subject is header metadata; do not put a Subject: prefix inside an email body. "
                "Return only valid JSON with dialogue (a string with one Name: body line per email) and writer_note (a short string about the choices). No prose or Markdown fences outside the JSON.")
    return (
        f"Write a believable {plan.channel} conversation from this scenario. "
        "Use `Me` exactly for the user's own messages and distinct names for "
        "other people. `Me` is a speaker label, never a person's name inside message bodies; use natural pronouns there. The user must speak at least once. Do not use `User`, "
        "`I`, or another alias for the user's speaker label.\n\n"
        + (f"Chat members: {', '.join(plan.participants)}. Use only these speaker labels."+
           (' Members can be silent.\n' if not plan.messages_per_person else '\n') if plan.participants else "") +
        f"Conversation date: {plan.window_start[:10]}. Relative dates in "
        "the conversation use this date as their reference.\n"
        f"These messages will be timestamped between {plan.window_start[11:16]} "
        f"and {plan.window_end[11:16]} local time. Keep references to the "
        "current time of day consistent with that window.\n"
        f"{continuity}"
        f"Scenario: {plan.scene}{required}\n\n"
        "Continue each person's voice from their earlier messages. Start with what "
        "they want to say; small talk and unrelated remarks are optional. Allow "
        "short message bursts and different reply lengths. Do not force an opening "
        "greeting, joke or tired-after-work aside into every exchange. "
        + {'low': 'Noise: low. Use the shortest natural exchange with a little incidental chatter. ',
           'medium': 'Noise: medium. Mix the topic into ordinary daily chatter, unrelated facts and side plans. ',
           'high': 'Noise: high. Make a very chatty day, with many unrelated topics, facts and other plans. ' }[plan.noise] +
        "Write only text messages: no bracketed stage directions, photo "
        "placeholders, or imaginary attachment markers.\n\n"
        "Make commitments clear, but never turn confirmations into a recital "
        "of date, time, place, and companion. A short 'yeah that works' after "
        "a specific proposal is a real acceptance. In a continuation, people "
        "can change one detail without repeating the whole existing plan. "
        "A tentative 'should be' alone is not an acceptance.\n\n"
        "Use natural date language in casual messages. 'Friday' is enough "
        "when this conversation date makes it unambiguous; avoid month, day "
        "number, or year unless a person would actually clarify it.\n\n"
        + (f"Write between {plan.message_range[0]} and {plan.message_range[1]} messages. "
         if plan.message_range else "Choose the number of messages needed for a natural exchange. ") +
        (f"Each chat member should send {plan.messages_per_person[0]}–{plan.messages_per_person[1]} messages across this exchange. " if plan.messages_per_person else "") +
        "Write one message per line, as `Name: message` using consistent "
        "speaker names. No timestamps, headers, or narration inside the "
        "dialogue. Return only a JSON object with `dialogue` (one string "
        "containing the message lines) and `writer_note` (one or two "
        "sentences about the creative choices that made the conversation "
        "natural). The note is an explanation for the author, not a "
        "private reasoning transcript."
    )


def scene_prompt_sha() -> str:
    probe = ScenePlan(id="probe", channel="text", thread="probe",
                      scene="Two friends making weekend plans.",
                      test_intent="Recall their plan.")
    return hashlib.sha256(scene_prompt(probe).encode()).hexdigest()[:16]


def scene_spec(plan: ScenePlan) -> dict[str, Any]:
    """The full scene spec, as persisted in judgment audit records."""
    return {
        "id": plan.id,
        "channel": plan.channel,
        "thread": plan.thread,
        "scene": plan.scene,
        "test_intent": plan.test_intent,
        "facts": list(plan.facts),
        "at": plan.at,
        "window_start": plan.window_start,
        "window_end": plan.window_end,
        "timezone": plan.timezone,
        "message_range": list(plan.message_range) if plan.message_range is not None else None,
        "participants": list(plan.participants), "chat_name": plan.chat_name,
        **({'email_subject':plan.email_subject} if plan.email_subject else {}),
        "noise": plan.noise, "messages_per_person": list(plan.messages_per_person) if plan.messages_per_person is not None else None,
        "prompt_version": SCENE_PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
    }


def parse_scene_lines(text: str, plan: ScenePlan) -> list[tuple[str, str]]:
    """Parse speaker labels and require the explicit `Me` owner convention."""
    lines: list[tuple[str, str]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        head, sep, body = raw.partition(":")
        speaker = head.strip()
        if (not sep or not speaker or not body.strip() or
                speaker.lower() in {"user", "owner", "i", "myself"} or
                (speaker.lower() == "me" and speaker != "Me")):
            raise SceneRenderError(
                plan.id, text,
                f"line {lineno} has malformed or ambiguous speaker attribution: "
                f"{raw.strip()[:80]!r}")
        lines.append((speaker, body.strip()))
    if not lines:
        raise SceneRenderError(plan.id, text, "no message lines parsed")
    if not any(s == "Me" for s, _ in lines):
        raise SceneRenderError(plan.id, text, "no `Me` message establishes ownership")
    if not any(s != "Me" for s, _ in lines):
        raise SceneRenderError(plan.id, text, "no other participant in conversation")
    return lines


def spread_timestamps(plan: ScenePlan, n: int) -> list[str]:
    """Freeze reproducible uneven reply gaps within the authored scene window.

    This is synthetic pacing, not a measured human timing distribution. The
    model never authors clocks and provider reruns use the same frozen times.
    """
    try:
        start = datetime.fromisoformat(plan.window_start)
        end = datetime.fromisoformat(plan.window_end)
    except ValueError as exc:
        raise AuthoringError(
            f"scene {plan.id!r}: bad window "
            f"{plan.window_start!r}-{plan.window_end!r}") from exc
    if end <= start:
        raise AuthoringError(
            f"scene {plan.id!r}: window end {plan.window_end!r} is not after "
            f"start {plan.window_start!r}")
    if n <= 1:
        return [start.isoformat(timespec="seconds")]
    zone = ZoneInfo(plan.timezone) if plan.timezone else None
    if zone:start,end=start.astimezone(timezone.utc),end.astimezone(timezone.utc)
    weights=[(1,2,4,12,40)[hashlib.sha256(f'{plan.id}:{i}'.encode()).digest()[0]%5]
             for i in range(n-1)]
    total=sum(weights);elapsed=0;stamps=[start]
    for weight in weights:
        elapsed+=weight
        stamps.append(end if elapsed==total else start+(end-start)*(elapsed/total))
    return [(stamp.astimezone(zone) if zone else stamp).isoformat(timespec='seconds')
            for stamp in stamps]


def norm_thread(thread: str) -> str:
    """Canonical thread-id form: stripped lowercase. Anything else fails
    closed at build/freeze time (no silent case-split threads)."""
    return thread.strip().lower()


def build_scene_observations(life_id: str, plan: ScenePlan,
                             lines: list[tuple[str, str]]
                             ) -> tuple[Observation, ...]:
    """Realize frozen scene lines into observations. Deterministic and
    offline: timestamps come from :func:`spread_timestamps`, source ids from
    the scene id, ownership from the cast. Fail closed on any spec breach —
    unnormalized thread, unknown channel, unknown speaker, empty text, or a
    verbatim dupe inside the scene."""
    if plan.thread != norm_thread(plan.thread) or not plan.thread:
        raise AuthoringError(
            f"scene {plan.id!r}: thread {plan.thread!r} is not canonical "
            f"(want {norm_thread(plan.thread)!r})")
    if plan.channel not in CHANNELS:
        raise AuthoringError(
            f"scene {plan.id!r}: unknown channel {plan.channel!r}")
    seen: set[str] = set()
    for sender, text in lines:
        if plan.participants and sender not in plan.participants:
            raise AuthoringError(f'Unknown source participant {sender!r}')
        if sender != "Me" and sender.lower() in {"user", "owner", "i", "myself", "me"}:
            raise AuthoringError(f"scene {plan.id!r}: ambiguous owner label {sender!r}")
        if not text.strip():
            raise AuthoringError(f"scene {plan.id!r}: empty message text")
        key = text.strip().lower()
        if key in seen:
            raise AuthoringError(
                f"scene {plan.id!r}: verbatim dupe {text[:60]!r}")
        seen.add(key)
    stamps = spread_timestamps(plan, len(lines))
    # Author scene names can reveal cancellations or outcomes. Export only
    # opaque source identities; readable authoring labels stay in audit files.
    thread='thread-'+hashlib.sha256(f'{plan.channel}:{plan.thread}'.encode()).hexdigest()[:24]
    return tuple(
        Observation(
            source_id='message-'+hashlib.sha256(f'{life_id}/{plan.id}/{i}'.encode()).hexdigest()[:24], channel=plan.channel,
            written_at=stamp, arrived_at=stamp, collected_at=stamp,
            sender=sender, thread=thread, text=text,
            from_owner=sender == "Me", structured=(
                {'participants':list(plan.participants),'chat_name':plan.chat_name,
                 **({'email_subject':plan.email_subject} if plan.email_subject else {})} if plan.participants else None))
        for i, ((sender, text), stamp) in enumerate(zip(lines, stamps)))


# ------------------------------------------------------------------ judging --

#: Approved narrow-judge prompter (verdicts only, never ground truth).
JUDGE_PROMPT_VERSION = "scene-judge-v8"

def judge_prompt(plan: ScenePlan, lines: list[tuple[str, str]],
                 prior_lines: tuple[tuple[str, ...], ...] = ()) -> str:
    """Verify that the transcript establishes the authored scene."""
    transcript = "\n".join(f"{i + 1}. {s}: {t}"
                           for i, (s, t) in enumerate(lines))
    cross_channel=any(len(row)==4 for row in prior_lines)
    prior = (("Earlier source messages across channels" if cross_channel else "Earlier messages in this thread") + ", for interpreting references "
             "(cite only the numbered new messages below):\n" +
             "\n".join((f"{row[0]} [{row[1]}] {row[2]}: {row[3]}" if len(row)==4 else
                        f"{row[0][:10]} {row[1]}: {row[2]}" if len(row) == 3
                        else f"{row[0]}: {row[1]}") for row in prior_lines[-20:]) +
             "\n\n" if prior_lines else "")
    required = ("\n\nFor each numbered required fact, report whether the dialogue "
                "actually establishes it, with line citations. Distinguish "
                "a suggestion from a commitment and an old option from the "
                "current plan. Include `facts`, an array with one object per "
                "numbered fact: index (1-based integer), supported (boolean), "
                "evidence (array of line integers), problem (short string). "
                "Reject the scene if any fact is absent or contradicted.\n"+
                "\n".join(f"{i}. {fact}" for i, fact in enumerate(plan.facts, 1))
                if plan.facts else "")
    return (
        "You are verifying a fictional conversation, not writing it. "
        "Does the dialogue establish the scenario, including each specified "
        "person, detail, and commitment? Reject missing, contradictory, or "
        "merely tentative details when the scenario says agreed. Check that "
        "each asserted participant's commitment is clear in context; "
        "do not count mere interest, uncertainty or silence as agreement. "
        "Cite evidence of commitment without requiring a scripted confirmation order. Check that "
        "`Me` consistently represents the user. Judge meaning, not token "
        "overlap. Return only JSON with keys supported (boolean), evidence "
        "(array of numbered dialogue line integers), and problem (short "
        "explanation, empty when supported).\n\n"
        f"Conversation date: {plan.window_start[:10]}. Resolve relative "
        "dates from this fictional date.\n"
        f"The new messages will be timestamped between {plan.window_start[11:16]} "
        f"and {plan.window_end[11:16]} local time. Reject dialogue that "
        "clearly places the current exchange at a different time of day.\n"
        f"Scenario: {plan.scene}{required}\n\n{prior}New messages:\n{transcript}"
    )


def write_judgment_record(*, record_dir: str | Path, scene_id: str,
                          attempt: int, spec: dict[str, Any],
                          dialogue: list[str], judge_prompt_text: str,
                          judge_response: str, verdict: dict[str, Any] | None,
                          passed: bool, model: str,
                          prompt_chars: int, response_chars: int,
                          model_call: str | None = None,
                          model_usage: dict | None = None) -> str:
    """Persist one judge decision as audit evidence. Timestamped names, never
    overwritten: ``<scene-id>-<utc-timestamp>-<attempt>.json`` (a ``-2``,
    ``-3``, ... suffix disambiguates a same-second collision). Returns the
    record path as a string."""
    outdir = Path(record_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{scene_id}-{stamp}-{attempt}"
    path = outdir / f"{base}.json"
    suffix = 2
    while path.exists():
        path = outdir / f"{base}-{suffix}.json"
        suffix += 1
    record = {
        "scene_id": scene_id,
        "attempt": attempt,
        "passed": passed,
        "model": model,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "spec": spec,
        "dialogue": dialogue,
        "judge_prompt": judge_prompt_text,
        "judge_response": judge_response,
        "verdict": verdict,
        "prompt_chars": prompt_chars,
        "response_chars": response_chars,
        "model_call": model_call,
        "usage": model_usage,
        "cost_usd": (model_usage or {}).get("cost_usd"),
        "cost_note": ("reported by API" if (model_usage or {}).get("cost_usd") is not None
                      else "API did not report a charge"),
    }
    path.write_text(json.dumps(record, indent=2) + "\n")
    return str(path)


@dataclass
class SceneRecord:
    """Per-scene call and verification provenance."""

    scene_id: str
    status: str = "pending"  # ok
    attempts: int = 0
    judge_calls: int = 0
    prompt_chars: int = 0
    response_chars: int = 0
    prompt_sha: str = ""
    scene_sha: str = ""
    #: Audit trail: judgment-record paths, one per judge call, in order.
    judgment_paths: list[str] = field(default_factory=list)
    generation_paths: list[str] = field(default_factory=list)
    writer_notes: list[str] = field(default_factory=list)


@dataclass
class SceneRealization:
    """Frozen scene lines plus their provenance and the passing verdict."""

    scene_id: str
    lines: tuple[tuple[str, str], ...]
    record: SceneRecord
    verdict: dict = field(default_factory=dict)


def judge_scene(plan: ScenePlan, lines: list[tuple[str, str]],
                judge_generate: Generate, *, model: str = "",
                record_dir: str | Path | None = None,
                attempt: int = 1,
                prior_lines: tuple[tuple[str, ...], ...] = ()) -> tuple[dict, int, int, str | None]:
    """Run the narrow judge; return (verdict, prompt_chars, response_chars,
    judgment_path). Fail closed on an unparsable verdict or any unsupported
    fact. Every call is persisted to `record_dir` when given (audit evidence,
    pass or fail)."""
    prompt = judge_prompt(plan, lines, prior_lines)
    generated = judge_generate(prompt)
    model_call = generated.record_path if isinstance(generated, ModelOutput) else None
    model_usage = generated.usage if isinstance(generated, ModelOutput) else None
    out = (generated.text if isinstance(generated, ModelOutput) else generated).strip()
    usage = (len(prompt), len(out))
    verdict: dict[str, Any] | None = None
    problem = ""
    try:
        start, stop = out.index("{"), out.rindex("}") + 1
        verdict = json.loads(out[start:stop])
    except (ValueError, json.JSONDecodeError) as exc:
        problem = f"unparsable verdict: {out[:120]!r}"
        path = _record(plan, lines, prompt, out, None, False, model,
                       record_dir, attempt, *usage,
                       model_call=model_call, model_usage=model_usage)
        raise SceneJudgeError(plan.id, problem,
                              judgment_path=path) from exc
    try:
        if not isinstance(verdict, dict):
            raise TypeError("judge verdict must be a JSON object")
        evidence = verdict["evidence"]
        ok = (isinstance(verdict["supported"], bool)
              and verdict["supported"] is True
              and isinstance(evidence, list) and bool(evidence)
              and all(type(i) is int and 1 <= i <= len(lines) for i in evidence)
              and isinstance(verdict["problem"], str))
        if plan.facts:
            facts = verdict.get("facts")
            ok = ok and isinstance(facts, list) and len(facts) == len(plan.facts)
            if ok:
                ok = all(
                    isinstance(row, dict) and row.get("index") == i and
                    row.get("supported") is True and
                    isinstance(row.get("evidence"), list) and row["evidence"] and
                    all(type(n) is int and 1 <= n <= len(lines)
                        for n in row["evidence"]) and
                    isinstance(row.get("problem"), str)
                    for i, row in enumerate(facts, 1))
    except (AttributeError, TypeError, KeyError) as exc:
        problem = f"malformed verdict shape: {out[:120]!r}"
        path = _record(plan, lines, prompt, out, verdict, False, model,
                       record_dir, attempt, *usage,
                       model_call=model_call, model_usage=model_usage)
        raise SceneJudgeError(plan.id, problem, judgment_path=path) from exc
    if not ok:
        problem = str(verdict.get("problem") or verdict)[:300]
        path = _record(plan, lines, prompt, out, verdict, False, model,
                       record_dir, attempt, *usage,
                       model_call=model_call, model_usage=model_usage)
        raise SceneJudgeError(plan.id, problem, judgment_path=path)
    path = _record(plan, lines, prompt, out, verdict, True, model,
                   record_dir, attempt, *usage,
                   model_call=model_call, model_usage=model_usage)
    return verdict, *usage, path


def _record(plan: ScenePlan, lines: list[tuple[str, str]], prompt: str,
            out: str, verdict: dict[str, Any] | None, passed: bool,
            model: str, record_dir: str | Path | None, attempt: int,
            prompt_chars: int, response_chars: int,
            model_call: str | None = None,
            model_usage: dict | None = None) -> str | None:
    if record_dir is None:
        return None
    return write_judgment_record(
        record_dir=record_dir, scene_id=plan.id, attempt=attempt,
        spec=scene_spec(plan),
        dialogue=[f"{s}: {t}" for s, t in lines],
        judge_prompt_text=prompt, judge_response=out, verdict=verdict,
        passed=passed, model=model,
        prompt_chars=prompt_chars, response_chars=response_chars,
        model_call=model_call, model_usage=model_usage)


def realize_scene(plan: ScenePlan, generate: Generate,
                  judge_generate: Generate, *, model: str = "",
                  record_dir: str | Path | None = None,
                  prior_lines: tuple[tuple[str, ...], ...] = (),
                  verification_lines: tuple[tuple[str, ...], ...] | None = None) -> SceneRealization:
    """Render and verify a scene, with at most one revision after rejection."""
    if plan.message_range and plan.message_range[1] > 150:
        # Bounded authoring calls, with the same conversation carried forward.
        # Never duplicate, truncate or pad messages to reach a noise target.
        target = sum(plan.message_range) // 2
        count = (target + 119) // 120
        size = target // count
        lines, records = [], []
        start, end = datetime.fromisoformat(plan.window_start), datetime.fromisoformat(plan.window_end)
        for index in range(count):
            chunk_start = start + (end-start) * index / count
            chunk_end = start + (end-start) * (index+1) / count
            quota = (f'Continue the chat. Remaining messages per member toward the daily target: '
                     f'{ {p:max(0,target//len(plan.participants)-sum(s==p for s,_ in lines)) for p in plan.participants} }.'
                     if plan.participants else 'Continue the chat.')
            chunk = replace(plan, id=f'{plan.id}-part-{index+1}',
                scene=plan.scene if index==0 else f'{plan.scene}\n{quota} The required discussion already happened earlier; continue unrelated daily topics without revising settled plans.',
                facts=plan.facts if index==0 else (), messages_per_person=None,
                at=chunk_start.isoformat(), window_start=chunk_start.isoformat(), window_end=chunk_end.isoformat(),
                message_range=(max(2,size-5),size+5))
            recent = prior_lines + tuple((s,t) for s,t in lines[-20:])
            result = realize_scene(chunk,generate,judge_generate,model=model,record_dir=record_dir,prior_lines=recent,
                                   verification_lines=verification_lines)
            lines.extend(result.lines); records.append(result.record)
        if not plan.message_range[0]<=len(lines)<=plan.message_range[1]:
            raise AuthoringError('Chunked conversation missed its message target; no padding permitted')
        if plan.messages_per_person and any(not plan.messages_per_person[0]<=sum(s==p for s,_ in lines)<=plan.messages_per_person[1] for p in plan.participants):
            raise AuthoringError('Chunked conversation missed a member target; no padding permitted')
        verdict,pc,rc,jpath=judge_scene(plan,lines,judge_generate,model=model,record_dir=record_dir,
            prior_lines=verification_lines if verification_lines is not None else prior_lines)
        record=SceneRecord(scene_id=plan.id,status='ok',attempts=sum(r.attempts for r in records),
            judge_calls=1+sum(r.judge_calls for r in records),
            prompt_chars=pc+sum(r.prompt_chars for r in records),response_chars=rc+sum(r.response_chars for r in records),
            judgment_paths=[p for r in records for p in r.judgment_paths]+([jpath] if jpath else []),
            generation_paths=[p for r in records for p in r.generation_paths],
            writer_notes=[n for r in records for n in r.writer_notes])
        return SceneRealization(plan.id,tuple(lines),record,verdict)
    if plan.channel not in CHANNELS or not plan.thread or plan.thread != norm_thread(plan.thread):
        raise AuthoringError(f"scene {plan.id!r}: invalid channel or thread metadata")
    spread_timestamps(plan, 2)
    record = SceneRecord(scene_id=plan.id)
    initial_prompt = scene_prompt(plan, prior_lines)
    note = ""
    failure = ""
    previous_draft = ""
    previous_result: ModelOutput | None = None
    retry_feedback = ""
    for attempt in (1, 2):
        record.attempts = attempt
        prompt = initial_prompt + note
        if record.attempts > 1 and previous_result is not None and hasattr(generate, "continue_from"):
            record.prompt_chars += len(initial_prompt) + len(retry_feedback)
            generated = generate.continue_from(previous_result, initial_prompt, retry_feedback)
        elif record.attempts > 1 and hasattr(generate, "complete"):
            messages = [
                {"role": "user", "content": initial_prompt},
                {"role": "assistant", "content": previous_draft},
                {"role": "user", "content": retry_feedback}]
            record.prompt_sha = hashlib.sha256(json.dumps(messages).encode()).hexdigest()[:16]
            record.prompt_chars += sum(len(m["content"]) for m in messages)
            generated = generate.complete(messages)
        else:
            record.prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()[:16]
            record.prompt_chars += len(prompt)
            generated = generate(prompt)
        out = (generated.text if isinstance(generated, ModelOutput) else generated).strip()
        if isinstance(generated, ModelOutput):
            record.generation_paths.append(generated.record_path)
            previous_result = generated
            if generated.request_sha:
                record.prompt_sha = generated.request_sha[:16]
        record.response_chars += len(out)
        try:
            dialogue = out
            writer_note = ""
            if out.startswith("{"):
                rendered = json.loads(out)
                if not isinstance(rendered, dict) or not isinstance(rendered.get("dialogue"), str):
                    raise ValueError("render must contain dialogue text")
                if not isinstance(rendered.get("writer_note"), str):
                    raise ValueError("render must contain a writer note")
                dialogue = rendered["dialogue"]
                writer_note = rendered["writer_note"].strip()
                if not writer_note:
                    raise ValueError("render writer note is empty")
            lines = parse_scene_lines(dialogue, plan)
            if plan.message_range and not plan.message_range[0] <= len(lines) <= plan.message_range[1]:
                raise ValueError(f"message count {len(lines)} outside {plan.message_range}")
            if plan.messages_per_person and any(not plan.messages_per_person[0]<=sum(s==p for s,_ in lines)<=plan.messages_per_person[1] for p in plan.participants):
                raise ValueError('Conversation missed a member message target')
            record.writer_notes.append(writer_note)
        except (SceneRenderError, ValueError) as exc:
            failure = f"unparsable render: {exc.problem if isinstance(exc, SceneRenderError) else exc}"
        else:
            try:
                build_scene_observations("scene-build", plan, lines)
            except AuthoringError as exc:
                failure = str(exc)
            else:
                try:
                    verdict, pc, rc, jpath = judge_scene(
                        plan, lines, judge_generate, model=model,
                        record_dir=record_dir, attempt=record.attempts,
                        prior_lines=verification_lines if verification_lines is not None else prior_lines)
                except SceneJudgeError as exc:
                    record.judge_calls += 1
                    if exc.judgment_path is not None:
                        record.judgment_paths.append(exc.judgment_path)
                    failure = str(exc.problem)[:300]
                else:
                    record.judge_calls += 1
                    record.prompt_chars += pc
                    record.response_chars += rc
                    if jpath is not None:
                        record.judgment_paths.append(jpath)
                    record.scene_sha = hashlib.sha256(
                        "\n".join(f"{s}: {t}" for s, t in lines)
                        .encode()).hexdigest()[:16]
                    record.status = "ok"
                    return SceneRealization(
                        scene_id=plan.id, lines=tuple(lines),
                        record=record, verdict=verdict)
        if attempt == 1:
            previous_draft = out
            retry_feedback = (f"The verifier rejected this draft: {failure}. "
                              "Revise the same conversation, preserving good "
                              "lines and fixing the failure.")
            note = (f"\n\nYour previous draft was:\n{out}\n\n"
                    f"{retry_feedback}")
    raise AuthoringError(
        f"scene {plan.id!r}: failed after re-render: {failure}")
