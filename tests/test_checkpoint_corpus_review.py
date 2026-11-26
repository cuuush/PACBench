import copy,json
from types import SimpleNamespace
import pytest
from pacbench.corpus.review import (
    checkpoint_prompt as prompt, checkpoint_errors as check_errors,
    review_checkpoints as review, audit_checkpoints as audit,
)
from pacbench.followups import save

@pytest.fixture
def case():
 m=[{'source_id':'booking','written_at':'2026-10-05T09:00:00-04:00','collected_at':'2026-10-05T09:00:00-04:00','text':'Class is Tuesday.'}]
 c={'id':'now','at':'2026-10-06T10:00:00-04:00','prompt':'Did I attend class?','facts':['SECRET']}
 r={'scenes':[],'checks':[{'id':'earlier','at':'2026-10-05T10:00:00-04:00','prompt':'I booked class.'},c,{'id':'future','at':'2026-10-07T09:00:00-04:00','prompt':'FUTURE_SECRET'}]}
 v={'consistent':True,'questions_answerable':True,'natural_enough':True,'issues':[], 'assessments':[{'check_id':'now','event_date':'2026-10-06','answerability':'bounded_unknown','assessable':True,'ambiguous':True,'current_state_conflict':False,'grounding_kind':'source_record','known_fields':['Class booked.'],'unresolved_fields':[{'attribute':'attendance','missing':'No outcome.','alternatives':['attended','absent']}],'reason':'Booking does not prove attendance.','full_eligible_history_reviewed':True,'alternatives':['attended','absent'],'source_evidence':[{'source_id':'booking','written_at':m[0]['written_at'],'quote':'Class is Tuesday.'}]}]}
 return m,r,c,v

def test_scoped_input_keeps_earlier_public_history_without_future_or_answers(case):
 m,r,c,v=case;p=prompt(m,r,c)
 assert 'I booked class.' in p and 'FUTURE_SECRET' not in p and 'SECRET' not in p
 assert not check_errors(v,m,r,c)

@pytest.mark.parametrize('mutation',['wrong_id','invented_quote','future','negative','missing','public_mismatch'])
def test_fail_closed(case,mutation):
 m,r,c,v=case;row=v['assessments'][0]
 if mutation=='wrong_id':row['check_id']='future'
 if mutation=='invented_quote':row['source_evidence'][0]['quote']='Someone cancelled.'
 if mutation=='future':row['source_evidence'][0].update(source_id='public-input:future',written_at=r['checks'][-1]['at'],quote='FUTURE_SECRET')
 if mutation=='negative':v['consistent']=False
 if mutation=='missing':v['assessments']=[]
 if mutation=='public_mismatch':row['grounding_kind']='explicit_public_report'
 assert check_errors(v,m,r,c)

def test_receipt_audit_and_rejected_resume(case,tmp_path):
 m,r,c,v=case;r['checks']=[c];(tmp_path/'model-calls').mkdir()
 def model(p):
  path=tmp_path/'model-calls'/'call.json';save(path,{'status':'ok','text':json.dumps(v),'request':{'messages':[{'role':'user','content':p}]}})
  return SimpleNamespace(text=json.dumps(v),record_path=str(path),usage={})
 q=review(m,r,model,tmp_path);assert not audit(tmp_path,m,r,q)
 changed=copy.deepcopy(m);changed[0]['text']='Changed.'
 assert audit(tmp_path,changed,r,q)
 path=tmp_path/'checkpoint-source-reviews'/'now.json';d=json.loads(path.read_text());d['state']='fail';save(path,d)
 with pytest.raises(ValueError,match='Rejected checkpoint'):review(m,r,model,tmp_path)

def test_locator_binding_retains_failed_raw_receipt_and_recomputes(case,tmp_path):
 from pacbench.corpus.review import bind_citation_locators as bind
 m,r,c,v=case;m[0]['source_id']='message-abcd2';v['assessments'][0]['source_evidence'][0]['source_id']='message-abcd'
 v['assessments'][0]['source_evidence'].append({**v['assessments'][0]['source_evidence'][0],'source_id':'message-abcd2'})
 raw=copy.deepcopy(v);receipt={'state':'fail','review':v,'validation_errors':check_errors(v,m,r,c)}
 view,binding=bind(receipt,m,r,c,b'rawreceipt',b'rawcall')
 assert v==raw and view['assessments'][0]['source_evidence'][0]['source_id']=='message-abcd2'
 assert binding['bindings'][0]['original_id']=='message-abcd'
 assert not check_errors(view,m,r,c)

@pytest.mark.parametrize('defect',['existing','ambiguous','different_id','quote','timestamp','future','negative','category'])
def test_locator_binding_cannot_repair_semantics_or_other_defects(case,defect):
 from pacbench.corpus.review import bind_citation_locators as bind
 m,r,c,v=case;m[0]['source_id']='message-abcd2';cite=v['assessments'][0]['source_evidence'][0];cite['source_id']='message-abcd'
 if defect=='existing':m.append({**m[0],'source_id':'message-abcd','text':'Different.'})
 if defect=='ambiguous':m.append({**m[0],'source_id':'message-another'})
 if defect=='different_id':cite['source_id']='message-wrong'
 if defect=='quote':cite['quote']='Invented.'
 if defect=='timestamp':cite['written_at']='2026-10-05T09:01:00-04:00'
 if defect=='future':m[0]['collected_at']='2026-10-07T09:00:00-04:00'
 if defect=='negative':v['consistent']=False
 if defect=='category':v['assessments'][0]['grounding_kind']='explicit_public_report'
 receipt={'state':'fail','review':v,'validation_errors':check_errors(v,m,r,c)}
 with pytest.raises(ValueError):bind(receipt,m,r,c,b'r',b'c')

def test_bound_protocol_audits_raw_call_and_derived_binding(case,tmp_path):
 from pacbench.corpus.review import BOUND_CHECKPOINT_VERSION as BOUND_VERSION
 m,r,c,v=case;r['checks']=[c];m[0]['source_id']='message-abcd2'
 cites=v['assessments'][0]['source_evidence'];cites[0]['source_id']='message-abcd';cites.append({**cites[0],'source_id':'message-abcd2'})
 (tmp_path/'model-calls').mkdir()
 def model(p):
  path=tmp_path/'model-calls'/'call.json';save(path,{'status':'ok','text':json.dumps(v),'request':{'messages':[{'role':'user','content':p}]}})
  return SimpleNamespace(text=json.dumps(v),record_path=str(path),usage={})
 q=review(m,r,model,tmp_path,locator_binding=True)
 assert q['review_version']==BOUND_VERSION and not audit(tmp_path,m,r,q)
 raw=json.loads((tmp_path/'checkpoint-source-reviews/now.json').read_text());assert raw['state']=='fail' and raw['review']==v
 binding=tmp_path/'citation-bindings/now.json';d=json.loads(binding.read_text());d['bindings'][0]['canonical_id']='message-incorrect';save(binding,d)
 assert audit(tmp_path,m,r,q)
