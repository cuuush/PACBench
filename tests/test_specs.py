import json
from pathlib import Path

import pytest

from pacbench.prepare import normalize_week, prepare, resolve_spec
from pacbench.specs import inspect_spec, main, read_spec


def small_spec():
    return json.loads(Path('specs/pizza_day.json').read_text())


def test_catalog_checks_sixty_specs_without_calls_or_output_files(tmp_path, monkeypatch, capsys):
    import pacbench.prepare as module
    monkeypatch.setattr(module, 'load_driver', lambda: pytest.fail('Validation called the model driver'))
    for index in range(60):
        spec = small_spec()
        spec['id'] = f'life-{index}'
        (tmp_path / f'{index:02}.json').write_text(json.dumps(spec))
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*')}
    assert main([str(tmp_path)]) == 0
    assert '60 valid specs; 0 errors' in capsys.readouterr().out
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*')} == before


def test_catalog_continues_past_bad_and_duplicate_specs(tmp_path, capsys):
    (tmp_path / '00-bad.json').write_text('{')
    spec = small_spec()
    for name in ('01-good', '02-duplicate'):
        (tmp_path / f'{name}.json').write_text(json.dumps(spec))
    assert main([str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert '01-good.json | 1 | 2 | 1 | low | 2026-09-07' in output
    assert 'Duplicate spec id' in output
    assert '1 valid specs; 2 errors' in output


def test_duplicate_json_keys_cannot_silently_drop_a_scene(tmp_path):
    path = tmp_path / 'bad.json'
    path.write_text('{"days": {"monday": {}, "monday": {}}}')
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        read_spec(path)


def test_authored_start_is_fixed_and_cli_override_is_explicit(tmp_path):
    spec = small_spec()
    _, _, resolved = resolve_spec(spec, tmp_path)
    assert resolved['week_start'] == '2026-09-07'
    assert resolved['checks'][0]['at'] == '2026-09-11T09:00:00-04:00'
    assert normalize_week(spec, '2026-10-26')['week_start'] == '2026-10-26'
    del spec['week_start']
    path = tmp_path / 'unpinned.json'
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match='never uses today'):
        inspect_spec(path)
    assert inspect_spec(path, week_start='2026-09-07')['start'] == '2026-09-07'


@pytest.mark.parametrize('channel', [
    {'type': 'carrier-pigeon', 'participants': ['Me', 'Devon']},
    {'type': 'imessage', 'participants': ['Me', {}]},
    {'type': 'imessage', 'participants': ['Me', 'Me']},
    {'type': 'imessage', 'participants': 'Me, Devon'},
    {'type': 'imessage', 'participants': ['Devon', 'Maya']},
])
def test_unused_bad_channel_stops_before_paid_generation(tmp_path, monkeypatch, channel):
    import pacbench.prepare as module
    monkeypatch.setattr(module, 'load_driver', lambda: pytest.fail('Invalid spec reached the model driver'))
    spec = small_spec()
    spec['channels']['unused'] = channel
    with pytest.raises((ValueError, RuntimeError)):
        prepare(spec, tmp_path / 'prepared')


def test_bad_thread_fails_before_an_earlier_good_scene_is_generated(tmp_path, monkeypatch):
    import pacbench.prepare as module
    monkeypatch.setattr(module, 'load_driver', lambda: pytest.fail('Invalid thread reached the model driver'))
    spec = {'question_at': '2026-09-11T09:00:00', 'scenes': [
        {'id': 'first', 'at': '2026-09-10T17:00:00', 'scene': 'They plan lunch.', 'facts': ['Lunch is Friday.']},
        {'id': 'second', 'thread': ' Different ', 'at': '2026-09-10T18:00:00', 'scene': 'They discuss work.'},
    ]}
    with pytest.raises(RuntimeError, match='thread must be'):
        prepare(spec, tmp_path / 'prepared')


@pytest.mark.parametrize('scene_id', ['../../escaped', '', '/absolute', 'nested/scene'])
def test_bad_scene_ids_stop_before_any_model_calls(tmp_path, monkeypatch, scene_id):
    import pacbench.prepare as module
    monkeypatch.setattr(module, 'load_driver', lambda: pytest.fail('Invalid id reached the model driver'))
    spec = small_spec()
    spec['days']['thursday']['scenes'][1]['id'] = scene_id
    with pytest.raises(RuntimeError, match='scene id must be'):
        prepare(spec, tmp_path / 'prepared')
