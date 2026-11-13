"""Import qualification must never execute or score benchmark questions."""
import json
from pathlib import Path
import pytest
from pacbench import import_trial as trial


@pytest.mark.parametrize('failed_batch',[None,'one'])
def test_import_only_waits_for_every_batch_and_never_runs_benchmark(tmp_path,monkeypatch,failed_batch):
    source=tmp_path/'source';source.mkdir();output=tmp_path/'trial'
    schedule=[{'id':n,'at':'2026-10-05T20:00:00-04:00','file':'ingestion/'+n+'.json'} for n in ['one','two']]
    messages=[{'source_id':'message-one'}]
    monkeypatch.setattr('pacbench.context_isolation.validate_recall_environment',lambda:None)
    monkeypatch.setattr(trial,'require_live_cost_boundary',lambda:{'accounted_usd':1})
    monkeypatch.setattr(trial,'read_prepared',lambda *a:(messages,None,{'timezone':'America/New_York'}))
    monkeypatch.setattr(trial,'copy_prepared_files',lambda *a:None)
    monkeypatch.setattr(trial,'prepare_sources',lambda *a:schedule)
    monkeypatch.setattr(trial,'provision',lambda *a,**kw:{'PACBENCH_USAGE_ROLES':'{}'})
    def probe(*a,**kw):
        assert kw['smoke'] is False
        return {'available':True}
    monkeypatch.setattr(trial,'probe',probe)
    monkeypatch.setattr(trial,'snapshot',lambda *a:{'state':'captured','calls':[]})
    monkeypatch.setattr(trial,'provider_model_health',lambda *a:{'state':'pass'})
    stopped=[];monkeypatch.setattr(trial,'stop_arm_model_work',lambda root:stopped.append(root))
    audited=[]
    monkeypatch.setattr('pacbench.document_audit.audit_ingestion',lambda *a:audited.append(True) or [])
    def forbidden(*a,**kw):pytest.fail('Import-only validation invoked benchmark execution')
    monkeypatch.setattr('pacbench.harness.run_turn',forbidden)
    monkeypatch.setattr('pacbench.native_pilot.ModelClient',forbidden)
    monkeypatch.setattr('pacbench.native_pilot.run_checks',forbidden)
    imported=[]
    def ingest(provider,source,*a,**kw):
        imported.append(source.name)
        return {'state':'failed' if source.name==failed_batch else 'ready','ready':source.name!=failed_batch}
    monkeypatch.setattr(trial,'ingest_day',ingest)
    result=trial.run(source,output,providers=['mem0'])
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
