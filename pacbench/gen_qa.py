"""Generation QA screens for Spark-rendered lives (criterion 1 proof points).

Deterministic, offline, provider-blind. Three screens:

- ``added_atoms`` — semantic fact-realization QA lite: the atoms
  (weekday/month/time/money/address) in a rendering that the draft did
  not contain. Spark must do surface language, never invent facts; any
  hit is an extra-fact finding for human review. Entity-level invention
  (new names/places) is NOT reliably detectable deterministically and
  stays with the human ambient review + review packet.
- ``screen_life`` — filler discipline: non-support observations must
  carry no temporal tokens, commitments, slot-forbidden words, or
  negate cues, so filler can neither answer a checkpoint nor trip the
  contradiction scan by accident.
- ``noise_report`` — the measured support ratio (§Q6: large lives carry
  2–10% direct scored support, never a quiz sheet, never a trivia
  swamp), computed from the frozen render, never requested.
"""

from __future__ import annotations

import re
from typing import Any

def _word_re(words: tuple[str, ...], plural: bool = False) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(words) + r")" + ("s?" if plural else "") + r"\b")

_FULL_DAY_RE = _word_re(
    ("monday", "tuesday", "wednesday", "thursday", "friday",
     "saturday", "sunday"), plural=True)
_ABBR_DAY_RE = re.compile(
    r"(?<![A-Za-z0-9_'\u2019])"
    r"(?:mon|tue|wed|thu|fri|sat|sun)\b")
_FULL_MONTH_RE = _word_re(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"), plural=True)
_ABBR_MONTH_RE = re.compile(
    r"(?<![A-Za-z0-9_'\u2019])"
    r"(?:jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b")
RELATIVE_DAYS = ("tomorrow", "tonight", "today", "yesterday")
COMMITMENTS = ("appointment", "meeting", "dinner", "party", "brunch",
               "rsvp", "deadline", "booked", "scheduled", "reserved",
               "reminder", "birthday", "wedding", "flight", "hotel",
               "dentist", "doctor", "race", "marathon")
#: Pure-polarity negate cues (grade._NEGATE minus topic words): a filler
#: "no" would match a YESNO forbidden and pollute the scan per-obs.
NEGATE_CUES = (r"\bno\b", "not going", "you are not", "won t")

_TIME_RES = (
    re.compile(r"\b\d{1,2}:\d{2}\b"),
    re.compile(r"\b\d{1,2}\s?(am|pm)\b"),
    re.compile(r"\$\d+"),
    re.compile(r"\b\d+\s?(st|nd|rd|th)\b"),
)
_ADDRESS_RE = re.compile(
    r"\b\d+\s+[A-Z][a-z]+\s+(St|Street|Ave|Avenue|Rd|Road|Blvd|Ln|Lane|Dr|Drive)\b")


def fact_atoms(text: str) -> set[str]:
    """Temporal/named atoms in text: the facts a rendering must not add."""
    low = text.lower()
    atoms = set()
    for pattern in (_FULL_DAY_RE, _ABBR_DAY_RE, _FULL_MONTH_RE,
                    _ABBR_MONTH_RE):
        atoms.update(m.group(0) for m in pattern.finditer(low))
    for word in RELATIVE_DAYS:
        if word in low:
            atoms.add(word)
    for pattern in _TIME_RES:
        atoms.update(m.group(0) for m in pattern.finditer(low))
    atoms.update(m.group(0) for m in _ADDRESS_RE.finditer(text))
    return atoms


def added_atoms(rendered: str, draft: str) -> set[str]:
    """Atoms in the rendering the draft did not contain: extra-fact hits."""
    return fact_atoms(rendered) - fact_atoms(draft)


def screen_filler_text(text: str, extra_forbidden: tuple[str, ...] = ()) -> list[str]:
    """Filler-discipline violations in one message. Empty == clean hay."""
    low = text.lower()
    hits: list[str] = []
    for pattern, tag in ((_FULL_DAY_RE, "weekday"), (_ABBR_DAY_RE, "weekday"),
                         (_FULL_MONTH_RE, "month"), (_ABBR_MONTH_RE, "month")):
        hits.extend(f"{tag}:{m.group(0)}" for m in pattern.finditer(low))
    for word in (*RELATIVE_DAYS, *COMMITMENTS, *extra_forbidden):
        if word in low:
            hits.append(f"word:{word}")
    if re.search(r"\d", text):
        hits.append("digits")
    for cue in NEGATE_CUES:
        if re.search(cue, low):
            hits.append(f"negate:{cue}")
    return hits


def screen_life(life: Any, manifest: Any,
                extra_forbidden: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Map source_id -> violations for every non-support observation."""
    supported: set[str] = set()
    for entry in manifest.entries:
        supported.update(entry.observation_ids)
    out: dict[str, list[str]] = {}
    for obs in life.observations:
        if obs.source_id in supported:
            continue
        violations = screen_filler_text(obs.text, extra_forbidden)
        if violations:
            out[obs.source_id] = violations
    return out


def noise_report(life: Any, manifest: Any) -> dict:
    """Measured support ratio from the frozen render (Q6 acceptance)."""
    supported: set[str] = set()
    per_fact: dict[str, int] = {}
    for entry in manifest.entries:
        per_fact[entry.fact_id] = len(entry.observation_ids)
        supported.update(entry.observation_ids)
    total = len(life.observations)
    ratio = len(supported) / total if total else 0.0
    return {"total": total, "supported": len(supported),
            "ratio": round(ratio, 4),
            "in_acceptance_band": 0.02 <= ratio <= 0.10,
            "per_fact": per_fact}
