"""Style instrumentation for human review (plan.md P0-14).

Offline, deterministic, provider-blind: compares scored-support messages
with ambient messages over surface properties ONLY — message length,
sentence count, explicit date/time frequency, proper-name repetition,
event-name repetition, punctuation/formality signals — with
channel-specific splits.

Output is distributions + side-by-side contrasts (dict/JSON-serializable).
There is no score or threshold; this is optional material for human review.

Provider-blind: reads only ``Life`` observations (channel/sender/text)
plus a support-id set. Never touches adapters, providers, or grading.

Nothing in this module gates, blocks, or scores a run.
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from typing import Any, Iterable

# Explicit date/time surface signals for optional style inspection.
_WEEKDAY_RE = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b",
    re.IGNORECASE,
)
_MONTH_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|"
    r"october|november|december)s?\b",
    re.IGNORECASE,
)
_RELATIVE_RE = re.compile(r"\b(?:today|tonight|tomorrow|yesterday)\b", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
_MERIDIEM_RE = re.compile(r"\b\d{1,2}\s?(?:am|pm)\b", re.IGNORECASE)
_MONEY_RE = re.compile(r"\$\d+")
_ORDINAL_RE = re.compile(r"\b\d+\s?(?:st|nd|rd|th)\b", re.IGNORECASE)
_DATE_NUM_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")

_DATETIME_RES = (
    _WEEKDAY_RE, _MONTH_RE, _RELATIVE_RE, _CLOCK_RE,
    _MERIDIEM_RE, _MONEY_RE, _ORDINAL_RE, _DATE_NUM_RE,
)

# Surface proper-name / event-name signals: capitalized tokens and
# multi-word capitalized runs. Heuristic and deliberately shallow — this
# is review material, not entity resolution.
_NAME_TOKEN_RE = re.compile(r"\b[A-Z][a-z]{1,}(?:'[a-z]+)?\b")
_EVENT_PHRASE_RE = re.compile(r"\b(?:[A-Z][a-z]{1,}(?:'[a-z]+)?\s+){1,}[A-Z][a-z]{1,}\b")

_SENTENCE_SPLIT_RE = re.compile(r"[.!?]+(?:\s+|$)")

# Common sentence-initial capitalized words that are not names; excluded
# from the name signal so plain formal prose does not read as name-dense.
_NON_NAME_STARTERS = frozenset({
    "The", "This", "That", "These", "Those", "Your", "You", "Thanks",
    "Please", "Just", "See", "Confirmed", "Reminder", "Also", "Still",
    "Never", "Always", "Hey", "Hi", "Hello", "Dear", "For", "With",
    "From", "When", "Where", "What", "How", "Are", "Can", "Will",
    "Would", "Should", "Could", "There", "Here", "Lets", "Let",
})


def _has_datetime(text: str) -> tuple[bool, int]:
    """(has_any, hit_count) for explicit date/time surface tokens."""
    hits = sum(len(p.findall(text)) for p in _DATETIME_RES)
    return (hits > 0, hits)


def _name_tokens(text: str) -> list[str]:
    toks = _NAME_TOKEN_RE.findall(text)
    return [t for t in toks if t not in _NON_NAME_STARTERS]


def _event_phrases(text: str) -> list[str]:
    return _EVENT_PHRASE_RE.findall(text)


def message_features(text: str) -> dict[str, Any]:
    """Surface properties of one message. No semantics, no judgment."""
    words = text.split()
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
    has_dt, dt_hits = _has_datetime(text)
    names = _name_tokens(text)
    name_counts = Counter(names)
    events = _event_phrases(text)
    event_counts = Counter(e.strip() for e in events)
    excl = text.count("!")
    ques = text.count("?")
    commas = text.count(",")
    alpha = [c for c in text if c.isalpha()]
    upper_ratio = (
        sum(1 for c in alpha if c.isupper()) / len(alpha) if alpha else 0.0
    )
    return {
        "chars": len(text),
        "words": len(words),
        "sentences": max(len(sentences), 1 if text.strip() else 0),
        "has_datetime": has_dt,
        "datetime_hits": dt_hits,
        "name_tokens": len(names),
        "repeated_name_tokens": sum(1 for t, c in name_counts.items() if c >= 2),
        "event_phrases": len(events),
        "repeated_event_phrases": sum(1 for e, c in event_counts.items() if c >= 2),
        "has_exclamation": excl > 0,
        "exclamations": excl,
        "has_question": ques > 0,
        "questions": ques,
        "commas": commas,
        "upper_ratio": round(upper_ratio, 4),
    }


def _dist(values: list[float | int]) -> dict[str, Any]:
    """JSON-serializable distribution summary + sorted values."""
    if not values:
        return {"n": 0, "mean": None, "median": None, "min": None,
                "max": None, "values": []}
    floats = [float(v) for v in values]
    return {
        "n": len(values),
        "mean": round(statistics.fmean(floats), 3),
        "median": round(statistics.median(floats), 3),
        "min": min(floats),
        "max": max(floats),
        "values": sorted(floats),
    }


def _frac(values: list[bool]) -> dict[str, Any]:
    n = len(values)
    k = sum(1 for v in values if v)
    return {"n": n, "k": k, "fraction": round(k / n, 4) if n else None}


def _summarize(feats: list[dict[str, Any]],
               texts: list[str]) -> dict[str, Any]:
    """Distribution bundle for one message group."""
    top_names = Counter(
        t for text in texts for t in set(_name_tokens(text))
    ).most_common(10)
    top_events = Counter(
        e.strip() for text in texts for e in set(_event_phrases(text))
    ).most_common(10)
    return {
        "n_messages": len(feats),
        "chars": _dist([f["chars"] for f in feats]),
        "words": _dist([f["words"] for f in feats]),
        "sentences": _dist([f["sentences"] for f in feats]),
        "has_datetime": _frac([f["has_datetime"] for f in feats]),
        "datetime_hits_per_msg": _dist([f["datetime_hits"] for f in feats]),
        "name_tokens_per_msg": _dist([f["name_tokens"] for f in feats]),
        "msgs_with_repeated_name": _frac(
            [f["repeated_name_tokens"] > 0 for f in feats]),
        "event_phrases_per_msg": _dist([f["event_phrases"] for f in feats]),
        "msgs_with_repeated_event": _frac(
            [f["repeated_event_phrases"] > 0 for f in feats]),
        "msgs_with_exclamation": _frac([f["has_exclamation"] for f in feats]),
        "msgs_with_question": _frac([f["has_question"] for f in feats]),
        "commas_per_msg": _dist([f["commas"] for f in feats]),
        "exclamations_per_msg": _dist([f["exclamations"] for f in feats]),
        "questions_per_msg": _dist([f["questions"] for f in feats]),
        "upper_ratio": _dist([f["upper_ratio"] for f in feats]),
        "top_names": [[name, count] for name, count in top_names],
        "top_event_phrases": [[ev, count] for ev, count in top_events],
    }


def _diff(a: float | int | None, b: float | int | None) -> float | None:
    if a is None or b is None:
        return None
    return round(float(a) - float(b), 4)


def _contrast(support: dict[str, Any],
              ambient: dict[str, Any]) -> dict[str, Any]:
    """Side-by-side support-vs-ambient contrasts (differences, not scores)."""
    out: dict[str, Any] = {}
    for key in ("chars", "words", "sentences", "datetime_hits_per_msg",
                "name_tokens_per_msg", "event_phrases_per_msg",
                "commas_per_msg", "exclamations_per_msg",
                "questions_per_msg", "upper_ratio"):
        out[key] = {
            "support_mean": support[key]["mean"],
            "ambient_mean": ambient[key]["mean"],
            "difference": _diff(support[key]["mean"], ambient[key]["mean"]),
            "support_median": support[key]["median"],
            "ambient_median": ambient[key]["median"],
        }
    for key in ("has_datetime", "msgs_with_repeated_name",
                "msgs_with_repeated_event", "msgs_with_exclamation",
                "msgs_with_question"):
        out[key] = {
            "support_fraction": support[key]["fraction"],
            "ambient_fraction": ambient[key]["fraction"],
            "difference": _diff(support[key]["fraction"],
                                ambient[key]["fraction"]),
        }
    out["n_messages"] = {
        "support": support["n_messages"],
        "ambient": ambient["n_messages"],
    }
    return out


def style_report(life: Any, support_ids: Iterable[str]) -> dict[str, Any]:
    """Support-vs-ambient surface-style distributions for one life.

    ``support_ids`` are the scored-support observation ids (e.g. the union
    of :func:`pacbench.difficulty.required_support_ids` over the life's
    checkpoints, or ids from a support manifest). Every other observation
    is ambient. Unknown ids are ignored.
    """
    wanted = set(support_ids)
    support_texts: list[str] = []
    ambient_texts: list[str] = []
    support_by_channel: dict[str, list[str]] = {}
    ambient_by_channel: dict[str, list[str]] = {}
    for obs in life.observations:
        channel = getattr(obs, "channel", "unknown")
        text = getattr(obs, "text", "")
        if obs.source_id in wanted:
            support_texts.append(text)
            support_by_channel.setdefault(channel, []).append(text)
        else:
            ambient_texts.append(text)
            ambient_by_channel.setdefault(channel, []).append(text)
    support = _summarize([message_features(t) for t in support_texts],
                         support_texts)
    ambient = _summarize([message_features(t) for t in ambient_texts],
                         ambient_texts)
    by_channel: dict[str, Any] = {}
    for channel in sorted(set(support_by_channel) | set(ambient_by_channel)):
        s = _summarize(
            [message_features(t) for t in support_by_channel.get(channel, [])],
            support_by_channel.get(channel, []),
        )
        a = _summarize(
            [message_features(t) for t in ambient_by_channel.get(channel, [])],
            ambient_by_channel.get(channel, []),
        )
        by_channel[channel] = {
            "support": s,
            "ambient": a,
            "contrast": _contrast(s, a),
        }
    return {
        "life_id": getattr(life, "id", "unknown"),
        "n_observations": len(list(life.observations)),
        "n_support": len(support_texts),
        "n_ambient": len(ambient_texts),
        "support": support,
        "ambient": ambient,
        "contrast": _contrast(support, ambient),
        "by_channel": by_channel,
        "notes": (
            "Offline provider-blind surface-style instrument for human "
            "review (P0-14). Distributions and side-by-side contrasts only; "
            "this instrument offers no automated judgment and blocks no "
            "run. The human review packet remains authoritative."
        ),
    }


def style_report_for_life(life: Any) -> dict[str, Any]:
    """Report with scored support = union of rubric-required ids.

    Uses :func:`pacbench.difficulty.required_support_ids` over every
    checkpoint, i.e. the observations grading actually depends on.
    """
    from .difficulty import required_support_ids

    ids: set[str] = set()
    for checkpoint in life.checkpoints:
        ids.update(required_support_ids(checkpoint))
    return style_report(life, ids)


def support_ids_from_manifest(manifest: Any) -> set[str]:
    """Support ids from an authoring support manifest (review use only)."""
    ids: set[str] = set()
    for entry in manifest.entries:
        ids.update(entry.observation_ids)
    return ids


def report_to_json(report: dict[str, Any]) -> str:
    """Serialize a report (proves JSON-serializability)."""
    return json.dumps(report, indent=2, sort_keys=True)
