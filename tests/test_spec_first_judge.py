"""Offline judge controls for the synthetic staffing form and the public r2 scope exception."""

from copy import deepcopy

import pytest

from pacbench.corpus import r2 as pilot

@pytest.fixture(scope='module')
def rsvp():
    return pilot.historical_rsvp_scopes()

def test_historical_rsvp_drift_remains_visible_with_the_pinned_exception(rsvp):
    with pytest.raises(ValueError, match='unrecognized scope drift'):
        pilot.audit_scope(*rsvp)
    audit = pilot.audit_scope(*rsvp, pilot.read_spec(pilot.EXCEPTION))
    assert audit['status'] == 'existing-scope exception'
    assert audit['differences'] == ['question', 'criteria']
    assert (audit['authored_facts'], audit['scored_claims']) == (5, 4)
    assert any('No person outside' in f for f in audit['authored_only'])
    assert 'Maya sent the lunch invitation.' in audit['scored_only']

@pytest.mark.parametrize('change', ['fifth-claim', 'evidence', 'question', 'authored-fact', 'other-track',
                                    'replaced-by-roster'])
def test_scope_exception_never_covers_another_contract(rsvp, change):
    authored, scored = deepcopy(rsvp)
    if change == 'fifth-claim':
        scored['claims'].append(dict(id='fact-5', description=authored['facts'][2], evidence=()))
    elif change == 'evidence':
        scored['claims'][0]['evidence'] = ('different-source',)
    elif change == 'question':
        scored['question'] += ' Who else might come?'
    elif change == 'authored-fact':
        authored['facts'][0] += ' Updated.'
    elif change == 'other-track':
        scored['track'] = 'another-track'
    else:
        scored['question'] = authored['question']
        scored['claims'] = [dict(id=f'fact-{i}', description=f, evidence=())
                            for i, f in enumerate(authored['facts'], 1)]
    with pytest.raises(ValueError, match='unrecognized scope drift'):
        pilot.audit_scope(authored, scored, pilot.read_spec(pilot.EXCEPTION))
