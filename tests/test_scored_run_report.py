import hashlib,json
from pathlib import Path
from pacbench.scored_run_report import build


def test_partial_scores_keep_full_denominator_and_shared_judge_attribution(tmp_path):
    root=tmp_path/'cohort';component=root/'mem0';arm=component/'repeat-1/mem0';turn=arm/'turns/one';turn.mkdir(parents=True)
    (root/'cohort.json').write_text(json.dumps({'state':'running','providers':['mem0','reme']}))
    (component/'summary.json').write_text(json.dumps({'state':'running'}))
    (turn/'grade.json').write_text(json.dumps({'correct':2,'missed':1,'misrepresented':0}))
    (turn/'turn.json').write_text(json.dumps({'context_isolation':{'state':'pass'}}))
    (turn/'status.json').write_text(json.dumps({'recovery':{'score':{'points':1}}}))
    (turn/'judgment.json').write_text(json.dumps({'judge_protocol':'semantic-judge-date-scope-v2'}))
    (component/'model-calls').mkdir();(component/'model-calls/one.json').write_text(json.dumps({'response':{'id':'gen-owned'}}))
    project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-mem0'
    def row(ident,proj,cost,gen):return dict(id=ident,project=proj,model='openai/gpt-6-luna',state='settled',accounted=cost,started='2026-11-14T01:00:00+00:00',finished='2026-11-14T01:00:01+00:00',upstream_request_id=gen,input_tokens=2,output_tokens=1,http_status=200)
    ledger=tmp_path/'ledger.json';ledger.write_text(json.dumps({'calls':[row('driver',project+'-driver','.001','gen-driver'),row('judge','pacbench-judge','.002','gen-owned'),row('unrelated','pacbench-judge','.9','gen-other')]}));before=ledger.read_bytes()
    r=build(root,ledger);d=r['providers'][0]
    assert d['planned_questions']==37 and d['planned_atomic_criteria']==155
    assert d['graded_answers']==1 and d['graded_atomic_criteria']==3 and d['whole_suite_score'] is None
    assert d['initial_semantic_correct']==2 and d['retention_recovery_points_on_completed_cases']==1
    assert d['phases']['judging']['settled_actual_usd']=='.002' or d['phases']['judging']['settled_actual_usd']=='0.002'
    assert r['providers'][1]['state']=='not-started' and r['providers'][1]['chronological_gates_match_native_schedule'] is None
    assert d['phases']['other_native_or_setup']['wall_interval_union_s'] is None
    assert d['phases']['other_native_or_setup']['observed_api_duration_union_s']==1
    assert ledger.read_bytes()==before
