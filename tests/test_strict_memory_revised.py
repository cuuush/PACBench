import json
import pytest
from pacbench.strict_memory import classify

def turn(**extra):return dict(tool_calls=[],tool_results=[],prefetch_context='',provider_traces=[],**extra)

def test_hybrid_unknown_and_explicit_source_take_precedence():
 t=turn();t['prefetch_context']='Profile with flattened hybrid search results'
 assert classify('supermemory',t)['state']=='unknown'
 t['tool_calls']=[{'name':'supermemory_read_document','arguments':{'document_id':'original'}}]
 t['tool_results']=[{'success':True,'text':json.dumps({'content':'Original conversation transcript'})}]
 assert classify('supermemory',t)['state']=='excluded'
 assert classify('mem0',turn(),inherited='excluded')['state']=='excluded'

def test_normalized_recall_and_empty_source_are_distinct():
 t=turn();t['tool_calls']=[{'name':'memos_search'}];t['tool_results']=[{'success':True,'text':'{"hits":[]}'}]
 assert classify('memos',t)['state']=='eligible'
 t['tool_results'][0]['text']='{"hits":[{"refKind":"trace","snippet":"original"}]}'
 assert classify('memos',t)['state']=='excluded'

def test_memory_uri_does_not_launder_chat_transcript():
 t=turn();t['prefetch_context']='<uri>viking://user/memories/event</uri>\n# Today ChatLog:\nuser: original'
 assert classify('openviking',t)['state']=='excluded'

@pytest.mark.parametrize('provider',['supermemory','memcal'])
def test_ungraded_gate_precedes_judge_and_is_independently_audited(tmp_path,provider):
 from tests.test_evaluation import checkpoints
 from pacbench.harness import TurnResult
 from pacbench.native_checks import run_checks,audit_checks
 from pacbench.clock_context import expected_prompt
 checks=checkpoints(tmp_path);root=tmp_path/provider
 seen=[]
 def ready(row,directory):seen.append(('ready',row['id']))
 def runner(prompt,**kw):
  seen.append(('answer',kw['at']))
  return TurnResult(prompt=expected_prompt(kw['at'],prompt,'user-clock-prefix-v1'),at=kw['at'],answer='answer',
      tool_calls=[],tool_results=[],prefetch_context='Flattened hybrid memories',policy_blocks=0,latency_s=1,
      returncode=0,stderr_tail='',raw_events=0,session_id=kw.get('session_id') or 'session',session_input_tokens=2,session_output_tokens=1)
 def forbidden(*a,**kw):raise AssertionError('Unknown-source answer reached judge')
 report=run_checks(checks,'shared',root,forbidden,'',{'PACBENCH_STRICT_MEMORY_ONLY':'1'},
      question_dir=tmp_path/'questions',run_turn=runner,before_check=ready,budget_snapshot=lambda:{'projects':[]})
 assert report['state']=='diagnostic' and all(x['state']=='strict-ungraded' for x in report['checks'])
 assert not list(root.glob('turns/*/grade.json')) and not list(root.glob('turns/*/judgment.json'))
 assert all(seen[i][0]=='ready' and seen[i+1][0]=='answer' for i in range(0,len(seen),2))
 assert audit_checks(checks,'shared',root)==[]

def test_logical_document_transport_preserves_join_without_apple_fixture(tmp_path):
 from tests.test_scene_local_authoring import week,exact,built
 from pacbench.prepare import resolve_spec
 from pacbench.document_ingestion import prepare_sources
 plans=resolve_spec(week([exact(participants=['Me','Sam','Robin']),exact(id='join',time='7 pm',joins=['Nora'])]),tmp_path)[0]
 messages=[m for p in plans for m in built(p)]
 schedule=prepare_sources(tmp_path/'sources-run',messages,'America/New_York',native_fixtures=False)
 source=tmp_path/'sources-run/sources'/schedule[0]['id']
 assert json.loads((source/'messages.json').read_text())==messages
 assert not (source/'chat.db').exists()
 text=(source/'messages.txt').read_text()
 assert 'Nora' not in text.split('Participants: Me, Sam, Robin, Nora')[0]


def test_unknown_recovery_is_counted_ungraded_without_erasing_initial_grade(tmp_path):
 from tests.test_followups import contract,plan,turn
 from pacbench.native_checks import run_checks,audit_checks
 c=contract();root=tmp_path/'memcal';sent=[];model_calls=[]
 def runner(prompt,**kw):
  sent.append(prompt);t=turn(prompt,answer='Friday.' if len(sent)==1 else 'Seven.')
  if len(sent)>1:t.prefetch_context='Unverified inherited/native payload'
  return t
 def model(prompt):
  if prompt.startswith('Write '):model_calls.append('write');return json.dumps({'question':'What time?'})
  if 'Claims (hidden from the assistant): ' not in prompt:
   model_calls.append('review');return json.dumps({'safe':True,'covers_claim_ids':['time'],'reason':'Neutral.'})
  model_calls.append('judge')
  return json.dumps({'verdicts':[{'id':'date','verdict':'correct','reason':'Synthetic control.'},
      {'id':'time','verdict':'missed','reason':'Synthetic omission.'}],
      'recovery':[{'id':'date','kind':'provided','reason':'Synthetic control.'},
      {'id':'time','kind':'implicit_omission','reason':'Synthetic control.'}]})
 checks=[({'id':'q','at':'2026-09-11T09:00:00-04:00'},c,plan())]
 report=run_checks(checks,'isolated',root,model,'',{'PACBENCH_STRICT_MEMORY_ONLY':'1'},
     question_dir=tmp_path/'questions',run_turn=runner,budget_snapshot=lambda:{'projects':[]})
 assert report['checks'][0]['state']=='diagnostic' and report['counts']['recovery_ungraded']==1
 assert report['checks'][0]['recovery']['score_state']=='strict-ungraded'
 assert report['checks'][0]['recovery']['score'] is None and model_calls==['judge','write','review']
 assert json.loads((root/'turns/q/grade.json').read_text())['correct']==1
 assert audit_checks(checks,'isolated',root)==[]
