"""Exact reviewed MemCal through native collectors/Dream; synthetic replies only."""
import json,re,hashlib,sqlite3
from pathlib import Path
import pytest
from tests.offline_native_wire import NativeWire
from pacbench.memcal_diagnostic import run_native,freeze_inputs

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/workspace/memcal-review-dac7214-import')
OLD='Avoids mushrooms; fine with dairy.'
NEW='Avoids dairy; mushrooms are fine.'


def native_env(wire,tmp):
 return {'HOME':str(tmp/'os-home'),'MEMCAL_SRC':str(SOURCE),'PACBENCH_MEMCAL_SRC':str(SOURCE),
   'PACBENCH_MEMCAL_CONDITION':'wiki-context-review-dac7214-v1','MEMCAL_LLM_PROVIDER':'openai-compatible',
   'MEMCAL_OPENAI_BASE_URL':wire.endpoint+'/p/offline-memory/v1','OPENAI_COMPAT_API_KEY':'offline-not-a-secret',
   'OPENAI_API_KEY':'offline-not-a-secret','OPENROUTER_API_KEY':'offline-not-a-secret',
   'MEMCAL_PROPOSE_MODEL':'openai/gpt-6-luna','MEMCAL_SWEEP_MODEL':'openai/gpt-6-luna','MEMCAL_MATCH_MODEL':'openai/gpt-6-luna',
   'MEMCAL_MAX_PARALLEL':'1','MEMCAL_FALLBACK_PROVIDER':'','PACBENCH_TRANSPORT':'openrouter','PACBENCH_DIRECT':'0',
   'PACBENCH_OPENROUTER_PROXY_URL':wire.endpoint,'PACBENCH_OPENROUTER_PROXY_TOKEN':'offline-not-a-secret',
   'PACBENCH_USAGE_ROLES':'{}','TZ':'America/New_York'}


def empty_review(body):
 suffix=body['messages'][1]['content'];ids=list(dict.fromkeys(re.findall(r'BUNDLE ID ([a-f0-9]+)',suffix)))
 return json.dumps({'reviewed':ids,'diffs':[]} if ids else {'merges':[],'actions':[]})


def test_six_original_inputs_expose_prior_self_context_and_reject_borrowed_stale_assertion(tmp_path):
 if not SOURCE.exists():pytest.skip('Exact private reviewed tree missing; live gate remains closed')
 state={};completed=[]
 def response(body):
  suffix=body['messages'][1]['content'];ids=list(dict.fromkeys(re.findall(r'BUNDLE ID ([a-f0-9]+)',suffix)))
  if not ids:return empty_review(body)
  rows=[];batch=state['batch'];phase=state['phase'];case=state['case']
  def claim(slot,value,text,cites,owner='me',supersedes=None):
   return {'page':owner,'section':'people','subject':owner,'subject_role':'speaker','durability':'lasting','claim_kind':'preference',
       'slot':slot,'value':value,'cites':cites,'assertion':{'text':text,'mode':'current'},'supersedes':supersedes}
  if phase=='monday':
   rows.append(claim('pizza_preferences',OLD,batch[0]['text'],['L1']))
   if case=='natural':
    rows.append(claim('coffee_at_work_preference','Avoids almond milk.',batch[1]['text'],['L2']))
    rows.append(claim('pizza_preferences','Avoids olives on pizza.',batch[2]['text'],['L3'],'Maya'))
  elif phase=='wednesday':
   rows.append(claim('dietary_preferences',NEW,batch[0]['text'],['L1'],supersedes={
       'slot':'pizza_preferences','previous':OLD,'prior_lines':[1],'scope':'same','quote':batch[0]['text']}))
  else:
   rows.append(claim('pizza_preferences',OLD,'For pizza, I avoid mushrooms; I’m fine with dairy.',['L1'],'Maya'))
  return json.dumps({'reviewed':ids,'diffs':[{'bundle':ids[0],'wiki':rows,'events':[],'todos':[],'questions':[],'threads':[]}]})
 wire=NativeWire(response);output=tmp_path/'native-six-inputs'
 try:
  record=run_native(ROOT,output,native_env(wire,tmp_path),before_phase=lambda c,p,b:state.update(case=c,phase=p,batch=b),
      after_phase=lambda c,p,s,r:completed.append((c,p)))
  assert len(record['phases'])==6 and all(p['ready'] for p in record['phases'])
  assert completed==[(c,p) for c in ['natural','same-scope-positive'] for p in ['monday','wednesday','friday']]
  assert len(wire.requests)==12
  for case in ['natural','same-scope-positive']:
   arm=output/case
   before=(arm/'monday/wiki/people/me.md').read_text();after=(arm/'wednesday/wiki/people/me.md').read_text();stale=(arm/'friday/wiki/people/me.md').read_text()
   assert OLD in before and NEW in after and OLD not in after and stale==after
   if case=='natural':
    assert 'almond milk' in after
    assert 'mushrooms' not in (arm/'friday/wiki/people/maya.md').read_text()
   with sqlite3.connect(arm/'friday/native-state.private.db') as db:
    dates=db.execute("SELECT evidence_ts FROM slot_history WHERE page='me' AND new_value=?",(NEW,)).fetchall()
    assert dates and dates[-1][0].startswith('2026-10-07')
   for phase in ['monday','wednesday','friday']:
    assert (arm/phase/'brief.md').exists()
  # Inspect actual HTTP requests: no product prompt construction is mocked.
  corrected=[r['messages'][1]['content'] for r in wire.requests if OLD in r['messages'][1]['content'] and 'PRIOR WIKI FACT CANDIDATES' in r['messages'][1]['content']]
  assert corrected and all('prior_lines=[1]' in text and 'archive #1' in text for text in corrected)
  quoted=[r['messages'][1]['content'] for r in wire.requests if 'L1 quotes wording found at archive #1' in r['messages'][1]['content']]
  assert len(quoted)==2 and all('original speaker=me' in text for text in quoted)
  (tmp_path/'captured-requests.private.json').write_text(json.dumps(wire.requests,indent=2))
 finally:wire.close()


def test_repaired_native_collector_and_dream_cover_frozen_r2_checkpoints(tmp_path,monkeypatch):
 if not SOURCE.exists():pytest.skip('Exact reviewed source missing')
 from pacbench.document_ingestion import prepare_sources
 from pacbench.imessage_membership import CONDITION
 from pacbench.native_checkpoint_sources import MemcalSourceCheckpoints
 from pacbench.observation_adapters import checkpoint_plan
 from pacbench.checkpoint_evaluator import verify_checkpoint
 preparation=ROOT/'runs/pizza-week-revised-20261115/cohort-corrected-v5-native-retry1/mem0'
 bundle=json.loads((preparation/'bundle.json').read_text());messages=next(iter(bundle['lives'].values()))['messages']
 fixture=tmp_path/'preparation';monkeypatch.setenv('PACBENCH_IMESSAGE_MEMBERSHIP_CONDITION',CONDITION)
 schedule=prepare_sources(fixture,messages,'America/New_York',native_fixtures=True)
 for batch in schedule:batch['document_sha256']=hashlib.sha256((fixture/batch['source_file']).read_bytes()).hexdigest()
 resolved=json.loads((preparation/'resolved-spec.json').read_text());contracts={r['id']:json.loads((preparation/'evaluations'/r['id']/'contract.json').read_text()) for r in resolved['checks']}
 plan=checkpoint_plan(resolved,schedule,'memcal',contracts=contracts)
 wire=NativeWire(empty_review);canonical=tmp_path/'canonical';canonical.mkdir();home=canonical/'hermes-home';(home/'plugins').mkdir(parents=True)
 (home/'plugins/memcal').symlink_to(SOURCE/'integrations/hermes/memcal',target_is_directory=True)
 env=native_env(wire,canonical);env['MEMCAL_HOME']=str(canonical/'memory');Path(env['HOME']).mkdir()
 source=MemcalSourceCheckpoints(fixture,canonical,schedule,env,ROOT/'vendor/venv-hermes-isolated/bin/python')
 try:
  records=[]
  for group in plan['groups']:
   proof=source.materialize(group,tmp_path/('snapshot-'+str(group['id'])))
   verify_checkpoint(group,proof,schedule,'memcal')
   closure=json.loads(Path(proof['native_closure']['evidence'][0]['path']).read_text());records.append(closure)
   assert closure['native_reopen']['archive_rows']==sum(len(b['source_ids']) for b in schedule[:len(group['ready_batch_ids'])])
  assert records[-1]['native_reopen']['archive_rows']==142 and len(records)==6 and plan['criteria']==188
  from collections import Counter
  compared=records[-1]['archive_source_readback'];counts=Counter(w['condition'] for w in compared['rows'])
  assert counts=={'exact':137,'line-ending-only':2,'native-text-transformation':3}
  assert compared['native_text_unchanged'] is False
  (tmp_path/'conformance.json').write_text(json.dumps({'state':'pass','model_quality_verified':False,'native_checkpoints':6,
      'archive_rows':142,'planned_questions':42,'planned_criteria':188,'questions_answered':0,'native_calls':len(wire.requests)}))
 finally:wire.close()
