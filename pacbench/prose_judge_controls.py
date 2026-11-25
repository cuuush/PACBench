"""Prepare the synthetic booking controls in memory for either judge.

The cutoffs select authored facts and references before rendering. The output
uses judge_blind_controls' case fields plus the visible reference texts for
adapters that consume evidence. The answer key is a separate test fixture.
"""
from .prepare import read_spec
from .spec_first_pilot import FIXTURES
from .workflow_spec import instant

FIXTURE = FIXTURES / 'prose_booking_controls_v1.json'


def _at_cutoff(spec, receipts):
    cutoff = instant(spec['question_at'])
    notices = spec['notices']
    ids = {n['id'] for n in notices}
    if len(ids) != len(notices) or receipts.keys() != ids:
        raise ValueError('each notice must have a unique ID and one receipt')
    required = spec['required']
    slots = set(required)
    facts, references = {}, []
    # Apply visible amendments in authored order. A late receipt cannot unlock
    # any fact or reference from that notice, even when it was written earlier.
    previous = None
    for notice in notices:
        written, received = instant(notice['written_at']), instant(receipts[notice['id']])
        if received < written or (previous is not None and written <= previous):
            raise ValueError('invalid notice or receipt chronology')
        previous = written
        if received > cutoff:
            continue
        if notice['facts'].keys() - slots:
            raise ValueError('notice updates an unknown criterion')
        references.append(dict(id=notice['id'], text=notice['text'], written_at=notice['written_at'],
                               received_at=receipts[notice['id']]))
        for slot, statement in notice['facts'].items():
            facts[slot] = (slot, statement, (notice['id'],))
    if slots != facts.keys():
        raise ValueError('a required criterion has no evidence at the question cutoff')
    return [facts[slot] for slot in required], references


def prepare_cases(spec=None):
    """Return six label-free case inputs. No renderer or inference is invoked."""
    if spec is None:
        spec = read_spec(FIXTURE)
    if spec['revision'] != 'prose-booking-controls-v1' or spec['required'] != ['room', 'booking']:
        raise ValueError('unexpected booking fixture revision or required criterion order')
    snapshots = {name: _at_cutoff(spec, receipts) for name, receipts in spec['receipts'].items()}
    cases = []
    for row in spec['cases']:
        claims, references = snapshots[row['receipts']]
        cases.append(dict(id=row['id'], at=spec['question_at'], question=spec['question'], answer=row['answer'],
                          claims=list(claims), references=[dict(r) for r in references]))
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('duplicate prose case ID')
    return cases
