import json

import pytest

from pacbench.native_pilot import ingest_pending
from pacbench.stop import OperatorStop, check_stop


def test_stop_after_first_native_day_preserves_receipt_and_skips_second(tmp_path):
    stop = tmp_path/'STOP'
    root = tmp_path/'arm'
    days = [{'id': day, 'at': day, 'file': f'ingestion/{day}.json'} for day in ('one', 'two')]
    ingested, calls = [], []
    def ingest(_provider, source, *_args):
        calls.append(source.name)
        stop.touch()
        return {'state': 'ready', 'ready': True}
    with pytest.raises(OperatorStop):
        ingest_pending(days, ingested, set(), tmp_path, root, 'memcal', {},
                       'America/New_York', stop, ingest, lambda: None)
    assert calls == ['one'] and ingested == ['one']
    assert json.loads((root/'ingestion/one.json').read_text()) == {'state': 'ready', 'ready': True}
    assert not (root/'ingestion/two.json').exists()


def test_connector_stops_before_dispatch(tmp_path, monkeypatch, capsys):
    from provider_adapters import document_connector
    stop = tmp_path/'STOP'
    stop.touch()
    monkeypatch.setenv('PACBENCH_STOP_FILE', str(stop))
    assert document_connector.main(['memcal', 'src', 'root', 'at', 'at']) == 75
    assert json.loads(capsys.readouterr().out) == {'operator_stop': True}


def test_stop_checks_environment_and_explicit_path(tmp_path, monkeypatch):
    stop = tmp_path/'shared-stop'
    stop.touch()
    monkeypatch.setenv('PACBENCH_STOP_FILE', str(stop))
    with pytest.raises(OperatorStop):
        check_stop(tmp_path/'absent')


@pytest.mark.parametrize('mode',['operator',401,403])
def test_terminal_first_arm_does_not_start_independent_arm(tmp_path, monkeypatch,mode):
    from types import SimpleNamespace
    from pacbench import native_pilot, document_ingestion, native_audit, usage, live_trials
    monkeypatch.setattr(live_trials, 'require_live_trials_ready', lambda: None)
    source = tmp_path/'prepared'
    source.mkdir()
    for name in ('bundle.json', 'contract.json', 'resolved-spec.json', 'evaluation-plan.json'):
        (source/name).write_text('{}')
    (source/'summary.json').write_text('{"bundle_hash":"bundle"}')
    (source/'evaluations').mkdir()
    output = tmp_path/'run'
    check = {'id': 'one', 'at': '2026-10-01T12:00:00-04:00'}
    monkeypatch.setenv('META_API_KEY', 'test')
    monkeypatch.setattr(native_pilot, 'provider_names', lambda: ['memcal', 'mem0'])
    monkeypatch.setattr(native_pilot, 'read_prepared', lambda *_: ([], SimpleNamespace(contract_hash='contract'), {'timezone':'America/New_York'}))
    monkeypatch.setattr(native_pilot, 'load_evaluations', lambda *_: ({'plan_hash':'plan','context':'shared'}, [(check, object(), {})]))
    monkeypatch.setattr(native_pilot, 'require_live_cost_boundary', lambda: {})
    monkeypatch.setattr(native_pilot, 'load_driver', lambda: SimpleNamespace(model_id='test'))
    monkeypatch.setattr(native_pilot, 'client_settings', lambda *_: ('http://localhost', 'test'))
    monkeypatch.setattr(native_pilot, 'ModelClient', lambda **_: lambda *_: None)
    monkeypatch.setattr(native_pilot, 'freeze_policy', lambda *_: {'plan_hash':'followup'})
    monkeypatch.setattr(native_pilot, 'probe', lambda *_args, **_kw: {'available':True,'tools':[]})
    monkeypatch.setattr(native_pilot, 'smoke_succeeded', lambda *_: True)
    monkeypatch.setattr(native_pilot, 'policy_record', lambda *_: {'allowed_tools':[], 'source_argument_rules':{}})
    monkeypatch.setattr(native_pilot, 'stop_arm_model_work', lambda *_: None)
    started = []
    monkeypatch.setattr(native_pilot, 'provision', lambda root, provider, *_a, **_kw: started.append(provider) or {'PACBENCH_USAGE_ROLES':'{}'})
    monkeypatch.setattr(document_ingestion, 'prepare_sources', lambda *_: [{'id':'one','at':'a','file':'ingestion/one.json'}, {'id':'two','at':'b','file':'ingestion/two.json'}])
    monkeypatch.setattr(document_ingestion, 'validate_evaluation_clocks', lambda *_a, **_kw: None)
    monkeypatch.setattr(document_ingestion, 'ready_sources', lambda schedule, *_: schedule)
    def ingest(*_):
        if mode=='operator':
            (output/'STOP').touch()
            return {'state':'ready','ready':True}
        return {'state':'failed','ready':False,'result':{'native_error':{
            'http_status':mode,'authentication_blocked':True}}}
    monkeypatch.setattr(document_ingestion, 'ingest_day', ingest)
    monkeypatch.setattr(usage, 'snapshot', lambda *_: {})
    monkeypatch.setattr(usage, 'provider_model_health', lambda *_: {'state':'captured'})
    monkeypatch.setattr(native_audit, 'audit', lambda *_: {'state':'fail','errors':['interrupted arm']})
    def checks(_checks, _context, root, _judge, _allowlist, _env, **kwargs):
        from pacbench.gateway_client import ModelAccessBlocked
        try:
            kwargs['before_check'](check, root/'turns/one')
        except OperatorStop:
            return {'state':'interrupted','checks':[{'id':'one','state':'interrupted'}]}
        except ModelAccessBlocked as error:
            return {'state':'access_blocked','reason':str(error),
                    'checks':[{'id':'one','state':'ungraded','verdict':None}]}
        pytest.fail('stop was not honored')
    monkeypatch.setattr(native_pilot, 'run_checks', checks)
    report = native_pilot.run(source, output, providers=['memcal','mem0'])
    assert report['state'] == ('interrupted' if mode=='operator' else 'incomplete') and started == ['memcal']
    assert json.loads((output/'repeat-1/memcal/ingestion/one.json').read_text())['ready'] is (mode=='operator')
    if mode!='operator':assert report['access_stop']
    assert not (output/'repeat-1/memcal/ingestion/two.json').exists()
    assert not (output/'repeat-1/mem0').exists()


@pytest.mark.parametrize('kind',['authentication','budget'])
def test_live_block_stops_independent_questions_without_calling_judge(tmp_path,kind):
    from pacbench.gateway_client import ModelAccessBlocked,SpendingBlocked
    from pacbench.native_checks import run_checks
    from tests.test_evaluation import checkpoints
    calls=[]
    def before(*args):
        calls.append('before')
        raise (ModelAccessBlocked if kind=='authentication' else SpendingBlocked)('live access blocked')
    def forbidden(*args,**kwargs):pytest.fail('Live work continued after terminal block')
    report=run_checks(checkpoints(tmp_path),'isolated',tmp_path/'arm',forbidden,[],{},
        question_dir=tmp_path/'questions',before_check=before,run_turn=forbidden)
    assert calls==['before'] and len(report['checks'])==1
    assert report['state']==('access_blocked' if kind=='authentication' else 'budget_blocked')
    assert report['checks'][0]['verdict'] is None
