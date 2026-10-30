import json
from types import SimpleNamespace

import pytest

from pacbench import review_rebased as rb
from pacbench import prepare as prepare_module


def fixture_inputs(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'summary.json').write_text(json.dumps({'bundle_hash': 'original'}))
    (source / 'scenes.json').write_text(json.dumps({'scenes': [
        {'scene_id': 'one', 'lines': [['Me', 'Old date'], ['Sam', 'Okay']]},
        {'scene_id': 'two', 'lines': [['Sam', 'Another message'], ['Me', 'Yes']]},
    ]}))
    spec = {'id': 'life', 'timezone': 'America/New_York', 'noise': 'low',
        'test_scene_id': 'one', 'question_at': '2026-10-30T13:30:00-04:00',
        'scenes': [
            {'id': 'one', 'scene': 'A plan', 'channel': 'imessage', 'thread': 'sam',
             'participants': ['Me', 'Sam'], 'facts': ['A plan'], 'at': '2026-10-28T10:00:00-04:00'},
            {'id': 'two', 'scene': 'A later chat', 'channel': 'imessage', 'thread': 'sam',
             'participants': ['Me', 'Sam'], 'facts': [], 'at': '2026-10-29T10:00:00-04:00'}],
        'checks': [{'id': 'q', 'prompt': 'When?', 'facts': ['A plan'], 'at': '2026-10-30T13:30:00-04:00'}]}
    edits = [{'scene_id': 'one', 'line': 0, 'before': 'Old date', 'after': 'New date'}]
    return source, spec, edits


def test_rebase_preserves_text_and_exports_provenance(tmp_path, monkeypatch):
    source, spec, edits = fixture_inputs(tmp_path)
    output = tmp_path / 'out'
    monkeypatch.setattr(rb, 'audit_prepared', lambda root: {'state': 'pass', 'errors': []})
    monkeypatch.setattr(prepare_module, 'audit_prepared', lambda root: {'state': 'pass', 'errors': []})
    monkeypatch.setattr(rb, 'require_live_cost_boundary', lambda: {})
    monkeypatch.setattr(rb, 'load_driver', lambda: SimpleNamespace(model_id='mock'))
    monkeypatch.setattr(rb, 'client_settings', lambda _: ('http://mock', 'token'))
    monkeypatch.setenv('META_API_KEY', 'token')
    monkeypatch.setattr(rb, 'ModelClient', lambda **kw: object())
    prior_seen = []
    def judge(plan, lines, *args, prior_lines, **kwargs):
        prior_seen.append((plan.id, list(lines), prior_lines))
        return {'supported': True}, 0, 0, None
    monkeypatch.setattr(rb, 'judge_scene', judge)
    def freeze(resolved, messages, target, model, root, **kwargs):
        p = root / 'evaluations' / 'q'
        p.mkdir(parents=True, exist_ok=True)
        (p / 'contract.json').write_text('{}')
        return {'checks': [{'id': 'q', 'contract_hash': 'contract'}], 'plan_hash': 'plan'}
    monkeypatch.setattr(rb, 'freeze_evaluations', freeze)
    monkeypatch.setattr(rb, 'load_contract', lambda _: SimpleNamespace(contract_hash='contract'))
    monkeypatch.setattr(rb, 'review_corpus', lambda *a: None)
    monkeypatch.setattr(rb, 'freeze_policy', lambda *a: None)
    rb.prepare_rebased(source, spec, edits, output)
    messages = json.loads((output / 'bundle.json').read_text())['lives']['life']['messages']
    assert [m['text'] for m in messages] == ['New date', 'Okay', 'Another message', 'Yes']
    assert prior_seen[1][2][0][-1] == 'New date'
    manifest = json.loads((output / 'week.json').read_text())
    assert 'rebase-provenance.json' in manifest['files']
    assert json.loads((output / 'rebase-provenance.json').read_text())['edits'] == edits


def test_bad_edit_stops_before_gateway_and_resume_preserves_rejection(tmp_path, monkeypatch):
    source, spec, edits = fixture_inputs(tmp_path)
    monkeypatch.setattr(rb, 'audit_prepared', lambda root: {'state': 'pass', 'errors': []})
    monkeypatch.setattr(rb, 'require_live_cost_boundary', lambda: pytest.fail('must not reach gateway'))
    edits[0]['before'] = 'wrong'
    with pytest.raises(ValueError, match='original full message'):
        rb.prepare_rebased(source, spec, edits, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_resume_does_not_overwrite_rejected_review(tmp_path, monkeypatch):
    source, spec, edits = fixture_inputs(tmp_path)
    output = tmp_path / 'out'
    output.mkdir()
    _, _, resolved = rb.resolve_spec(spec, output)
    (output / 'spec.json').write_text(json.dumps(spec))
    (output / 'resolved-spec.json').write_text(json.dumps(resolved))
    (output / 'rebase-provenance.json').write_text(json.dumps({
        'source': str(source.resolve()), 'source_bundle_hash': 'original', 'edits': edits}))
    rejected = '{"state":"fail","review":{"issues":["bad dates"]}}'
    (output / 'realism-review.json').write_text(rejected)
    monkeypatch.setattr(rb, 'audit_prepared', lambda root: {'state': 'pass', 'errors': []})
    monkeypatch.setattr(rb, 'require_live_cost_boundary', lambda: pytest.fail('must not reach gateway'))
    with pytest.raises(ValueError, match='Rejected realism review'):
        rb.prepare_rebased(source, spec, edits, output, resume=True)
    assert (output / 'realism-review.json').read_text() == rejected
