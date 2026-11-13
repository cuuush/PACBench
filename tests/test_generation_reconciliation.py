"""Only authentic exact-attempt cancellation evidence resolves an unknown bill."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path
import pytest
from pacbench.direct_accounting import DirectBudget
from pacbench.generation_reconciliation import reconcile_cancelled
from pacbench.openrouter_route import MODEL


@pytest.fixture
def attempt(tmp_path,monkeypatch):
    path=tmp_path/'ledger.json'
    path.write_text(json.dumps({'version':1,'config':{'cap':'20','context_tokens':100,
        'prompt_per_million':'1','completion_per_million':'2','max_completion_tokens':50,
        'model':MODEL,'endpoint':'deepinfra/fp8/flex'},'calls':[]}))
    budget=DirectBudget(path)
    body={'model':MODEL,'stream':True,'messages':[{'role':'user','content':'Frozen corpus'}]}
    row=budget.reserve('pacbench-driver',body)
    raw=('data: '+json.dumps({'id':'gen-offline','model':MODEL,'provider':'DeepInfra',
        'choices':[{'delta':{'content':'partial'}}]})+'\n\n').encode()
    budget.settle(row['id'],200,raw)
    calls=tmp_path/'model-calls';(calls/'private').mkdir(parents=True)
    (calls/'private/stream.bin').write_bytes(raw)
    record={'status':'failed','url':'https://openrouter.ai/api/v1/chat/completions','request':body,
        'direct_response':{'http_status':200},'accounting_receipt':{'id':row['id']},
        'stream_wire_response':{'relative_path':'private/stream.bin','sha256':hashlib.sha256(raw).hexdigest()}}
    failed=calls/'failed.json';failed.write_text(json.dumps(record))
    generation={'id':'gen-offline','model':'canonical-model-name','provider_name':'DeepInfra','cancelled':True,
        'total_cost':'0.00001','native_tokens_prompt':3,'native_tokens_completion':2}
    catalog=[{'id':MODEL,'canonical_slug':'canonical-model-name'}]
    def fetched(url,key):
        assert key=='inert'
        assert url in ['https://openrouter.ai/api/v1/generation?id=gen-offline','https://openrouter.ai/api/v1/models']
        return json.dumps({'data':generation if '/generation?' in url else catalog}).encode()
    monkeypatch.setattr('pacbench.generation_reconciliation._fetch',fetched)
    return failed,budget,generation,catalog


def test_reconciliation_retains_failed_reply_and_records_canonical_alias_provenance(attempt,tmp_path):
    path,budget,metadata,_=attempt;original=path.read_bytes();old=budget.snapshot()[0]
    receipt=reconcile_cancelled(path,tmp_path/'receipt','inert',budget=budget)
    row=budget.snapshot()[0]
    assert row['state']=='settled' and row['accounted']=='0.00001' and row['cancelled'] is True
    assert row['request_sha256']==old['request_sha256'] and row['finished']==old['finished']
    assert receipt['previous_accounted_usd']==old['accounted'] and receipt['previous_state']=='uncertain'
    assert receipt['sse_model']==MODEL and receipt['generation_model']=='canonical-model-name'
    assert path.read_bytes()==original and json.loads(path.read_text())['status']=='failed'
    assert row['reconciliations'][0]['generation_id']=='gen-offline'
    with pytest.raises(ValueError,match='identity differs'):
        reconcile_cancelled(path,tmp_path/'again','inert',budget=budget)


@pytest.mark.parametrize('change',[
    {'id':'different'}, {'model':'unrecognized-model'}, {'provider_name':'other'}, {'cancelled':False},
    {'total_cost':-1},{'total_cost':'NaN'},{'total_cost':True},
    {'native_tokens_prompt':True},{'native_tokens_completion':-1},{'native_tokens_completion':51},
])
def test_bad_metadata_never_releases_an_uncertain_bound(attempt,tmp_path,change):
    path,budget,metadata,_=attempt;before=budget.snapshot();metadata.update(change)
    with pytest.raises((ValueError,KeyError)):
        reconcile_cancelled(path,tmp_path/'receipt','inert',budget=budget)
    assert budget.snapshot()==before


def test_tampered_original_wire_is_rejected_before_lookup(attempt,tmp_path,monkeypatch):
    path,budget,metadata,_=attempt
    (path.parent/'private/stream.bin').write_bytes(b'changed')
    monkeypatch.setattr('pacbench.generation_reconciliation._fetch',lambda *a:pytest.fail('lookup after failed binding'))
    with pytest.raises(ValueError,match='differs from receipt'):
        reconcile_cancelled(path,tmp_path/'receipt','inert',budget=budget)


def test_duplicate_generation_cannot_settle_two_attempts(attempt,tmp_path):
    path,budget,metadata,_=attempt
    with budget.locked() as state:
        duplicate={**state['calls'][0],'id':'another','state':'settled'}
        state['calls'].append(duplicate)
    before=budget.snapshot()
    with pytest.raises(ValueError,match='another settled'):
        reconcile_cancelled(path,tmp_path/'receipt','inert',budget=budget)
    assert budget.snapshot()==before


def test_reported_excess_liability_is_recorded_and_blocks_further_spend(attempt,tmp_path):
    path,budget,metadata,_=attempt;metadata['total_cost']='0.001'
    reconcile_cancelled(path,tmp_path/'receipt','inert',budget=budget)
    row=budget.snapshot()[0]
    assert row['accounted']=='0.001' and row['reported_liability_exceeds_bound']
    with pytest.raises(RuntimeError,match='liability exceeded'):
        budget.reserve('another-project',{'model':MODEL,'messages':[]})


def test_numeric_api_charge_preserves_decimal_precision_at_reservation_boundary(attempt,tmp_path,monkeypatch):
    path,budget,metadata,_=attempt
    exact='0.000200000000000000000001'
    metadata['total_cost']=exact
    from pacbench import generation_reconciliation as module
    original=module._fetch
    def fetched(url,key):
        raw=original(url,key)
        return raw.replace(('"'+exact+'"').encode(),exact.encode()) if '/generation?' in url else raw
    monkeypatch.setattr(module,'_fetch',fetched)
    receipt=reconcile_cancelled(path,tmp_path/'receipt','inert',budget=budget)
    assert receipt['reported_charge_usd']==exact
    assert budget.snapshot()[0]['reported_liability_exceeds_bound']
