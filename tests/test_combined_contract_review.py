import json

import pytest

from pacbench.model_calls import ModelOutput
from pacbench.semantic_contract import build_contract, load_contract


def inputs(tmp_path, *, generate=None, verify=None):
    observation = {'source_id':'source-1','written_at':'2026-10-19T11:00:00-04:00',
                   'channel':'imessage','thread':'riley','sender':'Riley','text':'Coffee is at nine.'}
    return dict(scene='Coffee', test_intent='Coffee is at nine.', observations=[observation],
        generate=generate, verify=verify, question_generate=lambda _: pytest.fail('supplied question'),
        output_path=tmp_path/'contract.json', user_question='When is coffee?',
        question_at='2026-10-26T10:00:00-04:00')


def draft(prompt):
    return json.dumps({'claims':[{'id':'time','statement':'Coffee is at nine.','evidence':['s1']}]})


def accepted():
    return {'claim_review':{'complete':True,'coverage_reason':'Time is covered.',
        'reviews':[{'id':'time','supported':True,'relevant':True,'unambiguous':True,'reason':'Supported.'}]},
        'question_review':{'safe':True,'faithful':True,'elicits_all':True,'reason':'Fair.'}}


def test_one_draft_and_one_independent_review_with_real_response_binding(tmp_path):
    calls=[]
    def generate(prompt): calls.append(('draft',prompt)); return draft(prompt)
    def review(prompt):
        calls.append(('review',prompt))
        return ModelOutput(json.dumps(accepted()), 'model', {}, '/model-calls/one.json')
    contract=build_contract(**inputs(tmp_path,generate=generate,verify=review))
    assert [kind for kind,_ in calls]==['draft','review']
    assert 'CLAIM REVIEW:' in calls[1][1] and 'QUESTION REVIEW:' in calls[1][1]
    body=json.loads((tmp_path/'contract.json').read_text())
    assert body['authoring_version']=='event-scoped-v7'
    assert calls[1][1].count('source-1 | written 2026-10-19T11:00:00-04:00') == 1
    assert body['combined_review_receipt']=={'prompt_format':'shared-context-v2', 'prompt':calls[1][1], 'response':json.dumps(accepted()),
                                             'model_call':'/model-calls/one.json'}
    assert body['review']==accepted()['claim_review'] and body['question_review']==accepted()['question_review']
    assert load_contract(tmp_path/'contract.json')==contract


@pytest.mark.parametrize('mutation', ['missing_question','missing_claim','rejected_question','rejected_claim'])
def test_either_reviewer_component_fails_closed(tmp_path, mutation):
    response=accepted()
    if mutation=='missing_question':response.pop('question_review')
    if mutation=='missing_claim':response.pop('claim_review')
    if mutation=='rejected_question':response['question_review']['elicits_all']=False
    if mutation=='rejected_claim':response['claim_review']['reviews'][0]['supported']=False
    # Authored facts prohibit drafting a replacement; rejection must never freeze.
    args=inputs(tmp_path,generate=lambda p:json.dumps({'claims':[{'id':'fact-1','statement':'x','evidence':['s1']}]}),
                verify=lambda _:json.dumps(response))
    args['author_facts']=('Coffee is at nine.',)
    response.get('claim_review',{}).get('reviews',[{}])[0]['id']='fact-1'
    with pytest.raises(ValueError):build_contract(**args)
    assert not (tmp_path/'contract.json').exists()


def test_malformed_json_retry_binds_actual_retry_request(tmp_path):
    calls=[]
    def review(prompt):
        calls.append(prompt)
        return 'bad json' if len(calls)==1 else json.dumps(accepted())
    build_contract(**inputs(tmp_path,generate=draft,verify=review))
    receipt=json.loads((tmp_path/'contract.json').read_text())['combined_review_receipt']
    assert len(calls)==2 and receipt['prompt']==calls[1]
    assert 'previous response was not a valid JSON object' in calls[1]


def test_generated_question_keeps_separate_ordered_review_calls(tmp_path):
    calls=[]
    def review(prompt):
        calls.append('claim' if 'Review each proposed claim' in prompt else 'question')
        return json.dumps(accepted()['claim_review'] if calls[-1]=='claim' else {'safe':True,'faithful':True,'reason':'Safe.'})
    args=inputs(tmp_path,generate=draft,verify=review)
    args['user_question']=None
    args['question_generate']=lambda _: json.dumps({'question':'When is coffee?'})
    contract=build_contract(**args)
    assert calls==['claim','question']
    assert contract.question=='When is coffee?'
    assert 'combined_review_receipt' not in json.loads((tmp_path/'contract.json').read_text())


def test_behavior_keeps_its_existing_review_sequence(tmp_path):
    calls=[]
    def behavior_review(prompt):
        calls.append(prompt)
        return json.dumps({'complete':True,'reviews':[{'id':'fact-1','supported':True,'relevant':True,
            'unambiguous':True,'reason':'User instruction.','evidence':['s1']}]})
    args=inputs(tmp_path,generate=lambda _:pytest.fail('no draft needed'),verify=behavior_review)
    args.update(behavior=True,author_facts=('Acknowledges the change.',))
    build_contract(**args)
    assert len(calls)==1 and 'Review this scripted assistant interaction' in calls[0]
    assert 'combined_review_receipt' not in json.loads((tmp_path/'contract.json').read_text())


def prepared_review_fixture(tmp_path):
    from pacbench.preparation_audit import audit_combined_contract_review
    calls=tmp_path/'model-calls'; calls.mkdir()
    def reviewer(prompt):
        text=json.dumps(accepted())
        (calls/'review.json').write_text(json.dumps({'status':'ok','text':text,
            'request':{'messages':[{'role':'user','content':prompt}]}}))
        return ModelOutput(text,'model',{},str(calls/'review.json'))
    args=inputs(tmp_path,generate=draft,verify=reviewer)
    build_contract(**args)
    doc=json.loads((tmp_path/'contract.json').read_text())
    check={'id':'coffee','prompt':args['user_question'],'at':args['question_at'],
           'expect':args['test_intent'],'facts':[]}
    return audit_combined_contract_review,doc,args['observations'],check


@pytest.mark.parametrize('tamper', ['prompt','response','source','claim_review','question_review','model_call','question','facts','format','missing_format'])
def test_combined_review_audit_rejects_changed_receipt_or_frozen_input(tmp_path,tamper):
    audit,doc,observations,check=prepared_review_fixture(tmp_path)
    assert audit(tmp_path,doc,observations,check)==[]
    if tamper=='prompt':doc['combined_review_receipt']['prompt']+=' changed'
    if tamper=='response':doc['combined_review_receipt']['response']='{}'
    if tamper=='source':observations[0]['text']='Coffee is at ten.'
    if tamper=='claim_review':doc['review']['reviews'][0]['supported']=False
    if tamper=='question_review':doc['question_review']['safe']=False
    if tamper=='model_call':doc['combined_review_receipt']['model_call']='missing.json'
    if tamper=='question':check['prompt']='Where is coffee?'
    if tamper=='facts':check['facts']=['A different authored fact']
    if tamper=='format':doc['combined_review_receipt']['prompt_format']='unknown'
    if tamper=='missing_format':doc['combined_review_receipt'].pop('prompt_format')
    assert audit(tmp_path,doc,observations,check)


def test_original_v7_combined_review_receipt_remains_auditable(tmp_path):
    from pacbench.semantic_contract import ContractClaim, _dialogue, combined_review_prompt, contract_review_prompts
    audit, doc, observations, check = prepared_review_fixture(tmp_path)
    claims = tuple(ContractClaim(c['id'], c['statement'], tuple(c['evidence'])) for c in doc['claims'])
    prompts = contract_review_prompts(claims=claims, author_facts=(), dialogue=_dialogue(observations),
        question_at=check['at'], user_question=check['prompt'], test_intent=check['expect'])
    legacy = combined_review_prompt(*prompts)
    receipt = doc['combined_review_receipt']
    receipt.pop('prompt_format')
    receipt['prompt'] = legacy
    call_path = tmp_path/'model-calls'/'review.json'
    call = json.loads(call_path.read_text())
    call['request']['messages'][0]['content'] = legacy
    call_path.write_text(json.dumps(call))
    assert audit(tmp_path,doc,observations,check) == []
    receipt['prompt_format'] = 'shared-context-v2'
    assert audit(tmp_path,doc,observations,check)


@pytest.mark.parametrize('version',['event-scoped-v4','event-scoped-v5','event-scoped-v6'])
def test_old_contract_reviews_keep_legacy_audit(version,tmp_path):
    audit,doc,observations,check=prepared_review_fixture(tmp_path)
    doc['authoring_version']=version
    doc.pop('combined_review_receipt')
    assert audit(tmp_path,doc,observations,check)==[]


def test_three_scene_facts_are_checked_in_one_independent_request():
    from pacbench.authoring import ScenePlan, judge_scene
    plan=ScenePlan(id='coffee',scene='Coffee plans',facts=('Sunday','Nine','Maple Mug'),
        window_start='2026-10-19T10:00:00-04:00',window_end='2026-10-19T10:20:00-04:00')
    calls=[]
    def judge(prompt):
        calls.append(prompt)
        return json.dumps({'supported':True,'evidence':[1],'problem':'',
            'facts':[{'index':i,'supported':True,'evidence':[1],'problem':''} for i in range(1,4)]})
    judge_scene(plan,[('Riley','Coffee Sunday at nine at Maple Mug.')],judge)
    assert len(calls)==1 and all(fact in calls[0] for fact in plan.facts)
