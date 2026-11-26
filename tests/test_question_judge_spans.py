"""Span-format regressions, including the original sealed Friday parser failure."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from pacbench import judge_development_challenge as sesame
from pacbench.judging import spans as spans
from pacbench.judging.paired import BASELINE, PROTOCOL, fingerprint, interpret
from pacbench.judging.shadow import CONTROL_ANSWER, SPAN_REFERENCE, render

ANSWER = '📅 Friday Oct 9:\n- Parcel at 9.\n- Lunch with Maya at 12:30.\n- Pizza at 6.'
RULES = [dict(id=k, mode='affirm') for k in ('date', 'time', 'street')]
HEADING = 'Friday Oct 9:'
BULLET = 'Lunch with Maya at 12:30.'


def row(key, *evidence, relation='entailed'):
    return dict(id=key, answer_spans=list(evidence), relation=relation, reason='fixture')


def outcomes(rows):
    return spans.parse(json.dumps({'verdicts': rows}), RULES, ANSWER)


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
        r, _ = spans.parse(json.dumps({'verdicts': [row('date', evidence)]}), RULES, 'aaa')
        assert r[0]['verdict'] == verdict


def test_duplicate_foreign_and_unidentified_never_receive_credit():
    rows, stats = outcomes([None, row('foreign', HEADING), row('date', HEADING),
                            row('time', '12:30'), row('date', 'Oct 9')])
    assert [r['verdict'] for r in rows] == [None, 'correct', None]
    assert rows[0]['error'] == 'duplicate criterion ID'
    assert stats == dict(foreign_rows=1, unidentified_rows=1)
    text = json.dumps({'verdicts': [row('date', HEADING), row('time', '12:30'), row('street', relation='abstain')]})
    text = text.replace('"id": "date"', '"id":"time","id":"date"')
    rows, _ = spans.parse(text, RULES, ANSWER)
    assert [r['state'] for r in rows] == ['ungraded', 'ungraded', 'abstained']


@pytest.mark.parametrize('raw', ['{', '[]', '{"wrong":[]}', '{"verdicts":{}}', '{"verdicts":[]}',
    '{"verdicts":[{"id":"other"}]}', '{"verdicts":[{"reason":"missing criterion ID"}]}',
    '{"verdicts":[],"verdicts":[{"id":"date"}]}'])
def test_malformed_or_unidentifiable_envelope_refused(raw):
    with pytest.raises(ValueError):
        spans.parse(raw, RULES, ANSWER)


def local_exports():
    if not (value := os.getenv('PACBENCH_LOCAL_SHADOW_TEST_EXPORTS')):
        pytest.skip('requires inspected runner-local exports')
    return Path(value)


def test_sealed_response_replay_and_counterfactual_controls():
    exports = local_exports()
    raw = (exports.parent / 'pizza-v2-live-20261121' / 'pizza-week-question-contract-development-shadow-v1'
           / 'observation-11.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'b4795ff0779ebeaeaf622693a896c902c5cbc84ebef0307f603d792cd770e33b'
    observed = json.loads(raw)
    name = 'memcal/assistant-friday-plans'
    old = render(exports)[2][name]
    assert observed['cell'] == name + '/' + PROTOCOL
    reply = observed['reply']
    assert reply['finish_reason'] == 'stop'
    assert reply['request_sha256'] == fingerprint(old.requests[PROTOCOL])
    assert interpret(old, PROTOCOL, reply)['graded'] == 0
    rows, _ = spans.parse(reply['text'], old.rules, old.answer, legacy=True)
    assert [r['id'] for r in rows if r['state'] == 'ungraded'] == ['lunch-date', 'pizza-date']
    assert sum(r['verdict'] is not None for r in rows) == 10
    manifest, cells, pairs = render(exports, candidate=spans.PROTOCOL)
    assert manifest['reference'] == SPAN_REFERENCE
    assert len(cells) == 14
    assert old.requests[BASELINE] == pairs[name].requests[BASELINE]
    for ident, label in [('assistant-friday-plans', 'missed'), ('friday-lunch-time', 'correct')]:
        real, control = pairs['memcal/' + ident], pairs['control/' + ident]
        assert (control.answer, control.turn, control.history, control.rules) == (
            CONTROL_ANSWER, real.turn, real.history, real.rules)
        assert manifest['reference']['control/' + ident]['lunch-time'][0] == label
        assert json.loads(control.requests[spans.PROTOCOL]['messages'][1]['content'])['latest_answer'] == CONTROL_ANSWER
    for cell in cells:
        assert 'control/' not in json.dumps(cell['body'])
        assert not any(reason in json.dumps(cell['body'], ensure_ascii=False)
                       for group in SPAN_REFERENCE.values() for _, reason in group.values())


def test_exact_existing_sesame_development_cases_offline():
    # Replay known labels and answers through the parser. This makes no model prediction.
    gold = sesame._frozen(sesame.REFERENCE, sesame.REFERENCE_SHA256)['families']['dietary-uncertainty']['cases']
    expected = {'pine': 'absent', 'fern': 'contradicted', 'slate': 'entailed', 'jade': 'entailed'}
    cells = [c for c in sesame._blinded_cells('9f' * 32) if c.family['id'] == 'dietary-uncertainty']
    assert {c.key for c in cells} == set(expected)
    for cell in cells:
        (_, answer, _), options = cell.judge_input
        assert options['judge_protocol'] == BASELINE and len(options['conversation']) == 2
        assert answer in sesame._request(cell, 'v4')['prompt']
        evidence = [] if cell.key == 'pine' else [cell.family['cases'][cell.key]]
        text = json.dumps({'verdicts': [row('criterion', *evidence, relation=expected[cell.key])]})
        verdicts, _ = spans.parse(text, [dict(id='criterion', mode='affirm')], answer)
        assert verdicts[0]['verdict'] == gold[cell.key]['verdict']
