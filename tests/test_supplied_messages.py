"""Offline exact-source integrity; mocked reviews do not establish corpus quality."""
import copy
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pacbench.authoring import (AuthoringError, SceneJudgeError, ScenePlan,
    build_scene_observations, realize_scene, scene_spec)
from pacbench.followups import digest, save
from pacbench.model_calls import ModelOutput
from pacbench.prepare import (generation_spec, import_week, prepare,
    realization_binding, resolve_spec, reuse_realization)


BODY = "  Pizza Friday at six — confirmed.\nBring the 🍕 voucher.  "
AT = '2026-10-05T18:00:02-04:00'
PRIVATE = 'PRIVATE_AUTHOR_CONSTRAINT_NEVER_SOURCE'


def supplied_spec(count=1):
    messages = [{'sender': 'Sam', 'text': BODY, 'at': AT}]
    if count == 3:
        messages += [
            {'sender': 'Me', 'text': 'Got it!\nThanks.', 'at': '2026-10-05T18:00:17-04:00'},
            {'sender': 'Sam', 'text': 'See you then.', 'at': '2026-10-05T18:03:04-04:00'},
        ]
    return {'id': 'supplied-case', 'timezone': 'America/New_York', 'scenes': [{
        'id': 'exact', 'channel': 'imessage', 'thread': 'sam', 'participants': ['Me', 'Sam'],
        'at': '2026-10-05T18:00:00-04:00', 'messages': messages, 'facts': [PRIVATE]}]}


def forbidden_writer(*args, **kwargs):
    pytest.fail('Exact supplied source reached the writer')


def checker(root, calls, *, reject=False):
    """Write model-shaped offline receipts so the actual preparation audit runs."""
    def call(prompt):
        calls.append(prompt)
        if prompt.startswith('Independently audit this complete raw personal-message corpus'):
            verdict = {'consistent': True, 'questions_answerable': True,
                       'natural_enough': True, 'issues': [], 'assessments': []}
        else:
            verdict = {'supported': not reject, 'evidence': [1],
                       'problem': 'Missing author requirement' if reject else ''}
            if 'numbered required fact' in prompt:
                verdict['facts'] = [{'index': 1, 'supported': not reject, 'evidence': [1],
                                     'problem': verdict['problem']}]
        text = json.dumps(verdict)
        path = root / 'model-calls' / f'offline-{len(calls)}.json'
        save(path, {'status': 'ok', 'model': 'offline-stub', 'text': text,
                    'request': {'messages': [{'role': 'user', 'content': prompt}]}})
        return ModelOutput(text, 'offline-stub', {}, str(path))
    return call


@pytest.mark.parametrize('count', [1, 3])
def test_supplied_source_preserves_exact_lines_clocks_and_checker(count, tmp_path):
    spec = supplied_spec(count)
    plans, _, resolved = resolve_spec(spec, tmp_path)
    plan = plans[0]
    assert plan.scene == ''  # Source-only scenes need no writer description.
    assert plan.message_range is None and plan.messages_per_person is None
    calls = []
    realization = realize_scene(plan, forbidden_writer, checker(tmp_path, calls),
                                record_dir=tmp_path / 'scene-verifications')
    original = spec['scenes'][0]['messages']
    assert realization.lines == tuple((m['sender'], m['text']) for m in original)
    assert len(calls) == 1 and f'[{AT}] Sam: {BODY}' in calls[0]
    assert realization.record.source_mode == 'supplied'
    assert realization.record.attempts == 0 and realization.record.judge_calls == 1
    assert realization.record.generation_paths == [] and realization.record.writer_notes == []
    messages = [o.public() for o in build_scene_observations('life', plan, list(realization.lines))]
    for actual, expected in zip(messages, original):
        assert actual['text'] == expected['text'] and actual['sender'] == expected['sender']
        assert actual['written_at'] == actual['arrived_at'] == actual['collected_at'] == expected['at']
        assert actual['channel'] == 'imessage'
        assert actual['from_owner'] is (expected['sender'] == 'Me')
        assert actual['structured']['participants'] == ['Me', 'Sam']
        assert actual['thread'].startswith('thread-') and actual['source_id'].startswith('message-')
    assert PRIVATE not in json.dumps(messages)
    assert resolved['scenes'][0]['messages'] == original
    assert json.loads(json.dumps(scene_spec(plan))) == scene_spec(plan)
    assert scene_spec(plan)['prompt_version'] == 'scene-supplied-v1'
    if count == 1:
        assert not messages[0]['from_owner']  # An incoming message does not need a fake owner reply.


@pytest.mark.parametrize('mutation', [
    'empty', 'null', 'not-list', 'extra-field', 'bad-sender', 'blank-text',
    'missing-clock', 'naive-clock', 'bad-clock', 'wrong-offset', 'outside-window',
    'reverse', 'equal-clocks', 'duplicate-body', 'quota', 'no-members',
])
def test_invalid_supplied_messages_stop_before_model_setup(tmp_path, monkeypatch, mutation):
    import pacbench.prepare as module
    spec = supplied_spec(3)
    scene = spec['scenes'][0]
    messages = scene['messages']
    if mutation == 'empty': scene['messages'] = []
    elif mutation == 'null': scene['messages'] = None
    elif mutation == 'not-list': scene['messages'] = 'Sam: Hello'
    elif mutation == 'extra-field': messages[0]['facts'] = [PRIVATE]
    elif mutation == 'bad-sender': messages[0]['sender'] = 'Stranger'
    elif mutation == 'blank-text': messages[0]['text'] = ' \n '
    elif mutation == 'missing-clock': del messages[0]['at']
    elif mutation == 'naive-clock': messages[0]['at'] = '2026-10-05T18:00:02'
    elif mutation == 'bad-clock': messages[0]['at'] = 'not a timestamp'
    elif mutation == 'wrong-offset': messages[0]['at'] = '2026-10-05T18:00:02-05:00'
    elif mutation == 'outside-window': messages[0]['at'] = '2026-10-05T18:30:02-04:00'
    elif mutation == 'reverse': scene['messages'] = list(reversed(messages))
    elif mutation == 'equal-clocks': messages[1]['at'] = messages[0]['at']
    elif mutation == 'duplicate-body': messages[1]['text'] = messages[0]['text']
    elif mutation == 'quota': scene['message_range'] = [2, 5]
    elif mutation == 'no-members': del scene['participants']
    monkeypatch.setattr(module, 'load_driver', forbidden_writer)
    with pytest.raises((AuthoringError, ValueError)):
        prepare(spec, tmp_path / 'invalid')


def test_week_channels_and_generated_quotas_remain_independent(tmp_path):
    spec = {'week_start': '2026-10-05', 'noise': 'medium',
            'channels': {'sam': {'type': 'imessage', 'participants': ['Me', 'Sam']}},
            'days': {day: {} for day in ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')}}
    spec['days']['monday']['scenes'] = [
        {'id': 'exact', 'channel': 'sam', 'time': '6 pm',
         'messages': supplied_spec()['scenes'][0]['messages'], 'facts': [PRIVATE]},
        {'id': 'generated', 'channel': 'sam', 'time': '7 pm', 'scene': 'A complex conversation.', 'facts': []},
    ]
    spec['days']['tuesday']['conversations'] = [{'id': 'recall', 'time': '9 am', 'turns': [
        {'id': 'check', 'prompt': 'What is the pizza plan?', 'expect': 'Friday at six.'}]}]
    plans, _, _ = resolve_spec(spec, tmp_path)
    assert plans[0].messages and plans[0].message_range is None
    assert plans[1].messages is None and plans[1].message_range == (40, 80)
    assert plans[0].channel == 'imessage' and plans[0].participants == ('Me', 'Sam')


def test_supplied_changes_invalidate_cache_and_line_edits_are_rejected(tmp_path):
    plans, _, _ = resolve_spec(supplied_spec(), tmp_path)
    plan = plans[0]
    changed_text = replace(plan, messages=[{**plan.messages[0], 'text': 'New text.'}])
    changed_time = replace(plan, messages=[{**plan.messages[0], 'at': '2026-10-05T18:00:03-04:00'}])
    for changed in (changed_text, changed_time):
        assert generation_spec(changed) != generation_spec(plan)
        assert realization_binding(changed, [(m['sender'], m['text']) for m in changed.messages], ()) != realization_binding(plan, [('Sam', BODY)], ())
    with pytest.raises(AuthoringError, match='differ from exact'):
        build_scene_observations('life', plan, [('Sam', BODY.strip())])


@pytest.mark.parametrize('resume', [False, True])
def test_rejected_supplied_source_is_not_rewritten_or_rejudged(tmp_path, monkeypatch, resume):
    import pacbench.prepare as module
    spec = supplied_spec()
    root = tmp_path / 'rejected'
    calls = []
    monkeypatch.setenv('META_API_KEY', 'offline-test')
    monkeypatch.setattr(module, 'load_driver', lambda: SimpleNamespace(model_id='offline-stub'))
    monkeypatch.setattr(module, 'client_settings', lambda _: ('http://offline.test/v1', 'offline-test'))
    monkeypatch.setattr(module, 'ResponsesWriter', forbidden_writer)
    monkeypatch.setattr(module, 'ModelClient', lambda **_: checker(root, calls, reject=True))
    if resume:
        plans, _, resolved = resolve_spec(spec, root)
        plan = plans[0]
        # Valid cached text with an obsolete verification binding must be rechecked once.
        record = {'scene_id': plan.id, 'status': 'ok', 'source_mode': 'supplied',
                  'attempts': 0, 'judge_calls': 1}
        previous = {'lines': [['Sam', BODY]], 'record': record, 'verdict': {'supported': True},
                    'verification_binding': 'stale-context'}
        save(root / 'resolved-spec.json', resolved)
        save(root / 'model-cache/scene-realizations.json', {
            plan.id: {'spec': scene_spec(plan), 'realization': previous}})
    result = prepare(spec, root, resume=resume)
    assert result['state'] == 'incomplete' and len(calls) == 1
    assert 'Missing author requirement' in result['errors'][0]['reason']
    assert not (root / 'week.json').exists()
    rejected = list((root / 'scene-verifications').glob('*.json'))
    assert len(rejected) == 1 and json.loads(rejected[0].read_text())['passed'] is False


def fake_contract(**kwargs):
    """Only contract authoring is stubbed; normal freeze and audits still execute."""
    from pacbench.semantic_contract import load_contract
    messages = kwargs['observations']
    body = {'claims': [{'id': 'fact-1', 'statement': 'Pizza Friday at six.',
                       'evidence': [messages[0]['source_id']]}],
            'question': 'What is the pizza plan?', 'evidence_hash': digest(messages)}
    save(kwargs['output_path'], {**body, 'contract_hash': digest(body)})
    return load_contract(kwargs['output_path'])


@pytest.mark.parametrize('count', [1, 3])
@pytest.mark.parametrize('mixed', [False, True])
def test_supplied_scene_uses_normal_freeze_portable_audit_and_native_sources(tmp_path, monkeypatch, count, mixed):
    import pacbench.prepare as module
    from pacbench.document_ingestion import prepare_sources
    from pacbench.imessage_fixture import apple_ns
    from pacbench.native_pilot import read_prepared
    from pacbench.preparation_audit import audit_prepared, audit_scenes
    root = tmp_path / 'prepared'
    spec = supplied_spec(count)
    if mixed:
        spec['test_scene_id'] = 'exact'
        spec['scenes'].append({'id': 'generated', 'channel': 'imessage', 'thread': 'sam',
            'participants': ['Me', 'Sam'], 'at': '2026-10-06T18:00:00-04:00',
            'scene': 'They discuss a movie.', 'facts': []})
    calls, writer_calls = [], []
    def writer(prompt):
        writer_calls.append(prompt)
        assert BODY in prompt and AT[:10] in prompt
        return 'Sam: That movie ending was odd.\nMe: I liked it.'
    monkeypatch.setenv('META_API_KEY', 'offline-test')
    monkeypatch.setattr(module, 'load_driver', lambda: SimpleNamespace(model_id='offline-stub'))
    monkeypatch.setattr(module, 'client_settings', lambda _: ('http://offline.test/v1', 'offline-test'))
    monkeypatch.setattr(module, 'ResponsesWriter', (lambda **_: writer) if mixed else forbidden_writer)
    monkeypatch.setattr(module, 'ModelClient', lambda **_: checker(root, calls))
    monkeypatch.setattr(module, 'build_contract', fake_contract)
    result = prepare(spec, root)
    assert result['state'] == 'prepared'
    assert len(writer_calls) == int(mixed)
    assert len(calls) == 2 + int(mixed)  # Each scene check, then the real corpus-review path.
    assert audit_prepared(root)['state'] == 'pass'
    assert (root / 'realism-review.json').exists() and (root / 'week.json').exists()
    received, _, resolved = read_prepared(root, 100)
    expected = spec['scenes'][0]['messages']
    assert [(m['sender'], m['text'], m['written_at']) for m in received[:count]] == [
        (m['sender'], m['text'], m['at']) for m in expected]
    assert PRIVATE not in json.dumps(received)
    assert all('messages' not in m and 'facts' not in m for m in received)
    # Frozen replay uses exactly the same source without constructing any model.
    monkeypatch.setattr(module, 'load_driver', forbidden_writer)
    copied = import_week(root / 'week.json', tmp_path / 'copied')
    replayed, _, _ = read_prepared(copied, 100)
    assert replayed == received
    output = tmp_path / 'native-sources'
    schedule = prepare_sources(output, replayed, 'America/New_York')
    source = output / 'sources' / schedule[0]['id']
    assert json.loads((source / 'messages.json').read_text()) == received[:count]
    with sqlite3.connect(source / 'chat.db') as conn:
        rows = conn.execute('SELECT text, date, is_from_me FROM message ORDER BY date').fetchall()
    assert rows == [(m['text'], apple_ns(m['at']), int(m['sender'] == 'Me')) for m in expected]
    tampered = copy.deepcopy(received)
    tampered[0]['written_at'] = '2026-10-05T18:00:03-04:00'
    assert 'Frozen source bodies or metadata differ from verified scenes' in audit_scenes(root, resolved, 'supplied-case', tampered)
    # Exact sources cannot use the legacy missing-checker-binding escape hatch.
    scenes = json.loads((root / 'scenes.json').read_text())
    scenes['scenes'][0].pop('verification_binding')
    save(root / 'scenes.json', scenes)
    assert any('missing its checker binding' in e for e in audit_prepared(root)['errors'])


def test_next_day_single_reply_is_checked_against_actual_prior_source(tmp_path, monkeypatch):
    import pacbench.prepare as module
    from pacbench.document_ingestion import daily_batches
    from pacbench.native_pilot import read_prepared
    spec = supplied_spec()
    first = spec['scenes'][0]
    first['messages'][0]['text'] = 'Pizza Friday at six?'
    spec['test_scene_id'] = 'reply'
    spec['scenes'].append({
        'id': 'reply', 'channel': 'imessage', 'thread': 'sam', 'participants': ['Me', 'Sam'],
        'at': '2026-10-06T09:00:00-04:00', 'facts': ['The user accepts the proposal.'],
        'messages': [{'sender': 'Me', 'text': 'Yes, that works.', 'at': '2026-10-06T09:00:03-04:00'}],
    })
    root = tmp_path / 'next-day'
    calls = []
    monkeypatch.setenv('META_API_KEY', 'offline-test')
    monkeypatch.setattr(module, 'load_driver', lambda: SimpleNamespace(model_id='offline-stub'))
    monkeypatch.setattr(module, 'client_settings', lambda _: ('http://offline.test/v1', 'offline-test'))
    monkeypatch.setattr(module, 'ResponsesWriter', forbidden_writer)
    monkeypatch.setattr(module, 'ModelClient', lambda **_: checker(root, calls))
    monkeypatch.setattr(module, 'build_contract', fake_contract)
    assert prepare(spec, root)['state'] == 'prepared'
    assert len(calls) == 3
    assert AT in calls[1] and 'Sam: Pizza Friday at six?' in calls[1]
    assert '[2026-10-06T09:00:03-04:00] Me: Yes, that works.' in calls[1]
    messages, _, _ = read_prepared(root, 100)
    assert len({m['thread'] for m in messages}) == 1
    assert [day for day, _ in daily_batches(messages, 'America/New_York')] == ['2026-10-05', '2026-10-06']


def test_supplied_single_email_preserves_quoted_body_and_sender(tmp_path):
    from email import message_from_bytes
    from email.policy import default
    from pacbench.document_ingestion import prepare_sources
    spec = supplied_spec()
    source = spec['scenes'][0]
    source.update(channel='email', email_subject='Dinner')
    source['messages'][0]['text'] = 'Saturday works.\n\nOn Monday Me wrote:\n> Thursday instead?'
    plan = resolve_spec(spec, tmp_path)[0][0]
    calls = []
    result = realize_scene(plan, forbidden_writer, checker(tmp_path, calls))
    messages = [m.public() for m in build_scene_observations('mail', plan, list(result.lines))]
    output = tmp_path / 'native'
    prepare_sources(output, messages, 'America/New_York')
    stored = json.loads((output / 'sources/2026-10-05/mailbox.json').read_text())['messages'][0]
    assert stored['source'] == messages[0]
    assert stored['folder'] == 'INBOX' and stored['source']['sender'] == 'Sam'
    parsed = message_from_bytes(stored['rfc822'].encode('utf-8'), policy=default)
    assert parsed.get_content().splitlines() == source['messages'][0]['text'].splitlines()


def test_supplied_cache_reuses_exact_review_and_rejects_edited_cached_lines(tmp_path):
    plan = resolve_spec(supplied_spec(), tmp_path)[0][0]
    result = realize_scene(plan, forbidden_writer, checker(tmp_path, []))
    previous = {'lines': result.lines, 'record': vars(result.record), 'verdict': result.verdict,
                'verification_binding': realization_binding(plan, result.lines, ())}
    reused = reuse_realization(plan, previous, forbidden_writer, model='offline-stub',
                               record_dir=tmp_path, verification_lines=())
    assert reused.lines == result.lines
    previous['lines'] = (('Sam', BODY.strip()),)
    with pytest.raises(AuthoringError, match='differ from exact'):
        reuse_realization(plan, previous, forbidden_writer, model='offline-stub',
                          record_dir=tmp_path, verification_lines=())
