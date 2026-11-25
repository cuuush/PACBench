"""Versioned exact-span evidence and independent criterion validation."""
from collections import defaultdict
import json

PROTOCOL = 'question-scoped-assertions-spans-v2'
MAX_SPANS = 4
MAX_CHARS = 320

# Authored assertions, relation definitions and scope are shared with v1.
FORMAT = """Return only {"verdicts":[{"id":...,"relation":...,"answer_spans":[...],"reason":...}]} with one row for each assertion ID. Give a short reason. For entailed, contradicted and ambiguous supply one to four short, exact substrings of the latest answer, each at most 320 Unicode characters. Use separate spans for a heading and a nonadjacent bullet. Copy each independently, including its original punctuation. Each span may be a unique verbatim string or {"text":...,"start":...,"end":...} using zero-based Unicode code-point offsets with an exclusive end. Choose enough context to make a string unique. Spans within a row must be distinct and nonoverlapping. For absent and abstain use an empty list. An entailed prohibition satisfied by silence may also use an empty list. Do not require the answer to cite a source."""


class _Object(dict):
    """Keep duplicate keys local to their JSON object, including conflicting row IDs."""
    def __init__(self, pairs):
        super().__init__()
        self.duplicates = set()
        self.ids = set()
        for key, value in pairs:
            if key in self:
                self.duplicates.add(key)
            if key == 'id' and isinstance(value, str):
                self.ids.add(value)
            self[key] = value


def _offsets(answer, span):
    if isinstance(span, str):
        quote = span
        start = answer.find(quote)
        if start < 0 or answer.find(quote, start + 1) >= 0:
            raise ValueError('quote absent or not unique')
        end = start + len(quote)
    elif isinstance(span, dict) and span.keys() == {'text', 'start', 'end'} and not span.duplicates:
        quote, start, end = span['text'], span['start'], span['end']
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(answer)
                or answer[start:end] != quote):
            raise ValueError('invalid exact offsets')
    else:
        raise ValueError('invalid span fields')
    if not quote.strip() or len(quote) > MAX_CHARS:
        raise ValueError('empty or oversized span')
    return dict(text=quote, start=start, end=end)


def _row(row, rule, answer, relations, *, legacy):
    key = 'answer_quote' if legacy else 'answer_spans'
    if row.duplicates or row.keys() != {'id', 'relation', key, 'reason'}:
        raise ValueError('invalid row fields')
    rel, spans, reason = row['relation'], row[key], row['reason']
    if not isinstance(rel, str) or rel not in relations or not isinstance(reason, str) or not reason.strip():
        raise ValueError('invalid relation or reason')
    if legacy:
        # Standalone replay of an authentic v1 reply. Never split or repair its quote.
        if spans is not None and not isinstance(spans, str):
            raise ValueError('invalid legacy quote type')
        spans = [] if spans is None else [spans]
    if not isinstance(spans, list) or len(spans) > MAX_SPANS:
        raise ValueError('invalid span list')
    if rel in ('absent', 'abstain'):
        if spans:
            raise ValueError('unexpected evidence for absence or abstention')
    elif not spans and not (rel == 'entailed' and rule['mode'] == 'prohibit'):
        raise ValueError('required answer evidence missing')
    evidence = sorted((_offsets(answer, span) for span in spans), key=lambda s: s['start'])
    if any(a['end'] > b['start'] for a, b in zip(evidence, evidence[1:])):
        raise ValueError('overlapping or repeated spans')
    return dict(id=rule['id'], relation=rel, verdict=relations[rel], answer_spans=evidence,
                reason=reason, state='abstained' if rel == 'abstain' else 'graded')


def parse(text, rules, answer, *, legacy=False):
    from .judge_format_review import _invalid_constant
    from .question_judge import RELATIONS

    result = json.loads(text, object_pairs_hook=_Object, parse_constant=_invalid_constant)
    if (not isinstance(result, _Object) or result.duplicates or result.keys() != {'verdicts'}
            or not isinstance(result['verdicts'], list)):
        raise ValueError('invalid judgment envelope')
    indexed = defaultdict(list)
    expected = {rule['id'] for rule in rules}
    foreign = unidentified = 0
    for row in result['verdicts']:
        if not isinstance(row, _Object) or not row.ids:
            unidentified += 1
            continue
        # Conflicting duplicate ID keys invalidate every named expected slot.
        matched = row.ids & expected
        foreign += bool(row.ids - expected)
        for ident in matched:
            indexed[ident].append(row)
    if not indexed:
        raise ValueError('no identifiable planned criterion')
    outcomes = []
    for rule in rules:
        ident = rule['id']
        matches = indexed[ident]
        if len(matches) == 1:
            try:
                outcomes.append(_row(matches[0], rule, answer, RELATIONS, legacy=legacy))
                continue
            except ValueError as exc:
                detail = str(exc)
        else:
            detail = 'duplicate criterion ID' if matches else 'missing criterion'
        outcomes.append(dict(id=ident, verdict=None, state='ungraded', error=detail))
    return outcomes, dict(foreign_rows=foreign, unidentified_rows=unidentified)
