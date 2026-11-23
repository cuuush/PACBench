"""Source-side minimal pairs for a synthetic two-job print exchange move."""
from dataclasses import replace

import pytest

from pacbench.spec_first_pilot import DeclinedEveryRole, VolunteerForm


QUESTION = DeclinedEveryRole(
    actor='Kit', recipient='Sol', event='the print exchange move',
    roles=('lifting', 'driving'), as_of='2026-05-06T16:00:00-04:00', source_id='move-reply',
)
REPLY = VolunteerForm(
    id='move-reply', actor='Kit', recipient='Sol', event='the print exchange move',
    written_at='2026-05-06T15:55:00-04:00', received_at='2026-05-06T15:56:00-04:00',
    reported_unable=frozenset({'lifting'}),
)
BOTH = frozenset({'lifting', 'driving'})


@pytest.mark.parametrize(('source_changes', 'expected'), [
    pytest.param({}, 'insufficient', id='reports-inability-to-lift'),
    pytest.param(dict(offered=frozenset({'driving'})), 'contradicted', id='also-offers-to-drive'),
    pytest.param(dict(declined=frozenset({'lifting'})), 'insufficient', id='declines-one-job'),
    pytest.param(dict(reported_unable=BOTH), 'insufficient', id='reports-inability-for-both-without-refusing'),
    pytest.param(dict(reported_unable=frozenset(), declined=BOTH), 'supported', id='declines-both-without-ability-report'),
    pytest.param(dict(declined=BOTH, received_at='2026-05-06T20:00:00Z'), 'supported', id='received-at-cutoff'),
    pytest.param(dict(declined=BOTH, received_at='2026-05-06T20:00:01Z'), 'insufficient', id='received-after-cutoff'),
    pytest.param(dict(declined=BOTH, recipient='Bea'), 'insufficient', id='told-someone-else'),
    pytest.param(dict(declined=BOTH, actor='Bea'), 'insufficient', id='different-volunteer'),
    pytest.param(dict(declined=BOTH, event='the print exchange setup'), 'insufficient', id='different-event'),
    pytest.param(dict(declined=BOTH, id='other-reply'), 'insufficient', id='uncited-reply'),
])
def test_only_source_changes(source_changes, expected):
    assert QUESTION.prompt == 'What did Kit tell Sol about helping with the print exchange move?'
    claim = QUESTION.claim
    assert (claim.id, claim.statement, claim.evidence) == (
        'all-roles-declined',
        'Kit told Sol they declined every job for the print exchange move (lifting, driving).',
        ('move-reply',),
    )
    assert QUESTION.check_source(replace(REPLY, **source_changes)) == expected


def test_malformed_form_is_distinct_from_insufficient_evidence():
    with pytest.raises(ValueError, match='conflicting decisions'):
        QUESTION.check_source(replace(REPLY, offered=frozenset({'driving'}), declined=BOTH))
    with pytest.raises(ValueError, match='unlisted job'):
        QUESTION.check_source(replace(REPLY, offered=frozenset({'catering'})))
    with pytest.raises(ValueError, match='before it was written'):
        QUESTION.check_source(replace(REPLY, received_at='2026-05-06T15:54:59-04:00'))
