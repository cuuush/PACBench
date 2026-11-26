"""Use the frozen development fixture and synthetic HTTP/account responses only."""
from collections import Counter
from hashlib import sha256
import json

import pytest

from pacbench.judging import decision_matrix as plan
from pacbench import direct_http
from pacbench import judge_development_challenge as challenge
from pacbench.judging import development_live as prepaid
from pacbench.judging.decision_admission import DecisionsAdmission, NAME, validate_response
from tests.test_judge_development_live import synthetic_checkpoint


SEED = '9f' * 32
MANIFEST = '3e2beb4453788e8d844318cfe860ec5d521cbea9a61f203fa6e326b7b0c8f091'


def test_exact_four_arm_manifest_does_not_open_reference(monkeypatch):
    reference = challenge._frozen(challenge.REFERENCE, challenge.REFERENCE_SHA256)['families']
    labels = {}
    for cell in challenge._blinded_cells(SEED):
        family = cell.family['id']
        labels[cell.id] = (family, reference[family]['cases'][cell.key]['reason'])
    old = {row['id']: row['prompt'] for row in challenge.plan(SEED, 'v4')}
    explicit = {row['id']: row['prompt'] for row in challenge.plan(SEED, 'explicit')}
    monkeypatch.setattr(challenge, 'REFERENCE', challenge.REFERENCE.with_suffix('.unavailable'))
    frozen = plan.render_plan(SEED)

    assert frozen.digest == MANIFEST
    assert len({cell.digest for cell in frozen.cells}) == 96
    assert len({cell.id for cell in frozen.cells}) == 96
    assert Counter(cell.arm for cell in frozen.cells) == {arm: 24 for arm in plan.ARMS}
    for index in range(4):
        assert Counter(cell.arm for cell in frozen.cells[index::4]) == {arm: 6 for arm in plan.ARMS}
    for cell in frozen.cells:
        body = json.loads(cell.body)
        for hidden in (*labels[cell.case_id], cell.case_id, SEED):
            assert hidden not in cell.body.decode()
        if cell.arm in plan.ROUTES:
            route = plan.ROUTES[cell.arm]
            assert body.keys() == {'model', 'provider', 'questions', 'state'}
            assert cell.url == plan.DECISIONS_URL
            assert body['model'] == route.model
            assert body['provider'] == {
                'only': [route.endpoint], 'order': [route.endpoint], 'allow_fallbacks': False,
            }
            assert body['provider']['allow_fallbacks'] is False
            assert body['state'] == explicit[cell.case_id].replace(plan.CHAT_OUTPUT, '\n\n', 1)
            assert set(body['questions']['criterion']['criteria']) == {'correct', 'missed', 'misrepresented'}
        else:
            assert body['messages'] == [{
                'role': 'user', 'content': (old if cell.arm == 'v4' else explicit)[cell.case_id],
            }]
            assert body['provider']['only'] == ['amazon-bedrock/global']
            assert body['provider']['allow_fallbacks'] is False
            assert body['max_completion_tokens'] == 32768


def test_paired_errors_keep_semantic_changes_separate_from_unusable_outputs():
    plan_cells = plan.render_plan(SEED).cells
    reference = challenge._frozen(challenge.REFERENCE, challenge.REFERENCE_SHA256)['families']
    expected = {cell.id: reference[cell.family['id']]['cases'][cell.key]['verdict']
                for cell in challenge._blinded_cells(SEED)}
    assert Counter(expected.values()) == {'correct': 12, 'missed': 6, 'misrepresented': 6}
    results = {cell.id: plan.Verdict('valid', expected[cell.case_id]) for cell in plan_cells}
    positives = [ident for ident, gold in expected.items() if gold == 'correct']
    conflicts = [ident for ident, gold in expected.items() if gold == 'misrepresented']
    results[positives[0] + ':v4'] = plan.Verdict('valid', 'missed')
    results[positives[1] + ':luna-decisions'] = plan.Verdict('valid', 'missed')
    results[conflicts[0] + ':luna-decisions'] = plan.Verdict('valid', 'correct')
    results[conflicts[1] + ':luna-decisions'] = plan.Verdict('abstained')
    results[conflicts[2] + ':luna-decisions'] = plan.Verdict('invalid')
    del results[conflicts[3] + ':luna-decisions']

    report = plan.paired_errors(SEED, results.items())
    assert report['cases'] == 24 and report['manifest_sha256'] == MANIFEST
    assert report['arms']['luna-decisions'] == {
        'exact': 19, 'false_accepts': 1, 'false_rejects': 1,
        'invalid': 1, 'abstained': 1, 'missing': 1,
        'by_family': report['arms']['luna-decisions']['by_family'],
    }
    assert len(report['arms']['luna-decisions']['by_family']) == 6
    assert report['comparisons']['luna-decisions_vs_v4']['counts'] == {
        'both_correct': 18, 'resolved_error': 1, 'new_error': 2, 'unpaired': 3,
    }
    assert report['comparisons']['luna-decisions_vs_explicit']['counts'] == {
        'both_correct': 19, 'new_error': 2, 'unpaired': 3,
    }
    changes = report['comparisons']['luna-decisions_vs_v4']['changes']
    assert {row['id'] for row in changes} == {*positives[:2], conflicts[0]}
    with pytest.raises(ValueError, match='duplicate'):
        cell_id = next(iter(results))
        plan.paired_errors(SEED, [*results.items(), (cell_id, results[cell_id])])
    with pytest.raises(ValueError, match='Unknown'):
        plan.paired_errors(SEED, [('foreign:v4', plan.Verdict('invalid'))])


@pytest.fixture
def account(tmp_path, monkeypatch):
    campaign = tmp_path/'campaign'
    campaign.mkdir()
    (campaign/'execution.lock').touch()
    checkpoint = tmp_path/'checkpoint'
    key, _, identity = synthetic_checkpoint(checkpoint)
    status = {**identity, 'limit': '11', 'limit_remaining': '10', 'limit_reset': None}
    credits = {'total_credits': '14', 'total_usage': '4'}
    calls = []

    def synthetic_account(endpoint, supplied_key):
        assert supplied_key == key
        calls.append(endpoint)
        observed = {'key': status, 'credits': credits}[endpoint]
        return plan.wire_bytes({'data': observed}), observed

    monkeypatch.setattr(prepaid, 'CAMPAIGN', campaign)
    monkeypatch.setattr(prepaid, 'CHECKPOINT', checkpoint)
    monkeypatch.setattr(prepaid, 'account_data', synthetic_account)
    return campaign, key, status, credits, calls


def open_admission(key, fd, digest=MANIFEST):
    return DecisionsAdmission(SEED, reviewed_manifest_sha256=digest, lock_fd=fd, key=key)


def prepare(admission, cell, **overrides):
    request = {'cell_id': cell.id, 'method': 'POST', 'url': cell.url, 'body': cell.body}
    return admission.prepare(**{**request, **overrides})


def response(cell, generation, answer=None):
    route = plan.ROUTES[cell.arm]
    return {
        'id': generation, 'model': route.served_model, 'provider': route.served_provider,
        'usage': {'cost': 0.000019992, 'input_tokens': 147, 'output_tokens': 1},
        'answers': {'criterion': {'type': 'choice', 'choice': 'correct'} if answer is None else answer},
    }


def send_stub(admission, cell, generation, answer=None):
    attempt = prepare(admission, cell)
    payload = plan.wire_bytes(response(cell, generation, answer))
    result = admission.settle(attempt, status=200, complete=True, body=payload, observed_generation=generation)
    return attempt, result


def test_all_48_decisions_cells_settle_with_exact_cost_and_no_replay(account):
    campaign, key, _, _, checks = account
    with prepaid.account_lock() as fd:
        admission = open_admission(key, fd)
        for index, cell in enumerate(admission.cells):
            answer = ({'type': 'refusal'} if index == 0 else
                      {'type': 'choice', 'choice': 'abstain'} if index == 1 else
                      {'type': 'choice', 'choice': ('correct', 'missed', 'misrepresented')[index % 3]})
            attempt, verdict = send_stub(admission, cell, f'gen-dec-synthetic-{index}', answer)
            if index == 0:
                assert verdict == plan.Verdict('abstained')
            elif index == 1:
                assert verdict == plan.Verdict('invalid')
            else:
                assert verdict == plan.Verdict('valid', answer['choice'])
        result = admission.audit()
        assert result['attempted'] == result['generations'] == 48
        assert result['unknown'] == [] and result['remaining'] == 0
        assert result['known_cost_usd'] == '0.000959616'
        assert len(result['verdicts']) == 48
        with pytest.raises(ValueError, match='No matching pending'):
            admission.settle(attempt, status=200, complete=True, body=b'{}')
        with pytest.raises(RuntimeError, match='no unused'):
            prepare(admission, cell)
        with pytest.raises(RuntimeError, match='unused run directory'):
            open_admission(key, fd)
    assert checks == ['key', 'credits']
    journal = (campaign/NAME/'receipts.jsonl').read_text()
    assert key not in journal
    assert journal.count('"kind": "prepared"') == journal.count('"kind": "settled"') == 48
    chat = plan.chat_verdict('{"verdicts":[{"id":"criterion","verdict":"missed","reason":"stub"}]}')
    chat_cells = ((cell.id, chat) for cell in plan.render_plan(SEED).cells if cell.arm not in plan.ROUTES)
    report = plan.paired_errors(SEED, [*result['verdicts'].items(), *chat_cells])
    assert all(arm['missing'] == 0 for arm in report['arms'].values())


def test_lock_fixed_manifest_order_body_method_route_and_stop(account):
    campaign, key, _, _, checks = account
    with pytest.raises(RuntimeError, match='shared execution lock'):
        open_admission(key, -1)
    with prepaid.account_lock() as fd:
        with pytest.raises(ValueError, match='Unreviewed'):
            open_admission(key, fd, '0' * 64)
        assert checks == []
        admission = open_admission(key, fd)
        with pytest.raises(BlockingIOError), prepaid.account_lock():
            pytest.fail('second writer acquired the executor lock')

        cell = admission.cells[0]
        altered_route = json.loads(cell.body)
        altered_route['provider']['allow_fallbacks'] = True
        substitutions = [
            {'method': 'GET'}, {'url': plan.DECISIONS_URL + '?route=typesafe'},
            {'cell_id': admission.cells[1].id}, {'body': cell.body + b' '},
            {'body': plan.wire_bytes(altered_route)}, {'body': bytearray(cell.body)},
        ]
        for overrides in substitutions:
            with pytest.raises(ValueError, match='next exact'):
                prepare(admission, cell, **overrides)
        assert admission.audit()['attempted'] == 0
        (campaign/'ACCOUNT_STOP').touch()
        with pytest.raises(RuntimeError, match='stop is active'):
            prepare(admission, cell)
        assert (campaign/'ACCOUNT_STOP').is_file()
    with pytest.raises(RuntimeError, match='shared execution lock'):
        prepare(admission, cell)


@pytest.mark.parametrize('problem', ['key', 'no-credit', 'recharge', 'resetting-key'])
def test_rejects_unverified_account_scope(account, problem):
    _, key, status, credits, calls = account
    if problem == 'no-credit':
        credits['total_usage'] = credits['total_credits']
    if problem == 'recharge':
        credits['auto_recharge'] = True
    if problem == 'resetting-key':
        status['limit_reset'] = 'daily'
    with prepaid.account_lock() as fd:
        with pytest.raises(ValueError, match='approved account|prepaid credit|Auto Recharge'):
            open_admission('foreign-key' if problem == 'key' else key, fd)
    if problem == 'key':
        assert calls == []


@pytest.mark.parametrize('problem,error', [
    ('model', 'served Decisions model'), ('provider', 'served Decisions model'),
    ('missing-id', 'generation receipt'), ('header-id', 'header generation'),
    ('no-cost', 'billed cost'), ('string-cost', 'billed cost'), ('boolean-cost', 'billed cost'),
    ('negative-cost', 'billed cost'), ('no-tokens', 'token usage'), ('byok', 'Prepaid Decisions'),
    ('incomplete', 'Complete Decisions'), ('http-429', 'Complete Decisions'),
    ('duplicate-json', 'Duplicate JSON'), ('nonfinite-json', 'Nonfinite JSON'),
])
def test_missing_or_mismatched_receipt_stops_after_one_attempt(account, problem, error):
    _, key, *_ = account
    with prepaid.account_lock() as fd:
        admission = open_admission(key, fd)
        first, second = admission.cells[:2]
        attempt = prepare(admission, first)
        with pytest.raises(RuntimeError, match='Unsettled'):
            prepare(admission, second)
        value = response(first, 'gen-dec-unverified')
        if problem in ('model', 'provider'):
            value[problem] = 'foreign'
        if problem == 'missing-id':
            del value['id']
        if problem == 'no-cost':
            del value['usage']['cost']
        if problem in ('string-cost', 'boolean-cost', 'negative-cost'):
            value['usage']['cost'] = {'string-cost': '0', 'boolean-cost': False, 'negative-cost': -1}[problem]
        if problem == 'no-tokens':
            del value['usage']['output_tokens']
        if problem == 'byok':
            value['usage']['is_byok'] = True
        raw = plan.wire_bytes(value)
        if problem == 'duplicate-json':
            raw = raw.replace(b'"usage": {', b'"usage": {"cost": 0, ')
        if problem == 'nonfinite-json':
            raw = raw.replace(b'1.9992e-05', b'NaN')
        with pytest.raises(ValueError, match=error):
            admission.settle(attempt, status=429 if problem == 'http-429' else 200,
                             complete=problem != 'incomplete', body=raw,
                             observed_generation='wrong-id' if problem == 'header-id' else None)
        report = admission.audit()
        assert report['attempted'] == 1 and report['unknown'] == [attempt]
        assert not report['verdicts'] and report['known_cost_usd'] is None
        for cell in (first, second):
            with pytest.raises(RuntimeError, match='Unsettled'):
                prepare(admission, cell)


def test_duplicate_generation_and_interrupted_dispatch(account):
    _, key, *_ = account
    with prepaid.account_lock() as fd:
        admission = open_admission(key, fd)
        first, second, third = admission.cells[:3]
        send_stub(admission, first, 'gen-dec-repeated')
        attempt = prepare(admission, second)
        # A transport fault with no complete response leaves the request pending.
        assert admission.audit()['unknown'] == [attempt]
        with pytest.raises(RuntimeError, match='Unsettled'):
            prepare(admission, second)
        with pytest.raises(ValueError, match='Duplicate Decisions generation'):
            admission.settle(attempt, status=200, complete=True,
                             body=plan.wire_bytes(response(second, 'gen-dec-repeated')))
        result = admission.audit()
        assert result['generations'] == 1 and result['unknown'] == [attempt]
        assert result['known_cost_usd'] == '0.000019992'
        with pytest.raises(RuntimeError, match='Unsettled'):
            prepare(admission, third)


@pytest.mark.parametrize('file,error', [
    ('manifest', 'prepaid evidence changed'), ('balance', 'prepaid evidence changed'),
    ('response', 'Duplicate or changed saved'), ('journal', 'Missing or excess Decisions attempts'),
])
def test_retained_evidence_cannot_be_replaced_or_replayed(account, file, error):
    _, key, *_ = account
    with prepaid.account_lock() as fd:
        admission = open_admission(key, fd)
        first, second = admission.cells[:2]
        attempt, _ = send_stub(admission, first, 'gen-dec-evidence')
        paths = {
            'manifest': admission.root/'reviewed-manifest.json',
            'balance': next((admission.root/'prepaid-verification').iterdir()),
            'response': admission.responses/(attempt + '.json'),
            'journal': admission.root/'receipts.jsonl',
        }
        if file == 'response':
            paths[file].write_bytes(paths[file].read_bytes().replace(b'1.9992e-05', b'0'))
        else:
            paths[file].write_bytes(b'')
        with pytest.raises(ValueError, match=error):
            prepare(admission, second)


def test_exact_decimal_usage_and_typed_choice():
    cell = next(cell for cell in plan.render_plan(SEED).cells if cell.arm in plan.ROUTES)
    raw = plan.wire_bytes(response(cell, 'gen-dec-test')).replace(b'1.9992e-05', b'0.0000000000000000000123456789')
    item, verdict = validate_response(cell, 'attempt-1', status=200, complete=True, body=raw)
    assert item['body_sha256'] == sha256(raw).hexdigest()
    assert item['request_sha256'] == cell.digest
    assert item['attempt_id'] == 'attempt-1' and item['generation_id'] == 'gen-dec-test'
    assert item['usage'] == {'cost': '1.23456789E-20', 'prompt_tokens': 147, 'completion_tokens': 1}
    assert item['reported_byok'] is None
    assert verdict == plan.Verdict('valid', 'correct')
    zero = raw.replace(b'0.0000000000000000000123456789', b'0')
    item, _ = validate_response(cell, 'attempt-2', status=200, complete=True, body=zero)
    assert item['reported_zero'] is True and item['usage']['cost'] == '0'
    score = {'criterion': {'type': 'score', 'score': 1}}
    assert plan.decisions_verdict({'answers': score}) == plan.Verdict('invalid')
    assert plan.decisions_verdict({'answers': {'foreign': score}}) == plan.Verdict('invalid')
    assert plan.decisions_verdict({'answers': {'criterion': {'type': 'refusal'}}}) == plan.Verdict('abstained')
    invalid_probability = {'type': 'choice', 'choice': 'correct', 'probabilities': {'correct': True}}
    assert plan.decisions_verdict({'answers': {'criterion': invalid_probability}}) == plan.Verdict('invalid')
    assert plan.chat_verdict('{"verdicts":[{"id":"criterion","verdict":"abstain","reason":"refused"}]}') == plan.Verdict('abstained')
    assert plan.chat_verdict('{"verdicts":[{"id":"criterion","verdict":"correct","reason":""}]}') == plan.Verdict('invalid')


def test_existing_transport_does_not_enable_decisions(monkeypatch):
    monkeypatch.setenv('PACBENCH_DIRECT', '1')
    monkeypatch.setenv('PACBENCH_DIRECT_UPSTREAM_KEY', 'synthetic-not-a-real-key')
    with pytest.raises(RuntimeError, match='Unsupported direct model endpoint'):
        direct_http.admitted(plan.DECISIONS_URL, {'Authorization': 'Bearer pbdirect-judge'}, b'{}')
