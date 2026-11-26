"""Import qualification must never execute or score benchmark questions."""
import json
from pathlib import Path
import pytest

from pacbench import document_ingestion, gateway_client, native_pilot, usage
from pacbench import measured_import as trial


@pytest.mark.parametrize('failed_batch',[None,'one'])
def test_import_only_waits_for_every_batch_and_never_runs_benchmark(tmp_path,monkeypatch,failed_batch):
    source=tmp_path/'source';source.mkdir();output=tmp_path/'trial'
    schedule=[{'id':n,'at':'2026-10-05T20:00:00-04:00','file':'ingestion/'+n+'.json'} for n in ['one','two']]
    messages=[{'source_id':'message-one'}]
    monkeypatch.setattr('pacbench.context_isolation.validate_recall_environment',lambda:None)
    monkeypatch.setattr(gateway_client,'require_live_cost_boundary',lambda:{'accounted_usd':1})
    monkeypatch.setattr(native_pilot,'read_prepared',lambda *a:(messages,None,{'timezone':'America/New_York'}))
    monkeypatch.setattr(native_pilot,'copy_prepared_files',lambda *a:None)
    monkeypatch.setattr(document_ingestion,'prepare_sources',lambda *a:schedule)
    monkeypatch.setattr(native_pilot,'provision',lambda *a,**kw:{'PACBENCH_USAGE_ROLES':'{}'})
    def probe(*a,**kw):
        assert kw['smoke'] is False
        return {'available':True}
    monkeypatch.setattr(native_pilot,'probe',probe)
    monkeypatch.setattr(usage,'snapshot',lambda *a:{'state':'captured','calls':[]})
    monkeypatch.setattr(usage,'provider_model_health',lambda *a:{'state':'pass'})
    stopped=[];monkeypatch.setattr(native_pilot,'stop_arm_model_work',lambda root:stopped.append(root))
    audited=[]
    monkeypatch.setattr('pacbench.document_audit.audit_ingestion',lambda *a,**kw:audited.append(kw['import_only']) or [])
    def forbidden(*a,**kw):pytest.fail('Import-only validation invoked benchmark execution')
    monkeypatch.setattr('pacbench.harness.run_turn',forbidden)
    monkeypatch.setattr('pacbench.native_pilot.ModelClient',forbidden)
    monkeypatch.setattr('pacbench.native_pilot.run_checks',forbidden)
    imported=[]
    def ingest(provider,source,*a,**kw):
        imported.append(source.name)
        return {'state':'failed' if source.name==failed_batch else 'ready','ready':source.name!=failed_batch}
    monkeypatch.setattr(document_ingestion,'ingest_day',ingest)
    result=trial.run_trial(source,output,providers=['mem0'])
    assert result['benchmark_questions_sent']==result['judge_calls']==0
    assert result['semantic_review_state']=='pending'
    assert len(stopped)==1
    assert imported==(['one'] if failed_batch else ['one','two'])
    assert bool(audited)==(failed_batch is None)
    assert result['state']==('incomplete' if failed_batch else 'processing-complete')
    assert not list(output.rglob('turn.json'))
    if failed_batch:
        saved=json.loads((output/'repeat-1/mem0/ingestion/one.json').read_text())
        assert saved['state']=='failed'


def test_conversation_probe_preserves_one_unit_and_refuses_future_day_duplicates():
    import copy
    import pytest
    from pacbench.measured_import import select_conversation_probe
    rows=[{'source_id':str(i),'channel':'imessage','thread':'thread-one','collected_at':'2026-10-06T09:00:00-04:00',
           'written_at':f'2026-10-06T08:0{i}:00-04:00','sender':f'speaker-{i%2}','text':f'exact body {i}'} for i in range(4)]
    before=copy.deepcopy(rows)
    assert select_conversation_probe(rows,['0','1','2','3'],'America/New_York')==before and rows==before
    with pytest.raises(ValueError,match='distinct'):select_conversation_probe(rows,['0','0','2','3'],'America/New_York')
    with pytest.raises(ValueError,match='unknown'):select_conversation_probe(rows,['0','1','2','missing'],'America/New_York')
    rows[-1]['collected_at']='2026-10-07T09:00:00-04:00'
    with pytest.raises(ValueError,match='visibility'):select_conversation_probe(rows,['0','1','2','3'],'America/New_York')
    rows[-1]=before[-1];rows[-1]['thread']='other'
    with pytest.raises(ValueError,match='conversation'):select_conversation_probe(rows,['0','1','2','3'],'America/New_York')
