import hashlib,json
from pathlib import Path
from pacbench.workflows.recording import scored_report as build


def test_cents_display_keeps_positive_subcent_values_visible(tmp_path):
    from pacbench.workflows.recording import display_usd, budget_display
    assert display_usd('0')=='$0.00'
    assert display_usd('.000023612232')=='<$0.01'
    assert display_usd('.217553695')=='$0.22'
    p=tmp_path/'ledger.json';p.write_text(json.dumps({'config':{'cap':'20'},'calls':[
        {'state':'settled','accounted':'2.014713321032'},
        {'state':'uncertain','accounted':'10'}, {'state':'reserved','accounted':'4'},
        {'state':'allocated','accounted':'3'}]}));before=p.read_bytes();d=budget_display(p)
    assert d['verified_charges']=='$2.01' and d['outstanding_reservations']=='$4.00'
    assert d['unresolved_charge_bounds']=='$10.00' and d['external_allocations']=='$3.00'
    assert d['remaining_after_all_exposure']=='$0.99' and p.read_bytes()==before


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


def test_identical_inputs_are_not_automatically_counted_as_retry_attempts(tmp_path):
    root=tmp_path/'cohort';arm=root/'mem0/repeat-1/mem0';arm.mkdir(parents=True)
    (root/'cohort.json').write_text(json.dumps({'state':'running','providers':['mem0']}))
    project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-mem0-memory'
    def row(i):return dict(id=i,project=project,model='openai/gpt-6-luna',state='settled',accounted='.001',
        started='2026-11-14T01:00:00+00:00',finished='2026-11-14T01:00:01+00:00',request_sha256='same',input_tokens=2,output_tokens=1)
    a,b=row('first'),row('second');p=tmp_path/'ledger.json';p.write_text(json.dumps({'calls':[a,b]}))
    d=build(root,p)['providers'][0];phase=d['phases']['other_native_or_setup']
    assert d['identical_request_groups']==1 and phase['explicit_retry_attempts']==0
    b['explicit_retry_of']='first';p.write_text(json.dumps({'calls':[a,b]}))
    phase=build(root,p)['providers'][0]['phases']['other_native_or_setup']
    assert phase['explicit_retry_attempts']==1 and phase['settled_actual_usd']=='0.002'
    calls=root/'mem0/model-calls';calls.mkdir()
    (calls/'writer.json').write_text(json.dumps({'response':{'id':'writer'},'request':{'messages':[{'role':'user','content':'Write one short user message asking for each missing detail. Hidden evidence.'}]}}))
    writer=row('writer');writer['project']='pacbench-judge';writer['upstream_request_id']='writer'
    p.write_text(json.dumps({'calls':[a,b,writer]}))
    phases=build(root,p)['providers'][0]['phases']
    assert phases['followup_authoring_review']['settled_actual_usd']=='0.001'
    assert phases['other_native_or_setup']['settled_actual_usd']=='0.002'

def test_revised_contract_and_strict_ungraded_denominators_are_explicit(tmp_path):
    root=tmp_path/'cohort';arm=root/'mem0-retry/repeat-1/mem0';turn=arm/'turns/one';turn.mkdir(parents=True)
    (root/'cohort.json').write_text(json.dumps({'state':'running','providers':['mem0'],'questions':42,'criteria':188,'provider_outputs':{'mem0':'mem0-retry'},'blocked_arms':[{'provider':'memcal','planned_questions':42}]}))
    (turn/'turn.json').write_text(json.dumps({'tool_calls':[{'name':'mem0_search'},{'name':'mem0_search'}],'tool_results':[{'success':True},{'success':False}]}))
    (turn/'strict-memory.json').write_text(json.dumps({'state':'excluded'}))
    ledger=tmp_path/'ledger.json';ledger.write_text('{"calls":[]}')
    report=build(root,ledger);d=report['providers'][0]
    assert d['planned_questions']==42 and d['planned_atomic_criteria']==188
    assert d['graded_answers']==0 and d['whole_suite_score'] is None
    assert d['strict_memory_question_counts']=={'excluded':1}
    assert d['memory_tool_calls_initial']['attempted']==2 and d['memory_tool_calls_initial']['completed_successfully']==1
    assert report['blocked_arms'][0]['provider']=='memcal'

def test_failed_turn_receipt_does_not_count_as_returned_answer_or_proven_zero_calls(tmp_path):
    root=tmp_path/'cohort';arm=root/'mem0/repeat-1/mem0';first=arm/'turns/one';second=arm/'turns/two';first.mkdir(parents=True);second.mkdir()
    (root/'cohort.json').write_text(json.dumps({'state':'running','providers':['mem0'],'questions':42,'criteria':188}))
    (first/'turn.json').write_text(json.dumps({'returncode':-1,'answer':'','tool_calls':[],'tool_results':[]}))
    (second/'turn.json').write_text(json.dumps({'returncode':0,'answer':'Known answer','session_id':'s','tool_calls':[],'tool_results':[]}))
    ledger=tmp_path/'ledger.json';ledger.write_text('{"calls":[]}')
    row=build(root,ledger)['providers'][0]
    assert row['answers_returned']==1 and row['turn_receipts']==2
    assert row['questions_without_strict_receipt']==42
    assert row['memory_tool_calls_initial']['attempted']==0
    assert row['memory_tool_calls_initial']['complete_turn_tool_logs']==1
    assert row['memory_tool_calls_initial']['failed_or_missing_turn_tool_logs']==1

def test_failed_judge_charge_bound_uses_accounting_id_without_generation_id(tmp_path):
    root=tmp_path/'cohort';calls=root/'mem0/model-calls';calls.mkdir(parents=True)
    (root/'cohort.json').write_text(json.dumps({'state':'running','providers':['mem0']}))
    (calls/'failed.json').write_text(json.dumps({'status':'failed','accounting_receipt':{'id':'exact-attempt'},'request':{'messages':[{'role':'user','content':'Judge the actual answer.'}]}}))
    ledger=tmp_path/'ledger.json';ledger.write_text(json.dumps({'calls':[{'id':'exact-attempt','project':'pacbench-judge','model':'openai/gpt-6-luna','state':'uncertain','accounted':'.26','started':'2026-11-15T01:00:00+00:00','finished':'2026-11-15T01:00:01+00:00'}]}))
    phases=build(root,ledger)['providers'][0]['phases']
    assert phases['judging']['calls']==1 and phases['judging']['settled_actual_usd']=='0'
    assert phases['judging']['unresolved_or_inflight_usd']=='0.26'

def test_local_only_import_keeps_measured_wall_time_with_zero_inference_calls(tmp_path):
    root=tmp_path/'cohort';component=root/'memos';arm=component/'repeat-1/memos';ingest=arm/'ingestion';ingest.mkdir(parents=True)
    (root/'cohort.json').write_text(json.dumps({'state':'running','providers':['memos']}))
    source=component/'sources/day/messages.txt';source.parent.mkdir(parents=True);source.write_text('source')
    (ingest/'day.json').write_text(json.dumps({'source_ids':['one'],'ready':True,'document_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'usage':{'started':'2026-11-15T00:00:00+00:00','finished':'2026-11-15T00:00:05+00:00'}}))
    ledger=tmp_path/'ledger.json';ledger.write_text('{"calls":[]}')
    phase=build(root,ledger)['providers'][0]['phases']['ingest_processing']
    assert phase['wall_interval_union_s']==5 and phase['calls']==0 and phase['settled_actual_usd']=='0'

def test_followup_answer_calls_are_not_mislabeled_as_setup(tmp_path):
    root=tmp_path/'cohort';arm=root/'mem0/repeat-1/mem0';follow=arm/'turns/one/followups/combined';follow.mkdir(parents=True)
    (root/'cohort.json').write_text(json.dumps({'state':'running','providers':['mem0']}))
    (follow/'usage.json').write_text(json.dumps({'started':'2026-11-15T00:00:00+00:00','finished':'2026-11-15T00:00:05+00:00'}))
    project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-mem0-driver'
    ledger=tmp_path/'ledger.json';ledger.write_text(json.dumps({'calls':[{'id':'follow','project':project,'model':'openai/gpt-6-luna','state':'settled','accounted':'.001','started':'2026-11-15T00:00:01+00:00','finished':'2026-11-15T00:00:03+00:00'}]}))
    d=build(root,ledger)['providers'][0]
    assert d['phases']['followup_answer_retrieval']['settled_actual_usd']=='0.001'
    assert d['phases']['followup_answer_retrieval']['wall_interval_union_s']==5
    assert d['memory_tool_calls_initial']['attempted']==0


def test_admission_reservation_and_unknown_charge_are_distinct(tmp_path):
    root=tmp_path/'cohort';arm=root/'reme/repeat-1/reme';arm.mkdir(parents=True)
    (root/'cohort.json').write_text(json.dumps({'state':'partial','providers':['reme']}))
    project='pacbench-'+hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]+'-reme-memory'
    calls=[{'id':str(i),'project':project,'model':'openai/gpt-6-luna','state':state,'accounted':amount,'started':'2026-11-15T00:00:00+00:00'} for i,(state,amount) in enumerate([('reserved','.2'),('uncertain','.3'),('settled','.001')])]
    ledger=tmp_path/'ledger.json';ledger.write_text(json.dumps({'calls':calls}))
    row=build(root,ledger)['providers'][0]['phases']['other_native_or_setup']
    assert row['outstanding_reservations_usd']=='0.2'
    assert row['unresolved_charge_ceilings_usd']=='0.3'
    assert row['settled_actual_usd']=='0.001'
    assert row['unresolved_or_inflight_usd']=='0.5' # Explicit legacy combined field.
    assert row['sanitized_response_receipt_attempts']==0
