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


def test_retired_provider_evidence_is_visible_and_labeled_historical(tmp_path):
    root,arm=trial(tmp_path)
    original=(arm/'grade.json').read_bytes()
    retired=arm.with_name('byterover');arm.rename(retired)
    result=explorer.native_run(root,tmp_path/'runs')
    saved=result['arms']['byterover-repeat-1']
    assert saved['provider_lifecycle']=='retired'
    assert 'historical only' in saved['label']
    assert saved['exchanges'][0]['turn']['answer']=='First fact'
    assert (retired/'grade.json').read_bytes()==original


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


def test_explorer_updates_turn_usage_from_saved_final_receipt_without_live_ledger(tmp_path):
    from pacbench.usage import window
    root,arm=trial(tmp_path)
    pending={'id':'one','role':'memory_provider','state':'reserved',
             'input_tokens':None,'output_tokens':None,'accounted_upper_bound_usd':.1}
    usage=window({'state':'captured','at':'before','calls':[]},
        {'state':'captured','at':'after','calls':[pending]})
    settled={**pending,'state':'settled','input_tokens':100,'output_tokens':20,
             'accounted_upper_bound_usd':.000014}
    save(arm/'usage.json',usage)
    save(arm/'model-usage.json',{'state':'captured','at':'2026-10-15T12:00:00+00:00','calls':[settled]})
    result=explorer.native_run(root,tmp_path/'runs')
    ex=result['arms']['mem0-repeat-1']['exchanges'][0]
    assert ex['usage']==usage
    current=ex['usage_after_settlement']['roles']['memory_provider']
    assert current['unsettled_calls']==0 and current['input_tokens']==100
    assert current['accounted_upper_bound_usd']==.000014
    assert json.loads((arm/'usage.json').read_text())==usage


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
    save(root/'model-calls/saved.json',{'model':'test-model','text':'Exact saved output',
        'request':{'messages':[{'role':'user','content':'Long saved prompt'}]}})
    save(root/'preparation/scene-verifications/check.json',{'model_call':'model-calls/saved.json',
        'scene_id':'check','judge_prompt':'Long saved prompt','verdict':{'supported':True}})
    save(home/'model-calls/secret.json',{'text':'never-public'})
    (root/'model-calls/link.json').symlink_to(home/'model-calls/secret.json')
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
        assert data['runs'][0]['arms']['mem0-repeat-1']['exchanges'][0]['turn']['answer'] == 'New answer'
        with urllib.request.urlopen(base+'/runs?index=1') as response:
            index=json.load(response)
        assert index['runs'][0]['id']=='trial' and 'messages' not in index['runs'][0]
        with urllib.request.urlopen(base+index['runs'][0]['detail_url']) as response:
            assert json.load(response)['runs'][0]['arms']['mem0-repeat-1']['exchanges'][0]['turn']['answer']=='New answer'
        with urllib.request.urlopen(base+'/bundle?run=trial') as response:
            assert json.load(response)['bundle_hash']==data['runs'][0]['bundle_hash']
        with urllib.request.urlopen(base+'/record?run=trial&file=repeat-1/mem0/recall.json') as response:
            assert json.load(response)['answer'] == 'New answer'
        (arm/'turns'/'linked').mkdir(parents=True)
        (arm/'turns'/'linked'/'turn.json').symlink_to(home/'model-calls/secret.json')
        for route in ('/bundle?run=..','/bundle?run=trial&file=hermes-home/.env','/run?run=..',
                      '/record?run=trial&file=repeat-1/mem0/hermes-home/.env',
                      '/record?run=trial&file=../hermes-home/.env',
                      '/record?run=trial&file=repeat-1/mem0/turns/linked/turn.json'):

            with pytest.raises(urllib.error.HTTPError) as blocked:urllib.request.urlopen(base+route)
            assert blocked.value.code==404
        calls=data['runs'][0]['model_calls']
        saved=next(c for c in calls if c['file']=='model-calls/saved.json')
        assert saved['record']['model']=='test-model'
        assert 'request' not in saved['record'] and 'text' not in saved['record']
        review=next(x['record'] for x in data['runs'][0]['verifications'] if x['record'].get('scene_id')=='check')
        assert review['review_index'] and 'judge_prompt' not in review
        assert review['verdict']=={'supported':True}
        with urllib.request.urlopen(base+saved['record']['lazy_url']) as response:
            assert json.load(response)['request']['messages'][0]['content']=='Long saved prompt'
        for name in ('../trial/model-calls/saved.json','repeat-1/mem0/hermes-home/model-calls/secret.json',
                     'model-calls/link.json','model-calls/private/secret.json'):
            from urllib.parse import urlencode
            with pytest.raises(urllib.error.HTTPError) as blocked:
                urllib.request.urlopen(base+'/model-call?'+urlencode({'run':'trial','file':name}))
            assert blocked.value.code==404
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


def test_excluded_attempt_answers_remain_inspectable_without_replacing_selected_arm(tmp_path):
    root,arm=trial(tmp_path)
    evidence='attempts/old/repeat-1/mem0'
    shutil.copytree(arm,root/evidence)
    save(root/evidence/'recall.json',{'answer':'Excluded answer','tool_calls':[]})
    save(root/evidence/'configuration-review.json',{'finding':'Non-default output limit'})
    summary=json.loads((root/'summary.json').read_text())
    summary['other_attempts']=[{'provider':'mem0','evidence':evidence,
        'original_root':'/old/trial','reason':'Different configuration'}]
    save(root/'summary.json',summary)
    run=explorer.load_runs(tmp_path,include_model_records=False)['runs'][0]
    assert list(run['arms'])==['mem0-repeat-1']
    assert run['arms']['mem0-repeat-1']['exchanges'][0]['turn']['answer']=='First fact'
    old=run['excluded_arms'][evidence]
    assert old['exchanges'][0]['turn']['answer']=='Excluded answer'
    assert old['configuration_review']['finding']=='Non-default output limit'
    assert 'excluded attempt' in old['label']
    summary['other_attempts'][0]['evidence']='../private'
    save(root/'summary.json',summary)
    assert explorer.load_runs(tmp_path)['errors'][0]['error']=='attempt evidence must stay inside run'


def test_unasked_check_exposes_native_readiness_without_full_receipt(tmp_path):
    root, arm = trial(tmp_path)
    save(root/'evaluation-plan.json', {'checks': [{'id': 'later', 'at': '2026-10-26T11:00:00-04:00'}]})
    save(root/'evaluations/later/contract.json', {'question': 'What changed?', 'claims': [{'id': 'x', 'statement': 'Sunday'}]})
    save(arm/'evaluation.json', {'checks': [{'id': 'later', 'at': '2026-10-26T11:00:00-04:00',
        'state': 'ungraded', 'reason': 'RuntimeError: write not ready'}]})
    save(arm/'turns/later/write-readiness.json', {'state': 'unavailable', 'dependencies': ['write'],
        'inputs': {'accepted_ids': ['doc-1']}, 'latency_s': 901,
        'observed': {'ready': False, 'reason': 'Native queued write readiness deadline exceeded',
                     'snapshots': [[{'id': 'doc-1', 'status': 'done', 'dreaming_status': 'dreaming'}]]},
        'large_private_history': 'not needed in main view'})
    data = explorer.load_runs(tmp_path, include_model_records=False)['runs'][0]
    check = data['arms']['mem0-repeat-1']['evaluation']['checks'][0]
    status = check['write_readiness']
    assert status['accepted_ids'] == ['doc-1'] and status['native_states'][0]['dreaming_status'] == 'dreaming'
    assert status['latency_s'] == 901 and 'large_private_history' not in status
    assert status['record_url'].startswith('/record?')
    assert data['arms']['mem0-repeat-1']['exchanges'] == []


def test_compact_run_keeps_grades_and_clips_only_browser_preview(tmp_path):
    root, arm = trial(tmp_path)
    save(arm/'recall.json', {'answer': 'First fact', 'tool_calls': [{'name':'search'}],
        'tool_results': [{'text': 'x' * 1000}]})
    save(arm/'judgment.json', {'question': 'Q', 'prompt':'secret-large-prompt', 'response':'full-result'})
    run = explorer.load_runs(tmp_path, include_model_records=False)['runs'][0]
    ex = run['arms']['mem0-repeat-1']['exchanges'][0]
    assert ex['grade']['claims'][1]['verdict'] == 'missed'
    assert len(ex['turn']['tool_results'][0]['text']) == 500 and ex['turn']['tool_results'][0]['preview_clipped']
    assert 'prompt' not in ex['judgment'] and ex['judgment_record_url'].startswith('/record?')
    assert len(json.loads((arm/'recall.json').read_text())['tool_results'][0]['text']) == 1000
