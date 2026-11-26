"""Practical correctness overlay on frozen saved answers; never asks Hermes again."""
import os,json,hashlib,time
from pathlib import Path
from . import full_run_capability as cap
from .gateway_reuse import atomic
from .harness import TurnResult
from .checkpoint_evaluator import audit_transport
from .semantic_grade import grade_native_semantic,Claim,DATE_SCOPED_JUDGE_PROTOCOL,DIAGNOSTIC_VERSION
from .native_checks import diagnostic_inputs,extend_history,session_key
from .clock_context import judge_question
from pacbench.corpus.evaluation import judge_evidence
from .model_calls import ModelClient


def run(output,original,budget,settings):
 output=Path(output).resolve();existing=output.exists();output.mkdir(exist_ok=True)
 original=Path(original).resolve();prep,schedule,checks,plan=cap.frozen()
 from .campaigns.pizza_week_checkpoints import verify_completed
 verify_completed(original,'memcal',checks,plan,schedule)
 previous=cap.read(cap.binding(original/'capability.json'))
 projects={p:{r:'pacbench-'+hashlib.sha256(str(output/p).encode()).hexdigest()[:12]+'-'+p+'-'+r for r in ['memory','driver','judge']} for p in cap.PROVIDERS}
 permission=cap.binding(output/'capability.json') if existing else cap.create(output,projects,previous['configs'],prior_root=original)
 os.environ[cap.ENV]=json.dumps(permission)
 cap.load(permission)
 report={'protocol':'r2-practical-retained-overlay-v1','frozen_inputs':plan['frozen_inputs'],'original':cap.binding(original/'report.json'),'providers':{},'conditions':cap.specification(),'checks':[],'strict_scores_modified':False,'new_answers':0}
 if existing:
  report=json.loads((output/'report.json').read_text())
  if report['frozen_inputs']!=plan['frozen_inputs'] or cap.read(report['original'])!=json.loads((original/'report.json').read_text()):raise RuntimeError('Retained grading resume binding differs')
 atomic(output/'report.json',report)
 bundle=json.loads((prep/'bundle.json').read_text());messages=[m for v in bundle['lives'].values() for m in v['messages']];resolved=json.loads((prep/'resolved-spec.json').read_text())
 provider='memcal';base,key=settings(projects[provider]['judge']);client=ModelClient(key=key,model='openai/gpt-6-luna',url=base+'/chat/completions',record_dir=output/'model-calls',json_mode=True,timeout_s=180,max_attempts=1)
 history={}
 for row,contract,_ in checks:
  folder=original/provider/'evaluation/turns'/row['id'];turn_path=folder/'turn.json';turn=TurnResult(**json.loads(turn_path.read_text()));audit_transport(turn)
  group=next(g for g in plan['groups'] if any(q['id']==row['id'] for q in g['questions']))
  proof=cap.binding(original/provider/('checkpoint-'+str(group['id']))/'source-proof.json')
  ready=json.loads((Path(turn.hermes_gateway['receipt'])/'answer-ready.json').read_text());slot={'kind':'retained_judge','provider':provider,'group_id':group['id'],'batch_ids':group['ready_batch_ids'],
   'home':turn.hermes_gateway['clock_evidence']['descriptor']['home'],'checkpoint':ready['checkpoint'],'proof':proof,'ingest_receipt_ids':[],
   'question_id':row['id'],'observation':ready['observation'],'question_directory':str(folder),'turn':cap.binding(turn_path),'strict':cap.binding(folder/'strict-memory.json')}
  key_session=session_key(row,'isolated');conversation=history.get(key_session,[]);target=output/'judgments'/row['id'];target.mkdir(parents=True,exist_ok=True)
  saved=next((c for c in report['checks'] if c['id']==row['id']),None)
  if saved:
   if saved['contract_hash']!=contract.contract_hash or saved['turn']!=cap.binding(turn_path) or json.loads((target/'grade.json').read_text())!=saved:raise RuntimeError('Retained grade resume mismatch')
   history[key_session]=extend_history(conversation,turn);continue
  def judge(prompt):
   atomic(output/'active-slot.json',{**slot,'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
   begin=time.monotonic()
   try:return client(prompt)
   finally:
    with (output/'judge-timing.jsonl').open('a') as f:f.write(json.dumps({'id':row['id'],'wall_s':time.monotonic()-begin})+'\n')
  grade=grade_native_semantic(row['id'],judge_question(turn),turn.answer,tuple(Claim(c.id,c.statement,c.evidence) for c in contract.claims),judge,
   record_path=target/'judgment.json',conversation=conversation,assess_recovery=row.get('kind')!='behavior',assess_interaction='session' in row or row.get('kind')=='behavior',
   diagnostic_inputs=diagnostic_inputs(turn,contract,judge_evidence(prep,messages,resolved,row,'isolated',diagnostic_version=DIAGNOSTIC_VERSION),diagnostic_version=DIAGNOSTIC_VERSION),diagnostic_version=DIAGNOSTIC_VERSION,judge_protocol=DATE_SCOPED_JUDGE_PROTOCOL)
  value={'id':row['id'],'at':row['at'],'contract_hash':contract.contract_hash,'turn':cap.binding(turn_path),'correct':grade.correct,'claims':[vars(c) for c in grade.slots],'criteria':len(contract.claims),'track':'native-practical-only','strict_original':json.loads((folder/'strict-memory.json').read_text()),'strict_credit_awarded':False}
  atomic(target/'grade.json',value);report['checks'].append(value);history[key_session]=extend_history(conversation,turn)
  atomic(output/'report.json',report)
 report['state']='retained-practical-grading-complete';report['coverage']={'questions':len(report['checks']),'criteria':sum(x['criteria'] for x in report['checks']),'correct':sum(x['correct'] for x in report['checks'])}
 atomic(output/'report.json',report);return report
