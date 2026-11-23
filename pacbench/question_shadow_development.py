"""Predeclared development regression on two inspected, immutable Week answer exports."""
import hashlib
import json
from pathlib import Path

from .question_judge import BASELINE, PROTOCOL, fingerprint, interpret
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


def wire(body):
    return json.dumps(body, sort_keys=True, ensure_ascii=False).encode()


def render(directory):
    by_id = {}
    for arm, digest in EXPORTS.items():
        manifest, pairs = prepare(directory / (arm + '.jsonl'), digest, 'logical')
        origin = manifest['retained_origin']
        if origin['arm'] != arm or origin['cohort_manifest_sha256'] != INVENTORY:
            raise ValueError('Unexpected retained source')
        by_id.update({f'{arm}/{p.turn.id}': p for p in pairs})

    cells, cases = [], []
    for index, (case, expected) in enumerate(REFERENCE.items()):
        pair = by_id[case]
        if expected.keys() - {rule['id'] for rule in pair.rules}:
            raise ValueError('Expected criterion absent from the original turn')
        cases.append(dict(case=case, at=pair.turn.at, **pair.manifest()))
        for arm in ((BASELINE, PROTOCOL) if index % 2 == 0 else (PROTOCOL, BASELINE)):
            body = pair.requests[arm]
            cells.append(dict(id=case + '/' + arm, case=case, arm=arm, body=body,
                              body_sha256=hashlib.sha256(wire(body)).hexdigest()))
    if len(cells) > 24 or len({c['body_sha256'] for c in cells}) != len(cells):
        raise ValueError('Duplicate or excessive development requests')
    manifest = dict(version='question-contract-development-shadow-v1', revision=REVIEWED,
                    exports=EXPORTS, cohort_manifest=INVENTORY, historical_overlay=AUDIT_REPORT,
                    reference=REFERENCE, cases=cases,
                    cells=[{k: v for k, v in c.items() if k != 'body'} for c in cells])
    return manifest, cells, {case: by_id[case] for case in REFERENCE}


def compare(cases, replies):
    results = {}
    for case, targets in REFERENCE.items():
        sides = {}
        for arm in (BASELINE, PROTOCOL):
            parsed = interpret(cases[case], arm, replies.get(case + '/' + arm))
            slots = {row['id']: row['verdict'] for row in parsed['rows']}
            sides[arm] = dict(state=parsed['state'], graded=parsed['graded'],
                              targets={key: slots.get(key) for key in targets})
        results[case] = sides
    return results


def save(path, data):
    new_private_file(Path(path), [wire(data) + b'\n'])
