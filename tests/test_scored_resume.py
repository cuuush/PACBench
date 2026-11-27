import json

from types import SimpleNamespace

import pytest

from pacbench.judging.retained_week import verify_prefix

def test_pinned_mem0_extraction_failure_cannot_write_future_input():
    """Exercise the actual native SDK, rather than assuming a failed import is
    harmless. Its initial LLM failure must precede vector/history writes."""
    memory=pytest.importorskip('mem0.memory.main')
    calls=[]
    class Database:
        def get_last_messages(self,*a,**kw):calls.append('history-read');return []
        def __getattr__(self,name):raise AssertionError('Unexpected persistent operation: '+name)
    class Vector:
        def search(self,*a,**kw):calls.append('vector-read');return []
        def __getattr__(self,name):raise AssertionError('Unexpected vector operation: '+name)
    def fail(**kw):calls.append('llm-failure');raise RuntimeError('synthetic pre-dispatch refusal')
    fake=SimpleNamespace(db=Database(),vector_store=Vector(),embedding_model=SimpleNamespace(embed=lambda *a:[0.0]),
        llm=SimpleNamespace(generate_response=fail),custom_instructions=None)
    with pytest.raises(Exception,match='LLM extraction failed'):
        memory.Memory._add_to_vector_store(fake,[{'role':'user','content':'future synthetic source'}],{}, {'user_id':'synthetic'},True)
    assert calls==['history-read','vector-read','llm-failure']

def test_partial_or_future_store_is_rejected_before_recovery(tmp_path,monkeypatch):
    root=tmp_path/'repeat-1/memos';d=root/'turns/one';d.mkdir(parents=True)
    item={'id':'one','state':'diagnostic','contract_hash':'a'}
    def put(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v))
    put(root/'evaluation.json',{'checks':[item]});put(d/'status.json',item)
    put(d/'turn.json',{'returncode':0,'policy_blocks':[],'context_isolation':{'state':'pass'},'session_id':'s','prompt':'Question','at':'x','answer':'A'})
    put(d/'grade.json',{'claims':[]});put(d/'judgment.json',{'answer':'A','response':'{"verdicts": []}'})
    put(d/'source-state.json',{'ingested_batches':['day']})
    source=tmp_path/'sources/day/messages.txt';source.parent.mkdir(parents=True);source.write_text('source')
    batch={'id':'day','file':'ingestion/day.json','source_ids':['one']}
    import hashlib
    receipt={'ready':False,'state':'failed','source_ids':['one'],'document_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    put(root/'ingestion/day.json',receipt)
    report={'results':[{'provider':'memos'}],'ingestion_schedule':[batch],'process_timezone':'UTC','question_prompt_protocol':'native-question-v2'}
    checks=[({'id':'one','at':'x'},SimpleNamespace(contract_hash='a',question='Question'),None),({'id':'two','at':'y'},None,None)]
    monkeypatch.setattr('pacbench.harness.question_prompt',lambda *a:'Question')
    monkeypatch.setattr('pacbench.workflows.source_plan.ready_sources',lambda schedule,provider,at,tz:schedule if at=='x' else [])
    with pytest.raises(ValueError,match='Partial'):verify_prefix(tmp_path,report,checks)
    receipt.update(ready=True,state='ready');put(root/'ingestion/day.json',receipt)
    with pytest.raises(ValueError,match='future'):verify_prefix(tmp_path,report,checks)

def test_strict_ungraded_prefix_requires_actual_provenance_and_no_grades(tmp_path,monkeypatch):
    from dataclasses import asdict
    from pacbench.harness import TurnResult
    from pacbench.source_policy import classify_retention as classify
    root=tmp_path/'repeat-1/supermemory';d=root/'turns/one';d.mkdir(parents=True)
    item={'id':'one','state':'strict-ungraded','contract_hash':'a','verdict':None}
    def put(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v))
    t=TurnResult(prompt='Question',at='x',answer='A',tool_calls=[],tool_results=[],prefetch_context='Flattened hybrid',policy_blocks=0,latency_s=1,returncode=0,stderr_tail='',raw_events=0,session_id='session',session_input_tokens=2,session_output_tokens=1)
    put(root/'evaluation.json',{'checks':[item],'context':'shared','strict_memory_protocol':'strict-memory-only-v1'})
    put(d/'turn.json',{**asdict(t),'context_isolation':{'state':'pass'}});put(d/'status.json',item)
    put(d/'strict-memory.json',classify('supermemory',t));put(d/'source-state.json',{'ingested_batches':[]})
    report={'results':[{'provider':'supermemory'}],'ingestion_schedule':[],'process_timezone':'UTC','question_prompt_protocol':'native-question-v2'}
    checks=[({'id':'one','at':'x'},SimpleNamespace(contract_hash='a',question='Question'),None),({'id':'two','at':'y'},None,None)]
    monkeypatch.setattr('pacbench.harness.question_prompt',lambda *a:'Question')
    monkeypatch.setattr('pacbench.workflows.source_plan.ready_sources',lambda *a:[])
    prefix,completed=verify_prefix(tmp_path,report,checks)
    assert prefix==[item] and completed==[]
    put(d/'grade.json',{'claims':[]})
    with pytest.raises(ValueError,match='fabricated'):verify_prefix(tmp_path,report,checks)
    (d/'grade.json').unlink();put(d/'strict-memory.json',{'state':'eligible'})
    with pytest.raises(ValueError,match='provenance'):verify_prefix(tmp_path,report,checks)
