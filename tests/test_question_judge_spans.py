"""Span-format regressions, including the original sealed Friday parser failure."""

import json

import pytest

from pacbench.judging import paired as spans

ANSWER = '📅 Friday Oct 9:\n- Parcel at 9.\n- Lunch with Maya at 12:30.\n- Pizza at 6.'

RULES = [dict(id=k, mode='affirm') for k in ('date', 'time', 'street')]

HEADING = 'Friday Oct 9:'

BULLET = 'Lunch with Maya at 12:30.'

def row(key, *evidence, relation='entailed'):
    return dict(id=key, answer_spans=list(evidence), relation=relation, reason='fixture')

def outcomes(rows):
    return spans.parse_spans(json.dumps({'verdicts': rows}), RULES, ANSWER)

def test_nonadjacent_unicode_spans_and_independent_rows():
    start = ANSWER.index(BULLET)
    rows, diagnostics = outcomes([row('time', HEADING + ' ' + BULLET),
        row('date', HEADING, dict(text=BULLET, start=start, end=start + len(BULLET))),
        row('street', relation='absent')])
    assert [r['verdict'] for r in rows] == ['correct', None, 'missed']
    assert rows[0]['answer_spans'][0] == dict(text=HEADING, start=2, end=15)
    for span in rows[0]['answer_spans']:
        assert ANSWER[span['start']:span['end']] == span['text']
    assert diagnostics == dict(foreign_rows=0, unidentified_rows=0)

@pytest.mark.parametrize('evidence', [
    [HEADING, 'Friday'], ['at'], ['   '], ['12:30...Friday'], ['x' * 321],
    ['Lunch', 'Maya', 'Pizza', 'Parcel', 'Friday'], ['Lunch', 'Lunch'],
    [{'start': True, 'end': 7, 'text': 'Friday'}],
    [{'start': 0, 'end': 6, 'text': 'Friday'}], [None], [dict(text='Friday', start=2, end=8, extra=1)],
])
def test_bad_evidence_only_quarantines_its_criterion(evidence):
    rows, _ = outcomes([row('date', *evidence), row('time', '12:30')])
    assert [r['verdict'] for r in rows] == [None, 'correct', None]
    assert rows[2]['error'] == 'missing criterion'

def test_repeated_substring_requires_valid_offsets_including_overlapping_matches():
    for evidence, verdict in [('aa', None), (dict(text='aa', start=1, end=3), 'correct')]:
        r, _ = spans.parse_spans(json.dumps({'verdicts': [row('date', evidence)]}), RULES, 'aaa')
        assert r[0]['verdict'] == verdict

def test_duplicate_foreign_and_unidentified_never_receive_credit():
    rows, stats = outcomes([None, row('foreign', HEADING), row('date', HEADING),
                            row('time', '12:30'), row('date', 'Oct 9')])
    assert [r['verdict'] for r in rows] == [None, 'correct', None]
    assert rows[0]['error'] == 'duplicate criterion ID'
    assert stats == dict(foreign_rows=1, unidentified_rows=1)
    text = json.dumps({'verdicts': [row('date', HEADING), row('time', '12:30'), row('street', relation='abstain')]})
    text = text.replace('"id": "date"', '"id":"time","id":"date"')
    rows, _ = spans.parse_spans(text, RULES, ANSWER)
    assert [r['state'] for r in rows] == ['ungraded', 'ungraded', 'abstained']

@pytest.mark.parametrize('raw', ['{', '[]', '{"wrong":[]}', '{"verdicts":{}}', '{"verdicts":[]}',
    '{"verdicts":[{"id":"other"}]}', '{"verdicts":[{"reason":"missing criterion ID"}]}',
    '{"verdicts":[],"verdicts":[{"id":"date"}]}'])
def test_malformed_or_unidentifiable_envelope_refused(raw):
    with pytest.raises(ValueError):
        spans.parse_spans(raw, RULES, ANSWER)
