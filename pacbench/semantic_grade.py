"""Model-only semantic verdicts for a natural-language checkpoint contract.

The model sees the question, answer, and each independently testable claim.
Python validates the response shape and aggregates verdicts, but never matches
answer text or chooses a semantic verdict. A failed judge call cannot pass.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .model_calls import ModelOutput

Generate = Callable[[str], str]


@dataclass(frozen=True)
class Claim:
    id: str
    description: str
    evidence: tuple[str, ...] = ()


@dataclass
class GradedClaim:
    id: str
    verdict: str
    reason: str


@dataclass
class Grade:
    checkpoint: str
    slots: list[GradedClaim] = field(default_factory=list)
    correct: int = 0
    missed: int = 0
    misrepresented: int = 0
    claim_coverage: float = 0.0
    all_claims_correct: bool = False
    recovery: list[dict] | None = None
    interaction: str = 'answer'
    evidence_diagnostic: dict | None = None


class BenchmarkContractError(ValueError):
    """The script lacks information needed to judge a legitimate response."""


VERDICTS = {"correct", "missed", "misrepresented"}


LEGACY_DIAGNOSTIC_VERSION = "answer-visible-evidence-v1"
DIAGNOSTIC_VERSION = "answer-visible-evidence-v2"
DIAGNOSTIC_VERSIONS = {LEGACY_DIAGNOSTIC_VERSION, DIAGNOSTIC_VERSION}


def diagnostic_prompt(inputs, version=LEGACY_DIAGNOSTIC_VERSION):
    return (
        'Separately assess evidence, without changing claim verdicts or recovery. '
        'Return "evidence_diagnostic" with "claims" in claim order and "extra_assertions". '
        'Each claim has id, status (supported, contradicted, not_found, uncertain), reason, '
        'and citations [{id, quote}] from the answer-visible material only. '
        'Supported or contradicted requires exact nonempty quotes; not_found has no citations. '
        'This assesses what was available, not whether the assistant used or ignored it. '
        'The material may be incomplete or truncated; use uncertain where warranted. '
        'For each additional factual assertion actually made in the answer outside the expected claims, '
        'return {assertion, answer_quote, status, reason, citations}; status is supported, '
        'contradicted, or unverifiable, and citations quote only the supplied source evidence. '
        'The source evidence covers only cited expected-claim sources and is partial; '
        'absence is unverifiable, never contradicted. Contradicted requires direct contrary source evidence. '
        'Do not add requirements for facts the user did not ask for or penalize omitted assertions. '
        'Citation quote and answer_quote must be exact substrings of their material. '
        'Return empty extra_assertions if none.\n'
        + ('The claim evidence diagnostic covers only provider evidence returned to Hermes: captured prefetch and tool results. The user question, tool arguments, and prior public conversation are outside this evidence scope; do not count them as retrieval evidence. not_found means only that the detail was not found in the supplied provider material; it does not mean the assistant could not know the detail or that a write was lost. Use a short exact plain-text quote that directly shows the fact. Omit citation markers, brackets and formatting from quotes; never normalize or rewrite characters. Copy the smallest contiguous span from the material.\n' if version == DIAGNOSTIC_VERSION else '')
        + f'Diagnostic inputs: {json.dumps(inputs, ensure_ascii=False)}\n'
    )


def validate_diagnostic(value, claims, answer, inputs, version=LEGACY_DIAGNOSTIC_VERSION):
    """Validate provenance; an unusable diagnostic never erases a valid answer grade."""
    def citations_valid(citations, material, require):
        return (isinstance(citations, list) and (not require or bool(citations)) and
            all(isinstance(c, dict) and isinstance(c.get('id'), str) and
                isinstance(c.get('quote'), str) and bool(c['quote']) and
                c['id'] in material and c['quote'] in material[c['id']] for c in citations))
    try:
        visible = {x['id']: x['text'] for x in inputs['answer_visible']}
        sources = {x['id']: x['text'] for x in inputs['source_evidence']}
        rows, extras = value['claims'], value['extra_assertions']
        if not isinstance(rows, list) or len(rows) != len(claims) or not isinstance(extras, list):
            raise ValueError()
        for row, claim in zip(rows, claims):
            status = row['status']
            if version == DIAGNOSTIC_VERSION and not citations_valid(row['citations'], visible, status in ('supported', 'contradicted')):
                return {'version': version, 'state': 'ungraded', 'reason': f'Invalid evidence citation for claim {claim.id}'}
            if (row['id'] != claim.id or status not in ('supported', 'contradicted', 'not_found', 'uncertain') or
                not isinstance(row['reason'], str) or not row['reason'].strip() or
                not citations_valid(row['citations'], visible, status in ('supported', 'contradicted')) or
                status == 'not_found' and row['citations']):
                raise ValueError()
        for row in extras:
            status = row['status']
            if version == DIAGNOSTIC_VERSION and not citations_valid(row['citations'], sources, status in ('supported', 'contradicted')):
                return {'version': version, 'state': 'ungraded', 'reason': 'Invalid evidence citation for an extra assertion'}
            if (status not in ('supported', 'contradicted', 'unverifiable') or
                not isinstance(row['assertion'], str) or not row['assertion'].strip() or
                not isinstance(row['answer_quote'], str) or not row['answer_quote'] or row['answer_quote'] not in answer or
                not isinstance(row['reason'], str) or not row['reason'].strip() or
                not citations_valid(row['citations'], sources, status in ('supported', 'contradicted'))):
                raise ValueError()
        return {'version': version, 'state': 'complete', 'claims': rows, 'extra_assertions': extras}
    except (KeyError, TypeError, ValueError):
        return {'version': version, 'state': 'ungraded', 'reason': 'Malformed or unsupported diagnostic evidence'}


def judge_prompt(question: str, answer: str, claims: tuple[Claim, ...], *, conversation=(), assess_recovery=False, assess_interaction=False, diagnostic_inputs=None, diagnostic_version=LEGACY_DIAGNOSTIC_VERSION) -> str:
    spec = [{"id": c.id, "claim": c.description,
             "evidence": list(c.evidence)} for c in claims]
    return (
        "You are a semantic memory-benchmark judge. Evaluate each claim "
        "independently against the assistant's answer to the user's question. "
        "Use meaning and context, not string matching or a demand for "
        "verbatim wording. Grade only the details stated in each claim. "
        "Equivalent descriptions satisfy the same fact; noon pizza need not also be labelled lunch. "
        "A claim's event anchors identify the outing; grade its asserted attribute independently. "
        "Do not fail a correct participant or venue again just because another time/detail is wrong; "
        "do not award a correct value attached to a different outing. "
        "Resolve the answer's inherited event and day scope from the question, "
        "simulated clock and prior conversation; do not require repeating that scope. "
        "A correct fragment does not satisfy a claim if the same answer contradicts it. "
        "Distinguish current-state claims from historical claims; judge "
        "the state and time described by each claim. "
        "Clearly labeled history is fine. Offering old and new values as unresolved "
        "current alternatives misrepresents that attribute. For complete-list claims, "
        "extra superseded or cancelled items make the current list incorrect. "
        "If absent or explicitly "
        "unknown, mark missed; if contradicted or a wrong value is asserted "
        "for that fact, mark misrepresented; if correctly conveyed, mark "
        "correct. Do not infer facts from the question or claim alone. "
        "Return ONLY one JSON object with 'verdicts', an array in the same "
        "order, each containing id, verdict (correct/missed/misrepresented), "
        "and a short reason grounded in the answer.\n\n"
        + ('Also return "recovery", an array in claim order, each with id, kind and reason. '
           'kind is provided, incorrect, explicitly_requested, unavailable, or implicit_omission. '
           'Correct facts are provided; wrong facts are incorrect. For missed facts: if the answer '
           'says it cannot find or does not know the detail, use unavailable; otherwise if the user '
           'specifically asked for that detail, use explicitly_requested; otherwise use implicit_omission. '
           'A broad request for a recap or details is not an explicit request for each detail.\n' if assess_recovery else '')
        + ('Also return "interaction": "answer" or "clarification", and "benchmark_error": null. '
           'Clarification means asking for information needed to satisfy this request or its expected behavior. '
           'Optional offers of other actions are answers, not clarification. '
           'For factual recall, asking a clarification is not a separate style failure; '
           'grade whether the expected facts were conveyed. '
           'Judge behavioral criteria only against the authored expectation and what the answer actually does. '
           'For a negative behavior criterion such as not claiming an outing is cancelled, absence of that claim satisfies it. '
           'A cancellation acknowledgement proves only the acknowledgement. '
           'If a legitimate clarification cannot be judged because the script omits necessary ground truth, '
           'set benchmark_error to a short explanation; never excuse incorrect or missing recall this way.\n' if assess_interaction else '')
        + ("Use the prior conversation only to resolve references in the latest answer. "
         "Do not award a fact merely because it appeared in an earlier answer.\n"
         f"Prior public conversation: {json.dumps(conversation, ensure_ascii=False)}\n" if conversation else "")
        + (diagnostic_prompt(diagnostic_inputs, diagnostic_version) if diagnostic_inputs is not None else "") +
        f"Question: {question}\nAnswer: {answer}\n"
        f"Claims (hidden from the assistant): {json.dumps(spec, ensure_ascii=False)}"
    )


def grade_semantic(checkpoint_id: str, question: str, answer: str,
                   claims: tuple[Claim, ...], generate: Generate,
                   *, record_path: Path | None = None, conversation=(), assess_recovery=False, assess_interaction=False, diagnostic_inputs=None, diagnostic_version=LEGACY_DIAGNOSTIC_VERSION) -> Grade:
    """Judge every claim with a model; reject missing/malformed verdicts."""
    if not claims or len({c.id for c in claims}) != len(claims):
        raise ValueError("claims must be nonempty with unique IDs")
    if diagnostic_version not in DIAGNOSTIC_VERSIONS:
        raise ValueError('Unsupported evidence diagnostic protocol')
    prompt = judge_prompt(question, answer, claims, conversation=conversation, assess_recovery=assess_recovery,assess_interaction=assess_interaction,diagnostic_inputs=diagnostic_inputs,diagnostic_version=diagnostic_version)
    response = generate(prompt)
    output = response.text if isinstance(response, ModelOutput) else response
    if record_path is not None:
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps({
            "checkpoint": checkpoint_id, "question": question,
            "answer": answer, "claims": [vars(c) for c in claims],
            "prompt": prompt, "response": output,
            "conversation": list(conversation),
            **({"diagnostic_protocol": diagnostic_version, "diagnostic_inputs": diagnostic_inputs} if diagnostic_inputs is not None else {}),
            "model_call": (response.record_path if isinstance(response, ModelOutput)
                           else None)}, indent=2, ensure_ascii=False) + "\n")
    try:
        parsed = json.loads(output)
        rows = parsed["verdicts"]
        valid = (isinstance(rows, list) and len(rows) == len(claims) and
                 all(isinstance(r, dict) and r.get("id") == c.id and
                     r.get("verdict") in VERDICTS and
                     isinstance(r.get("reason"), str) and r["reason"].strip()
                     for r, c in zip(rows, claims)))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError(f"unusable semantic judge response: {output[:200]!r}") from exc
    if not valid:
        raise ValueError(f"unusable semantic judge verdicts: {output[:200]!r}")
    if assess_interaction:
        if parsed.get('interaction') not in ('answer','clarification') or 'benchmark_error' not in parsed:
            raise ValueError('Unusable interaction judgment')
        if parsed['benchmark_error'] is not None:
            if not isinstance(parsed['benchmark_error'],str) or not parsed['benchmark_error'].strip():raise ValueError('Malformed benchmark error')
            raise BenchmarkContractError(parsed['benchmark_error'])
    recovery = parsed.get('recovery') if assess_recovery else None
    if assess_recovery:
        validate_recovery(recovery, rows)
    result = Grade(checkpoint=checkpoint_id, recovery=recovery,interaction=parsed.get('interaction','answer'))
    if diagnostic_inputs is not None:
        result.evidence_diagnostic = validate_diagnostic(parsed.get("evidence_diagnostic"), claims, answer, diagnostic_inputs, diagnostic_version)
    result.slots = [GradedClaim(r["id"], r["verdict"], r["reason"]) for r in rows]
    result.correct = sum(r.verdict == "correct" for r in result.slots)
    result.missed = sum(r.verdict == "missed" for r in result.slots)
    result.misrepresented = sum(r.verdict == "misrepresented" for r in result.slots)
    result.claim_coverage = result.correct / len(claims)
    result.all_claims_correct = result.correct == len(claims)
    return result


def validate_recovery(recovery, rows):
    if (not isinstance(recovery, list) or len(recovery) != len(rows) or
        any(not isinstance(r, dict) or r.get('id') != row['id'] or
            r.get('kind') not in ({'provided'} if row['verdict'] == 'correct' else
                                 {'incorrect'} if row['verdict'] == 'misrepresented' else
                                 {'explicitly_requested', 'unavailable', 'implicit_omission'}) or
            not isinstance(r.get('reason'), str) or not r['reason'].strip()
            for r, row in zip(recovery, rows))):
        raise ValueError('unusable recovery eligibility judgment')
