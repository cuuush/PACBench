"""Offline, development-only Luna rubric comparison; no model transport."""
import argparse
import hashlib
import json
from pathlib import Path
from decimal import Decimal
from pacbench.semantic_grade import Claim, judge_prompt, grade_semantic, CONTEXT_JUDGE_PROTOCOL
from pacbench.openrouter_route import with_route
from pacbench.direct_accounting import request_cost_bound

FIXTURE = Path(__file__).resolve().parents[1]/'tests/fixtures/luna_judge_development_v1.json'
MANIFEST = FIXTURE.with_name('luna_judge_development_requests_v1.json')

def sha(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def spec():
 fixture=json.loads(FIXTURE.read_text())
 manifest=json.loads(MANIFEST.read_text())
 if hashlib.sha256(FIXTURE.read_bytes()).hexdigest()!=manifest['fixture_sha256']:
  raise ValueError('development fixture changed')
 return {'cases':fixture['cases'],'gold':{c['id']:c['gold'] for c in fixture['cases']},'model':'openai/gpt-6-luna'}

def requests():
 value=spec();cells=[]
 for i,c in enumerate(value['cases']):
  old=judge_prompt(c['question'],c['answer'],(Claim('criterion',c['criterion']),),question_at=c['question_at'],conversation=c['conversation'],judge_protocol=CONTEXT_JUDGE_PROTOCOL)
  # Order counterbalanced by case; no evidence spans, alternative output schema or new abstention rules.
  for arm in (('v4','explicit') if i%2==0 else ('explicit','v4')):
   prompt=old if arm=='v4' else old+'\nExperimental criterion rule '+c['explicit_rule_id']+': '+c['explicit_rule']
   body=with_route({'model':value['model'],'messages':[{'role':'user','content':prompt}],'response_format':{'type':'json_object'},'stream':True,'stream_options':{'include_usage':True},'max_completion_tokens':32768},profile='luna-bedrock-global')
   cells.append({'id':c['id']+':'+arm,'case_id':c['id'],'arm':arm,'body':body,'body_sha256':sha(body)})
 return cells

def bound(catalog):
 # Use full context, not character/token estimates. Conservative independent maxima.
 for name,limit in [('context_tokens',1050000),('max_completion_tokens',32768)]:
  if type(catalog.get(name)) is not int or catalog[name]<=0:raise ValueError('invalid endpoint limit')
  if name=='context_tokens' and catalog[name]>limit:raise ValueError('context ceiling increased')
  if name=='max_completion_tokens' and catalog[name]<limit:raise ValueError('established output cap unsupported')
 for k,maximum in [('prompt_per_million',Decimal('.25')),('completion_per_million',Decimal('.50'))]:
  v=Decimal(str(catalog[k]))
  if not v.is_finite() or not 0<=v<=maximum:raise ValueError('price outside proposed ceiling')
 config={**catalog,'prompt_per_million':'.25','completion_per_million':'.50'}
 per=request_cost_bound(config,32768,shared_context=False)
 return {'per_attempt_usd':str(per),'maximum_attempts':20,'batch_upper_bound_usd':str(per*20),'condition':'fresh exact-route catalog validation and unchanged ordinary accounting; not a price-list estimate or spend authorization'}

def evaluate_saved(observations):
 value=spec();byid={c['id']:c for c in value['cases']};expected={c['id'] for c in requests()};seen=set();out=[]
 for o in observations:
  ident=o['case_id']+':'+o['arm']
  if ident not in expected or ident in seen:raise ValueError('unknown/duplicate cell')
  seen.add(ident);c=byid[o['case_id']]
  # The semantic parser is unchanged for both arms. It does not generate a call.
  try:
   g=grade_semantic(ident,c['question'],c['answer'],(Claim('criterion',c['criterion']),),lambda _:o['response'],question_at=c['question_at'],judge_protocol=CONTEXT_JUDGE_PROTOCOL)
   verdict=g.slots[0].verdict
   out.append({'cell':ident,'verdict':verdict,'gold':value['gold'][c['id']],'agrees':verdict==value['gold'][c['id']],'invalid':False})
  except ValueError:
   # An unsolicited abstain remains rejected by the unchanged v4 parser,
   # but is reported separately from other malformed output.
   try:
    raw=json.loads(o['response']);vs=raw['verdicts']
    abstained=len(vs)==1 and vs[0].get('id')=='criterion' and vs[0].get('verdict')=='abstain'
   except (ValueError,TypeError,KeyError,AttributeError):abstained=False
   out.append({'cell':ident,'verdict':None,'gold':value['gold'][c['id']],'invalid':not abstained,'abstained':abstained,'parser_accepted':False})
 return {'breakdown':breakdown(out,value),'cells':out,'missing_cells':sorted(expected-seen),'provenance':'offline parse only; supplied results require separate request/receipt audit'}

def breakdown(rows,value):
 bycell={r['cell']:r for r in rows};rescues=[];new_wrong=[]
 families={c['scenario'] for c in value['cases']}
 both={arm:[] for arm in ('v4','explicit')}
 for c in value['cases']:
  old=bycell.get(c['id']+':v4',{});new=bycell.get(c['id']+':explicit',{})
  if value['gold'][c['id']]=='correct' and old.get('verdict')=='missed' and new.get('verdict')=='correct':rescues.append(c['id'])
  if value['gold'][c['id']]=='misrepresented' and old.get('verdict') in ('missed','misrepresented') and new.get('verdict')=='correct':new_wrong.append(c['id'])
 for arm in both:
  for family in sorted(families):
   cases=[c for c in value['cases'] if c['scenario']==family]
   if all(bycell.get(c['id']+':'+arm,{}).get('verdict')==value['gold'][c['id']] for c in cases):both[arm].append(family)
 return {'previously_missed_correct_rescued':len(rescues),'rescued_case_ids':rescues,
  'deliberately_wrong_newly_credited':len(new_wrong),'new_false_credit_case_ids':new_wrong,
  'scenario_families_both_sides_correct':{arm:len(v) for arm,v in both.items()},'family_ids':both,
  'invalid_outputs':sum(r.get('invalid',False) for r in rows),'unsupported_abstentions':sum(r.get('abstained',False) for r in rows),
  'scope':'observed cells only; invalid/missing/abstaining baseline is not evidence of a rescue or new false credit'}

def main():
 parser=argparse.ArgumentParser(description=__doc__)
 options=parser.add_mutually_exclusive_group(required=True)
 options.add_argument('--plan',type=Path)
 options.add_argument('--evaluate',type=Path)
 args=parser.parse_args()
 if args.evaluate:
  print(json.dumps(evaluate_saved(json.loads(args.evaluate.read_text())),indent=2));return
 cells=requests()
 expected=json.loads(MANIFEST.read_text())['requests']
 if [{'id':c['id'],'body_sha256':c['body_sha256']} for c in cells]!=expected:
  raise ValueError('frozen request manifest differs')
 with args.plan.open('x') as output:
  json.dump({'protocol':'luna-judge-development-v1','requests':cells},output,indent=2)
 print(json.dumps({'calls_planned':len(cells),'dispatches':0}))

if __name__=='__main__':main()
