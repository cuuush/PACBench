"""Predeclared development regression on two inspected, immutable Week answer exports."""
import hashlib
import json
from pathlib import Path

from .question_judge import BASELINE, PROTOCOL, fingerprint, interpret
from . import question_judge_spans as spans
from .question_judge_week import Week
from .question_judge_batch import new_private_file, prepare

REVIEWED = '5cb8865d2388dad8ded501b2a6340b3171260d91'
EXPORTS = {
    'memcal': '189120c948b4c97548761951ae3de34cd3601debe4c38e69e2898e61e4bc6629',
    'memos': '045fbb6a5b6c92552c31c209b20ad3f1450a01d8f0542de2c380f85f03957265',
}
INVENTORY = 'a98d3d8ea0c5348e44faf159ff22a4e467d58201e77a3802cbb5453a16aa506b'
AUDIT_REPORT = 'e8058ad4f983215d7ef3c50c8767de320636c0da060f8576a5d5503a46dd7bf4'
# Full questions go to both judges. Only the predeclared attributes enter correctness.
# None is a genuine uncertainty about how to credit the answer, never a model miss.
REFERENCE = {
    'memcal/rsvp-before': {
        'fact-3': ('correct', 'Leo has not replied at the first cutoff.'),
    },
    'memcal/rsvp-after': {
        'fact-3': ('correct', 'Leo has now confirmed and the answer includes him.'),
        'fact-6': (None, 'The named group is accurate. Whether the phrasing closes the list is disputed.'),
    },
    'memcal/withdrawal': {
        'fact-8': ('correct', 'The answer identifies the four still in and explicitly removes Leo.'),
    },
    'memos/withdrawal': {
        'fact-8': ('correct', 'The answer identifies the four still in and explicitly removes Leo.'),
    },
    'memcal/draft-after': {
        'fact-1': ('correct', 'The user is going to the tasting.'),
        'fact-5': ('correct', 'Devon declined the invitation. His absence leaves the user’s tasting intact.'),
    },
    'memcal/assistant-friday-plans': {
        'lunch-time': ('correct', 'The answer pairs 12:30 with Maya’s lunch despite another 12:30 event.'),
        'lunch-street': ('missed', 'The answer omits the street for the lunch venue.'),
    },
    'memos/assistant-friday-plans': {
        'lunch-time': ('correct', 'The lunch with Maya is labeled 12:30 in the agenda.'),
        'lunch-attendees': ('misrepresented', 'The extra named attendees contradict the latest two-person plan.'),
        'lunch-venue': ('misrepresented', 'The answer names the superseded restaurant.'),
    },
    'memcal/friday-lunch-time': {
        'lunch-time': ('correct', 'The question identifies the lunch. The answer supplies 12:30 pm.'),
    },
}


# Predeclared independently of responses to the span-format requests.
SPAN_REFERENCE = {case: dict(REFERENCE[case]) for case in (
    'memcal/rsvp-after', 'memcal/withdrawal', 'memcal/assistant-friday-plans',
    'memos/assistant-friday-plans', 'memcal/friday-lunch-time')}
SPAN_REFERENCE['memcal/assistant-friday-plans'].update({
    'lunch-date': ('correct', 'The Friday Oct 9 agenda heading scopes the lunch with Maya.'),
    'pizza-date': ('correct', 'The same heading scopes the separate pizza with Devon bullet.'),
})
CONTROL_ANSWER = '12:30 p.m.'
SPAN_REFERENCE.update({
    'control/assistant-friday-plans': {'lunch-time': (
        'missed', 'A bare time in the broad agenda does not identify which event it describes.')},
    'control/friday-lunch-time': {'lunch-time': (
        'correct', 'The direct question supplies the lunch referent for the identical bare time.')},
})


def wire(body):
    return json.dumps(body, sort_keys=True, ensure_ascii=False).encode()


def render(directory, *, candidate=PROTOCOL):
    if candidate not in (PROTOCOL, spans.PROTOCOL):
        raise ValueError('Unknown named regression')
    version_two = candidate == spans.PROTOCOL
    reference = SPAN_REFERENCE if version_two else REFERENCE
    by_id = {}
    for arm, digest in EXPORTS.items():
        manifest, pairs = prepare(directory / (arm + '.jsonl'), digest, 'logical', candidate=candidate)
        origin = manifest['retained_origin']
        if origin['arm'] != arm or origin['cohort_manifest_sha256'] != INVENTORY:
            raise ValueError('Unexpected retained source')
        by_id.update({f'{arm}/{p.turn.id}': p for p in pairs})

    if version_two:
        week = Week(candidate=candidate)
        for ident in ('assistant-friday-plans', 'friday-lunch-time'):
            original = by_id['memcal/' + ident]
            by_id['control/' + ident] = week.prepare(dict(turn_id=ident, answer_state='received',
                answer=CONTROL_ANSWER, prior_public_dialogue=original.history, delivered_corrections={}))
    cells, cases = [], []
    for index, (case, expected) in enumerate(reference.items()):
        pair = by_id[case]
        if expected.keys() - {rule['id'] for rule in pair.rules}:
            raise ValueError('Expected criterion absent from the original turn')
        cases.append(dict(case=case, at=pair.turn.at, **pair.manifest()))
        for arm in ((BASELINE, candidate) if index % 2 == 0 else (candidate, BASELINE)):
            body = pair.requests[arm]
            cells.append(dict(id=case + '/' + arm, case=case, arm=arm, body=body,
                              body_sha256=hashlib.sha256(wire(body)).hexdigest()))
    if version_two and len(cells) != 14:
        raise ValueError('Expected seven pairs in the named span regression')
    if len(cells) > 24 or len({c['body_sha256'] for c in cells}) != len(cells):
        raise ValueError('Duplicate or excessive development requests')
    manifest = dict(version='question-contract-development-shadow-v1', revision=REVIEWED,
                    exports=EXPORTS, cohort_manifest=INVENTORY, historical_overlay=AUDIT_REPORT,
                    reference=reference, cases=cases,
                    cells=[{k: v for k, v in c.items() if k != 'body'} for c in cells])
    if version_two:
        manifest.update(version='question-contract-span-development-regression-v2',
                        revision='96e0ac78f0de4c9724ca4dd755f413bafd360f9d',
                        synthetic_controls=['control/assistant-friday-plans', 'control/friday-lunch-time'])
    return manifest, cells, {case: by_id[case] for case in reference}


def compare(cases, replies, *, reference=REFERENCE):
    results = {}
    for case, targets in reference.items():
        sides = {}
        for arm in (BASELINE, cases[case].candidate):
            parsed = interpret(cases[case], arm, replies.get(case + '/' + arm))
            slots = {row['id']: row['verdict'] for row in parsed['rows']}
            sides[arm] = dict(state=parsed['state'], graded=parsed['graded'],
                              targets={key: slots.get(key) for key in targets})
            if cases[case].candidate == spans.PROTOCOL:
                sides[arm].update(planned=parsed['planned'], quarantined=[
                    row['id'] for row in parsed['rows'] if row.get('state') == 'ungraded'],
                    abstained=[row['id'] for row in parsed['rows'] if row.get('state') == 'abstained'])
        results[case] = sides
    return results


def save(path, data):
    new_private_file(Path(path), [wire(data) + b'\n'])
