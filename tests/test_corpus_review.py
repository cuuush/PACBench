"""Review binding and rejection behavior, without claims about model accuracy."""
import json
from types import SimpleNamespace

import pytest

from pacbench.corpus_review import review_corpus, review_prompt


def test_legacy_review_binding_is_unchanged_and_new_protocol_handles_scripted_inputs():
    import hashlib
    resolved={'scenes':[],'checks':[]}
    legacy=review_prompt([],resolved,version='source-realism-v3')
    assert hashlib.sha256(legacy.encode()).hexdigest()=='9dd33cc7e198102d1f795acd20a3a53047038a660007c9eadc4ed871d9af21b2'
    current=review_prompt([],resolved,version='source-realism-v6')
    assert 'Scripted public user turns are real inputs when delivered' in current
    assert 'never assume assistant memory writes' in current
    assert hashlib.sha256(review_prompt([],resolved,version='source-realism-v4').encode()).hexdigest()=='b7577efd4b11790092a54bb17b34ae776a18a614410d8eb356a2d02bd469f91d'
    assert hashlib.sha256(review_prompt([],resolved,version='source-realism-v5').encode()).hexdigest()=='8f30c6b20c930987c8cc3a487ae3b079e05575b48093ad42f03d3cdd4c982443'
    assert hashlib.sha256(current.encode()).hexdigest()=='35a69a3a1b0a00d175a2499793f3009008018c62a9fbe196daa7dfd08314c03d'


def test_realism_rejection_is_preserved_and_stops_preparation(tmp_path):
    messages=[{'source_id':'m1','written_at':'2026-10-19T09:00:00-04:00',
               'sender':'Jen','text':'Friday is off.','channel':'imessage','thread':'jen',
               'from_owner':False,'collected_at':'2026-10-19T09:00:00-04:00'}]
    resolved={'scenes':[], 'checks':[{'id':'question','at':'2026-10-19T10:00:00-04:00','prompt':'Is Friday on?', 'expect':'Yes.'}]}
    verdict={'consistent':True,'expectations_supported':False,'natural_enough':True,
             'issues':['The expected answer contradicts the cancellation.']}
    prompts=[]
    def model(prompt):
        prompts.append(prompt)
        return SimpleNamespace(text=json.dumps(verdict),record_path='actual-call.json',usage={})
    with pytest.raises(ValueError,match='Corpus realism review rejected'):
        review_corpus(messages,resolved,model,tmp_path)
    saved=json.loads((tmp_path/'realism-review.json').read_text())
    assert saved['state']=='fail' and saved['review']==verdict
    assert saved['model_call']=='actual-call.json'
    assert prompts==[review_prompt(messages,resolved)]
    changed=[{**messages[0],'text':'Friday is on.'}]
    assert review_prompt(changed,resolved)!=prompts[0]
    assert review_prompt(messages,{**resolved,'checks':[]})!=prompts[0]


def test_v6_requires_source_grounded_complete_temporal_assessments():
    from pacbench.corpus_review import review_prompt, verdict_passes
    msg={'source_id':'m','written_at':'2026-10-13T10:00:00-04:00',
         'collected_at':'2026-10-13T10:00:00-04:00','channel':'email','thread':'ben',
         'sender':'Ben','from_owner':False,'structured':{'email_subject':'Dinner'},
         'text':'Could we move dinner to Saturday, October 24?'}
    resolved={'scenes':[], 'checks':[{'id':'dinner','at':'2026-10-19T10:00:00-04:00',
                                       'prompt':'When?','expect':'Saturday'}]}
    prompt=review_prompt([msg],resolved,version='source-realism-v6')
    assert prompt.index(msg['text']) < prompt.index('Authored intentions')
    assert msg['written_at'] in prompt and 'Conversation: email | ben' in prompt
    assert 'Subject: Dinner' in prompt
    assessment={'check_id':'dinner','event_date':'2026-10-24',
                'source_evidence':[{'written_at':msg['written_at'],'thread':'ben',
                                    'quote':'Saturday, October 24'}],
                'alternatives':['none'],'ambiguous':False}
    verdict={'consistent':True,'expectations_supported':True,'natural_enough':True,
             'issues':[],'assessments':[assessment]}
    assert verdict_passes(verdict,[msg],resolved,version='source-realism-v6')
    for altered in ({**verdict,'assessments':[]},
                    {**verdict,'assessments':[{**assessment,'ambiguous':True}]},
                    {**verdict,'assessments':[{**assessment,'source_evidence':[
                        {'written_at':msg['written_at'],'thread':'ben','quote':'invented'}]}]},
                    {**verdict,'assessments':[assessment,assessment]}):
        assert not verdict_passes(altered,[msg],resolved,version='source-realism-v6')
    later={**msg,'collected_at':'2026-10-20T10:00:00-04:00'}
    assert not verdict_passes(verdict,[later],resolved,version='source-realism-v6')


def test_v7_blinds_authored_intent_and_checks_unique_citations():
    from pacbench.corpus_review import review_prompt, verdict_passes
    msg={'source_id':'m1','written_at':'2026-10-13T10:00:00-04:00',
         'collected_at':'2026-10-13T10:00:00-04:00','channel':'email','thread':'opaque',
         'sender':'Ben','from_owner':False,'structured':{'email_subject':'Dinner'},
         'text':'Could we move dinner to Saturday, October 24?'}
    resolved={'timezone':'America/New_York',
              'scenes':[{'id':'scene','at':msg['written_at'],'thread':'secret-thread',
                         'scene':'SECRET_SCENE', 'facts':['SECRET_SCENE_FACT']}],
              'checks':[{'id':'dinner','at':'2026-10-19T10:00:00-04:00',
                         'prompt':'When is dinner?', 'expect':'SECRET_EXPECTATION',
                         'facts':['SECRET_CHECK_FACT']}]}
    prompt=review_prompt([msg],resolved)
    assert all(x not in prompt for x in ('SECRET_SCENE','SECRET_SCENE_FACT',
                                          'SECRET_EXPECTATION','SECRET_CHECK_FACT','secret-thread'))
    assert msg['text'] in prompt and msg['written_at'] in prompt
    assert 'When is dinner?' in prompt and resolved['checks'][0]['at'] in prompt
    item={'check_id':'dinner','event_date':'2026-10-24',
          'source_evidence':[{'written_at':msg['written_at'],'quote':'Saturday, October 24'}],
          'alternatives':['none'],'ambiguous':False}
    verdict={'consistent':True,'questions_answerable':True,'natural_enough':True,
             'issues':[],'assessments':[item]}
    assert verdict_passes(verdict,[msg],resolved)
    duplicate={**msg,'source_id':'m2','thread':'another'}
    assert not verdict_passes(verdict,[msg,duplicate],resolved)
    from pacbench.corpus_review import validation_errors
    assert validation_errors(verdict,[msg,duplicate],resolved)==[
        {'path':'assessments[dinner].source_evidence[0]','code':'timestamp_quote_ambiguous_match'}]
    assert not verdict_passes({**verdict,'assessments':[{**item,'ambiguous':True}]},[msg],resolved)
    assert not verdict_passes({**verdict,'assessments':[]},[msg],resolved)


def test_v7_receipt_reports_bad_quote_without_altering_model_verdict(tmp_path):
    msg={'source_id':'m1','written_at':'2026-10-13T10:00:00-04:00',
         'collected_at':'2026-10-13T10:00:00-04:00','channel':'email','thread':'opaque',
         'sender':'Ben','from_owner':False,'text':'Saturday works.'}
    resolved={'scenes':[],'checks':[{'id':'dinner','at':'2026-10-19T10:00:00-04:00',
                                     'prompt':'When?','expect':'Saturday'}]}
    verdict={'consistent':True,'questions_answerable':True,'natural_enough':True,'issues':[],
             'assessments':[{'check_id':'dinner','event_date':'2026-10-24',
                 'source_evidence':[{'written_at':msg['written_at'],'quote':'Saturday surely works.'}],
                 'alternatives':['none'],'ambiguous':False}]}
    def model(_): return SimpleNamespace(text=json.dumps(verdict),record_path='call.json',usage={})
    with pytest.raises(ValueError,match='Corpus realism review rejected'):
        review_corpus([msg],resolved,model,tmp_path)
    saved=json.loads((tmp_path/'realism-review.json').read_text())
    assert saved['review']==verdict and saved['review']['issues']==[]
    assert saved['validation_errors']==[{'path':'assessments[dinner].source_evidence[0]',
                                         'code':'timestamp_quote_no_match'}]
