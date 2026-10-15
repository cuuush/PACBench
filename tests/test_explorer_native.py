"""Saved-run reading and serving; these synthetic traces do not measure accuracy."""
import json
import shutil
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pacbench import explorer_data as explorer
from pacbench.frozen import write_bundle


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def trial(tmp_path):
    root = tmp_path / 'runs' / 'trial'
    root.mkdir(parents=True)
    pinned = write_bundle({'lives': {'example': {'messages': [
        {'source_id': 'm1', 'text': 'Synthetic'}]}}}, root / 'bundle.json')
    save(root / 'summary.json', {'mode': 'native-hermes', 'bundle_hash': pinned,
                                'source': 'preparation'})
    save(root / 'preparation/spec.json', {'id': 'Original author spec'})
    save(root / 'preparation/scenes.json', [{'scene': 'Synthetic chat'}])
    save(root / 'preparation/scene-verifications/one.json', {'passed': True})
    save(root / 'contract.json', {'question': 'What happened?', 'claims': [
        {'id': 'one', 'statement': 'First fact'}, {'id': 'two', 'statement': 'Second fact'}]})
    arm = root / 'repeat-1' / 'mem0'
    save(arm / 'recall.json', {'answer': 'First fact', 'tool_calls': []})
    save(arm / 'grade.json', {'claims': [
        {'id': 'one', 'verdict': 'correct'}, {'id': 'two', 'verdict': 'missed'}]})
    save(arm / 'followups/recovery.json', {'attempts': [
        {'claim_ids': ['two'], 'metrics': {'operations': []}}]})
    save(arm / 'followups/combined/turn.json', {'answer': 'Second fact', 'tool_calls': []})
    save(arm / 'followups/combined/grade.json', {'claims': [{'id': 'two', 'verdict': 'correct'}]})
    return root, arm


def test_native_judges_followup_subset_and_private_files(tmp_path):
    root, arm = trial(tmp_path)
    home = arm / 'hermes-home'
    home.mkdir()
    (home / '.env').write_text('SECRET=should-never-be-exported')
    save(root / 'model-calls/one.json', {'response': {'output': [{'encrypted_content': 'opaque-secret'}]}})
    save(root / 'model-calls/private/one.json', {'text': 'private-replay-secret'})
    result = explorer.native_run(root, tmp_path / 'runs')
    exchanges = result['arms']['mem0-repeat-1']['exchanges']
    assert len(exchanges) == 2
    assert [c['id'] for c in exchanges[1]['contract']['claims']] == ['two']
    assert exchanges[0]['grade']['claims'][1]['verdict'] == 'missed'
    exported = json.dumps(result)
    assert all(secret not in exported for secret in (
        'should-never-be-exported', 'opaque-secret', 'private-replay-secret'))
    save(root / 'summary.json', {'mode': 'native-hermes', 'bundle_hash': 'tampered'})
    with pytest.raises(ValueError, match='bundle mismatch'):
        explorer.native_run(root, tmp_path / 'runs')


def test_portable_run_displays_authoring_and_judges_without_private_homes(tmp_path):
    root, _ = trial(tmp_path)
    moved = tmp_path / 'different-checkout'
    shutil.move(str(root.parent), moved / 'runs')
    data = explorer.load_runs(moved)
    assert data['errors'] == []
    assert len(data['runs']) == 1
    run = data['runs'][0]
    assert run['spec']['id'] == 'Original author spec'
    assert run['scenes'] == [{'scene': 'Synthetic chat'}]
    assert run['verifications'][0]['record']['passed'] is True
    assert len(run['arms']['mem0-repeat-1']['exchanges']) == 2
    assert not list(moved.rglob('hermes-home'))


def test_refresh_reflects_edits_and_deletions_without_snapshot_fallback(tmp_path):
    root, arm = trial(tmp_path)
    save(tmp_path / 'explorer/latest-run.json', {'runs': [{'id': 'stale-snapshot'}]})
    assert explorer.load_runs(tmp_path)['runs'][0]['arms']['mem0-repeat-1']['turn']['answer'] == 'First fact'
    save(arm / 'recall.json', {'answer': 'Edited answer', 'tool_calls': []})
    assert explorer.load_runs(tmp_path)['runs'][0]['arms']['mem0-repeat-1']['turn']['answer'] == 'Edited answer'
    shutil.rmtree(root)
    assert explorer.load_runs(tmp_path)['runs'] == []
    assert not (tmp_path / 'explorer/data').exists()


def test_latest_run_uses_recorded_capture_time_after_checkout_changes_mtimes(tmp_path):
    import os
    root, arm = trial(tmp_path)
    newer=root.parent/'newer'
    shutil.copytree(root,newer)
    save(arm/'model-usage.json',{'at':'2026-10-10T09:00:00+00:00'})
    save(newer/'repeat-1/mem0/model-usage.json',{'at':'2026-10-10T11:00:00+00:00'})
    os.utime(root/'summary.json',(2000000000,2000000000))
    os.utime(newer/'summary.json',(1000000000,1000000000))
    assert [r['id'] for r in explorer.load_runs(tmp_path)['runs']]==['newer','trial']


def test_broken_run_does_not_hide_valid_results(tmp_path):
    trial(tmp_path)
    save(tmp_path / 'runs/broken/summary.json', {'mode': 'native-hermes'})
    (tmp_path / 'runs/broken/bundle.json').write_text('{')
    data = explorer.load_runs(tmp_path)
    assert [run['id'] for run in data['runs']] == ['trial']
    assert [error['run'] for error in data['errors']] == ['broken']


def test_preparation_source_cannot_read_outside_runs(tmp_path):
    root, _ = trial(tmp_path)
    summary = json.loads((root / 'summary.json').read_text())
    summary['source'] = '../../outside'
    save(root / 'summary.json', summary)
    data = explorer.load_runs(tmp_path)
    assert data['runs'] == []
    assert data['errors'][0]['error'] == 'preparation source must stay inside runs/'


def test_http_reads_runs_on_every_request_and_blocks_raw_private_paths(tmp_path, monkeypatch):
    import serve_explorer
    root, arm = trial(tmp_path)
    (tmp_path / 'explorer').mkdir()
    (tmp_path / 'explorer/index.html').write_text('Local UI')
    home = arm / 'hermes-home'
    home.mkdir()
    (home / '.env').write_text('PRIVATE=never-public')
    monkeypatch.setattr(serve_explorer, 'ROOT', tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), serve_explorer.ExplorerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        with urllib.request.urlopen(base + '/runs?at=1') as response:
            assert response.headers['Cache-Control'] == 'no-store'
            assert response.headers['Content-Type'].startswith('application/json')
            assert b'never-public' not in response.read()
        save(arm / 'recall.json', {'answer': 'New answer', 'tool_calls': []})
        with urllib.request.urlopen(base + '/runs') as response:
            data = json.load(response)
        assert data['runs'][0]['arms']['mem0-repeat-1']['turn']['answer'] == 'New answer'
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + '/runs/trial/repeat-1/mem0/hermes-home/.env')
        assert error.value.code == 404
        shutil.rmtree(root)
        with urllib.request.urlopen(base + '/runs') as response:
            assert json.load(response)['runs'] == []
        assert not (tmp_path / 'explorer/data').exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_parallel_component_records_with_identical_filenames_keep_exact_aliases(tmp_path):
    root,_=trial(tmp_path)
    summary=json.loads((root/'summary.json').read_text())
    summary['component_runs']=[{'original_root':'/original/core','public_root':'.'},
        {'original_root':'/original/honcho','public_root':'components/honcho'}]
    save(root/'summary.json',summary)
    save(root/'model-calls/same.json',{'text':'core judgment'})
    save(root/'components/honcho/model-calls/same.json',{'text':'honcho judgment'})
    run=explorer.native_run(root,tmp_path/'runs')
    records=run['model_calls'];assert len(records)==2
    lookup={alias:r['record']['text'] for r in records for alias in r['aliases']}
    assert lookup['/original/core/model-calls/same.json']=='core judgment'
    assert lookup['/original/honcho/model-calls/same.json']=='honcho judgment'
