"""Synthetic policy/transport checks; no fake provider performance."""
import json
import sqlite3
from pathlib import Path
import pytest
from pacbench.followups import natural_eligibility
from pacbench.semantic_grade import Claim, grade_semantic
from pacbench.usage import snapshot, window
from pacbench.gateway_client import validate_route


@pytest.mark.parametrize('kind,verdict,eligible,reason', [
    ('implicit_omission','missed',['time'],None),
    ('explicitly_requested','missed',[],'explicit_request_failed'),
    ('unavailable','missed',[],'admitted_retrieval_failure'),
    ('incorrect','misrepresented',[],'incorrect_answer')])
def test_only_implicit_silent_omissions_are_recoverable(kind,verdict,eligible,reason):
    claims=(Claim('place','Pizza is at the restaurant.'),Claim('time','Pizza is at seven.'))
    def judge(prompt):
        return json.dumps({'verdicts':[{'id':'place','verdict':'correct','reason':'Stated.'},
            {'id':'time','verdict':verdict,'reason':'Synthetic assessment.'}],
            'recovery':[{'id':'place','kind':'provided','reason':'Stated.'},
                        {'id':'time','kind':kind,'reason':'Synthetic assessment.'}]})
    g=grade_semantic('synthetic','Recap the pizza plan.','Synthetic answer.',claims,judge,assess_recovery=True)
    assert natural_eligibility(g)==(eligible,reason)


def test_missing_classification_cannot_start_recovery():
    with pytest.raises(ValueError,match='eligibility'):
        grade_semantic('test','Recap.','Answer.',(Claim('one','Fact.'),),
            lambda _:json.dumps({'verdicts':[{'id':'one','verdict':'missed','reason':'Absent.'}]}),assess_recovery=True)


def test_gateway_blocks_external_routes_and_upstream_keys(tmp_path,monkeypatch):
    from pacbench import gateway_client
    (tmp_path/'client-token').write_text('local-key')
    (tmp_path/'address.json').write_text(json.dumps({'base':'http://127.0.0.1:8099'}))
    monkeypatch.setattr(gateway_client,'STATE',tmp_path)
    validate_route('http://127.0.0.1:8099/p/test/v1/responses','local-key')
    for url,key in [('https://api.meta.ai/v1/responses','upstream-key'),
                    ('http://127.0.0.1:8099/p/test/v1/responses','upstream-key'),
                    ('http://127.0.0.1:8099.evil/p/test/v1/responses','local-key')]:
        with pytest.raises(RuntimeError,match='shared spending gateway'):
            validate_route(url,key)


def test_usage_keeps_unknown_tokens_late_settlements_and_provider_roles_separate(tmp_path):
    with sqlite3.connect(tmp_path/'ledger.sqlite3') as db:
        db.execute('CREATE TABLE calls(id,project,model,endpoint,started,finished,state,input_tokens,output_tokens,accounted,reserved)')
        db.execute("INSERT INTO calls VALUES('prior','provider','spark','responses','t0',NULL,'reserved',NULL,NULL,100000000,100000000)")
    roles={'driver':'hermes','provider':'memory_provider'}
    before=snapshot(roles,tmp_path)
    with sqlite3.connect(tmp_path/'ledger.sqlite3') as db:
        db.execute("UPDATE calls SET finished='t1',state='settled',input_tokens=20,output_tokens=10 WHERE id='prior'")
        db.execute("INSERT INTO calls VALUES('driver-new','driver','spark','responses','t1','t2','settled',30,8,1000000,100000000)")
        db.execute("INSERT INTO calls VALUES('provider-new','provider','spark','responses','t1',NULL,'reserved',NULL,NULL,100000000,100000000)")
    after=snapshot(roles,tmp_path)
    usage=window(before,after)
    assert usage['roles']['hermes']['input_tokens']==30
    assert usage['roles']['memory_provider']['input_tokens'] is None
    assert [c['id'] for c in usage['late_settlements']]==['prior']
    assert usage['roles']['memory_provider']['unsettled_calls']==1
    assert usage['per_tool_tokens'] is None
    assert snapshot(roles,tmp_path/'missing')['state']=='unavailable'


def test_external_model_client_is_rejected_before_http(tmp_path,monkeypatch):
    from pacbench import gateway_client
    from pacbench.model_calls import ModelClient
    (tmp_path/'client-token').write_text('local-key')
    (tmp_path/'address.json').write_text(json.dumps({'base':'http://127.0.0.1:8099'}))
    monkeypatch.setattr(gateway_client,'STATE',tmp_path)
    def forbidden(*args,**kwargs):
        raise AssertionError('External Spark transport must never execute')
    monkeypatch.setattr('pacbench.model_calls.urllib.request.urlopen',forbidden)
    client=ModelClient(key='upstream-key',model='muse-spark-1.3-contributor',
        url='https://api.meta.ai/v1/chat/completions',record_dir=tmp_path/'records')
    with pytest.raises(RuntimeError,match='shared spending gateway'):
        client('Test blocking only; no model request.')
