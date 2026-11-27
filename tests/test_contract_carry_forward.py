import copy,hashlib
import pytest
from pacbench.corpus.contract import (
    validate_carry_forward as validate, APPROVED_QUERY_SUBSTITUTIONS as APPROVED, CARRY_FORWARD_VERSION as VERSION,
)
from pacbench.evidence_store import binding_digest as digest


def case():
 key='rsvp-before';a,b=APPROVED[key]
 old=[{'source_id':'raw','text':'Actual source','sender':'Me'},
      {'source_id':'assistant-user:'+key,'channel':'assistant','thread':'earlier','sender':'Me','text':'[assistant session earlier] '+a}]
 new=copy.deepcopy(old);new[-1]['text']='[assistant session earlier] '+b
 check={'id':'later','session':'different','prompt':'unchanged?'}
 doc={'contract_hash':'immutable','evidence_hash':digest(old),'claims':[{'id':'one','evidence':['raw']}]}
 change={'source_id':old[-1]['source_id'],'before':old[-1]['text'],'after':new[-1]['text']}
 w={'version':VERSION,'status':'carried-forward-equivalent-inputs','check':check,'corpus_hash':'same',
    'contract_hash':'immutable','policy_sha256':hashlib.sha256(b'policy').hexdigest(),'model_sha256':hashlib.sha256(b'actual receipt').hexdigest(),
    'old_evidence_hash':digest(old),'new_evidence_hash':digest(new),'changes':[change],
    'authorized_query_substitutions':{change['source_id']:{**change,'reason':'Explicit authorized RSVP wording repair; no new state'}}}
 return w,old,new,check,doc

def test_exact_query_only_equivalence_keeps_original_review_input():
 w,a,b,q,d=case();assert validate(w,a,b,q,d,b'policy',b'actual receipt','same')==a

@pytest.mark.parametrize('mutation',['version','status','source','corpus','check','policy','model','cited','same-session','query','metadata','ordering','diff','contract'])
def test_fail_closed_equivalence_witness(mutation):
 w,a,b,q,d=case();policy=b'policy';model=b'actual receipt';corpus='same'
 if mutation=='version':w['version']='unknown'
 if mutation=='status':w['status']='new-review-passed'
 if mutation=='source':b[0]['text']='modified source'
 if mutation=='corpus':corpus='changed'
 if mutation=='check':q={**q,'prompt':'changed?'}
 if mutation=='policy':policy=b'changed'
 if mutation=='model':model=b'changed'
 if mutation=='cited':d['claims'][0]['evidence'].append(a[-1]['source_id'])
 if mutation=='same-session':q['session']='earlier'
 if mutation=='query':b[-1]['text']='[assistant session earlier] I changed my plans.'
 if mutation=='metadata':b[-1]['thread']='changed'
 if mutation=='ordering':b.reverse()
 if mutation=='diff':w['changes']=[]
 if mutation=='contract':d['contract_hash']='changed'
 w['new_evidence_hash']=digest(b)
 with pytest.raises(ValueError):validate(w,a,b,q,d,policy,model,corpus)

def test_attribute_anchor_protocol_is_explicit_and_legacy_unchanged():
 from pacbench.corpus.contract import ContractClaim,shared_context_review_prompt
 kwargs=dict(claims=[ContractClaim('time','The October 9 lunch starts at12:30.',('raw',))],
     author_facts=(),dialogue='raw',question_at='2026-10-08',user_question='What is the lunch time?',test_intent='Recall time')
 old=shared_context_review_prompt(**kwargs);new=shared_context_review_prompt(**kwargs,review_version='event-scoped-v8-attribute-anchors')
 assert 'target identification' not in old and 'target identification' in new
 assert 'must split' in new and 'unsupported commitments' in new and 'Grounded unknowns' in new
 with pytest.raises(ValueError):shared_context_review_prompt(**kwargs,review_version='unknown')

def test_scoped_expectation_can_constrain_explicit_atomic_facts(tmp_path):
 from tests.test_scene_local_authoring import week,exact
 from pacbench.corpus.prepare import resolve_spec
 spec=week([exact()]);q=spec['days']['sunday']['conversations'][0]['turns'][0]
 q['expect']='Required Saturday outing only; Morgan’s stay is optional.'
 q['facts']=['The outing starts at 2 pm.','The outing ends at 2:30 pm.']
 checks=resolve_spec(spec,tmp_path)[2]['checks']
 assert checks[0]['expect']==q['expect'] and checks[0]['facts']==q['facts']

def test_native_copy_keeps_historical_model_receipts_separate_from_paid_arm_calls(tmp_path):
 import json
 from pacbench.corpus.contract import copy_review_evidence
 source=tmp_path/'source';dest=tmp_path/'dest';dest.mkdir()
 for folder in ['carry-forward','evaluations/later','model-calls']:(source/folder).mkdir(parents=True)
 (source/'carry-forward/later.json').write_text('{}')
 (source/'evaluations/later/contract.json').write_text(json.dumps({'combined_review_receipt':{'model_call':'/old/model-calls/actual.json'}}))
 (source/'model-calls/actual.json').write_text('{"original":true}')
 copy_review_evidence(source,dest)
 assert (dest/'carry-forward/later.json').read_bytes()==(source/'carry-forward/later.json').read_bytes()
 assert (dest/'contract-review-model-calls/actual.json').read_bytes()==(source/'model-calls/actual.json').read_bytes()
 assert not (dest/'model-calls').exists()
