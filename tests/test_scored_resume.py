import json
from types import SimpleNamespace
import pytest
from pacbench.scored_resume import verify_prefix
from pacbench.native_checks import run_checks
from pacbench.stop import OperatorStop


def test_scored_workspace_recovery_keeps_original_pre_recall_witness(tmp_path,monkeypatch):
    from pacbench.scored_resume import renew_scored_workspace
    import pathlib,tempfile,shutil
    workspace=pathlib.Path(tempfile.mkdtemp(prefix='pacbench-chat-',dir='/var/tmp'))
    root=tmp_path/'root';root.mkdir();history=tmp_path/'history';history.mkdir()
    (root/'recall-workspace.json').write_text(json.dumps({'workspace':str(workspace)}))
    witness=root/'pre-recall-isolation.json';witness.write_text('{"hermes_sessions_before_recall": []}')
    before=witness.read_bytes();monkeypatch.setattr('pacbench.context_isolation.outside_project',lambda p:True)
    try:
        assert renew_scored_workspace(root,history)==workspace and witness.read_bytes()==before
        (workspace/'unexpected').write_text('x')
        with pytest.raises(ValueError,match='not empty'):renew_scored_workspace(root,history)
    finally:shutil.rmtree(workspace)


def test_prefix_restores_history_without_repeating_completed_turn(tmp_path,monkeypatch):
    root=tmp_path;d=root/'turns/one';d.mkdir(parents=True)
    item={'id':'one','state':'diagnostic','contract_hash':'a','recovery':{'state':'complete'}}
    (root/'evaluation.json').write_text(json.dumps({'context':'shared','checks':[item]}));(d/'turn.json').write_text('{}')
    restored=[];monkeypatch.setattr('pacbench.harness.TurnResult',lambda **kw:SimpleNamespace())
    monkeypatch.setattr('pacbench.native_checks.advance',lambda state,turn,directory,version:restored.append(directory.name))
    checks=[({'id':'one','at':'x'},SimpleNamespace(contract_hash='a'),None),({'id':'two','at':'y'},SimpleNamespace(contract_hash='b'),None)]
    def before(row,directory):raise OperatorStop()
    def forbidden(*a,**kw):raise AssertionError('No model call may run before the stop')
    r=run_checks(checks,'shared',root,forbidden,'',{},question_dir=tmp_path,before_check=before,run_turn=forbidden,resume_prefix=1)
    assert restored==['one'] and r['checks'][0]==item and r['state']=='interrupted'
    assert not (root/'turns/one/status.json').exists()


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
    monkeypatch.setattr('pacbench.document_ingestion.ready_sources',lambda schedule,provider,at,tz:schedule if at=='x' else [])
    with pytest.raises(ValueError,match='Partial'):verify_prefix(tmp_path,report,checks)
    receipt.update(ready=True,state='ready');put(root/'ingestion/day.json',receipt)
    with pytest.raises(ValueError,match='future'):verify_prefix(tmp_path,report,checks)
