from pacbench.usage import provider_model_health, snapshot
import sqlite3


def call(number, *, project='provider', role='memory_provider', status=400, finished='done'):
    return {'id': str(number), 'project': project, 'role': role,
            'http_status': status, 'finished': finished, 'state': 'uncertain',
            'accounted_upper_bound_usd': .1}


def test_repeated_provider_bad_requests_stop_without_releasing_costs():
    health = provider_model_health({'state': 'captured', 'calls': [call(n) for n in range(3)]})
    assert health['state'] == 'stop'
    assert len(health['failed_requests']) == 3
    assert sum(c['accounted_upper_bound_usd'] for c in health['failed_requests']) > .29


def test_fallback_pending_missing_usage_other_roles_and_projects_do_not_stop():
    calls = [call(1), call(2, status=422), call(3, finished=None),
             call(4, status=200), call(5, role='hermes'), call(6, project='other')]
    assert provider_model_health({'state': 'captured', 'calls': calls})['state'] == 'captured'
    assert provider_model_health({'state': 'unavailable'})['state'] == 'unavailable'


def test_snapshot_includes_actual_http_status(tmp_path):
    with sqlite3.connect(tmp_path/'ledger.sqlite3') as db:
        db.execute('CREATE TABLE calls(id,project,model,endpoint,started,finished,state,input_tokens,output_tokens,accounted,reserved,http_status)')
        db.execute("INSERT INTO calls VALUES('bad','provider','spark','responses','t0','t1','uncertain',NULL,NULL,100000000,100000000,400)")
    capture = snapshot({'provider': 'memory_provider'}, tmp_path)
    assert capture['state'] == 'captured'
    assert capture['calls'][0]['http_status'] == 400


def test_cost_stop_invokes_only_the_failed_arms_native_shutdown(tmp_path, monkeypatch):
    from pacbench import native_pilot, process_cleanup
    first, other = tmp_path/'first', tmp_path/'other'
    stopped, cleaned = [], []
    monkeypatch.setattr(native_pilot, '_ARM_SHUTDOWNS', {
        first.resolve(): lambda: stopped.append('first'),
        other.resolve(): lambda: stopped.append('other')})
    monkeypatch.setattr(process_cleanup, 'cleanup_run_processes', lambda root: cleaned.append(root))
    native_pilot.stop_arm_model_work(first)
    native_pilot.stop_arm_model_work(first)
    assert stopped == ['first']
    assert cleaned == [first, first]
    assert other.resolve() in native_pilot._ARM_SHUTDOWNS
