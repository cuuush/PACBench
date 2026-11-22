import copy,json,subprocess,sys,unittest
from pacbench import judge_development as p
from pacbench.semantic_grade import Claim,judge_prompt,CONTEXT_JUDGE_PROTOCOL

class PairedLunaTests(unittest.TestCase):
 def test_frozen_requests(self):
  s=p.spec();self.assertEqual(len(s['cases']),10);self.assertEqual(len(p.requests()),20)
  manifest=json.loads(p.MANIFEST.read_text())
  self.assertEqual([{'id':c['id'],'body_sha256':c['body_sha256']} for c in p.requests()],manifest['requests'])
  self.assertEqual(set(s['gold'].values()),{'correct','misrepresented'})
 def test_rubric_only_difference(self):
  cells={c['id']:c for c in p.requests()}
  for c in p.spec()['cases']:
   a=copy.deepcopy(cells[c['id']+':v4']['body']);b=copy.deepcopy(cells[c['id']+':explicit']['body'])
   old=judge_prompt(c['question'],c['answer'],(Claim('criterion',c['criterion']),),question_at=c['question_at'],conversation=c['conversation'],judge_protocol=CONTEXT_JUDGE_PROTOCOL)
   self.assertEqual(a.pop('messages'),[{'role':'user','content':old}])
   self.assertEqual(b.pop('messages'),[{'role':'user','content':old+'\nExperimental criterion rule '+c['explicit_rule_id']+': '+c['explicit_rule']}])
   self.assertEqual(a,b);self.assertEqual(a['model'],'openai/gpt-6-luna')
   self.assertEqual(a['provider'],{'only':['amazon-bedrock/global'],'order':['amazon-bedrock/global'],'allow_fallbacks':False,'require_parameters':True})
 def test_full_context_bound(self):
  c=dict(context_tokens=1050000,max_completion_tokens=32768,prompt_per_million='.25',completion_per_million='.50')
  self.assertEqual(p.bound(c)['batch_upper_bound_usd'],'5.577680')
  for k,v in [('context_tokens',1050001),('max_completion_tokens',32767),('prompt_per_million','.250001'),('completion_per_million','NaN')]:
   with self.assertRaises(ValueError):p.bound({**c,k:v})
 def observations(self):
  return [{'case_id':c['case_id'],'arm':c['arm'],'response':json.dumps({'verdicts':[{'id':'criterion','verdict':p.spec()['gold'][c['case_id']],'reason':'offline mocked control'}]})} for c in p.requests()]
 def test_offline_parser_and_pairs(self):
  a=p.evaluate_saved(self.observations());self.assertEqual(a['missing_cells'],[])
  self.assertEqual(a['breakdown']['scenario_families_both_sides_correct'],{'v4':5,'explicit':5})
 def test_rescues_and_new_false_credit(self):
  rows=self.observations()
  for r in rows:
   if r['case_id']=='diet_no_meat_0' and r['arm']=='v4':v='missed'
   elif r['case_id']=='diet_no_meat_1' and r['arm']=='explicit':v='correct'
   else:continue
   r['response']=json.dumps({'verdicts':[{'id':'criterion','verdict':v,'reason':'mock'}]})
  b=p.evaluate_saved(rows)['breakdown'];self.assertEqual(b['previously_missed_correct_rescued'],1);self.assertEqual(b['deliberately_wrong_newly_credited'],1)
 def test_invalid_abstain_and_missing_separate(self):
  rows=self.observations()[:2];rows[0]['response']='invalid';rows[1]['response']=json.dumps({'verdicts':[{'id':'criterion','verdict':'abstain','reason':'mock'}]})
  a=p.evaluate_saved(rows);self.assertEqual(a['breakdown']['invalid_outputs'],1);self.assertEqual(a['breakdown']['unsupported_abstentions'],1);self.assertEqual(len(a['missing_cells']),18)
  self.assertEqual(a['breakdown']['previously_missed_correct_rescued'],0)
 def test_duplicate_unknown_rejected(self):
  r=self.observations()[0]
  with self.assertRaises(ValueError):p.evaluate_saved([r,r])
  with self.assertRaises(ValueError):p.evaluate_saved([{**r,'case_id':'unknown-case'}])
 def test_live_fails_closed(self):
  r=subprocess.run([sys.executable,'-m','pacbench.judge_development','--live'],capture_output=True,text=True)
  self.assertNotEqual(r.returncode,0)

if __name__=='__main__':unittest.main()
