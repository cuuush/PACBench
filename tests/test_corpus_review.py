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


def test_v8_quotes_public_updates_but_rejects_future_or_conditional_inputs():
    from pacbench.corpus_review import validation_errors,review_prompt
    update={'id':'change','at':'2026-10-26T16:10:00-04:00','kind':'behavior',
            'prompt':'Move the pickup to Sunday at 11.'}
    recall={'id':'recall','at':'2026-10-26T17:00:00-04:00','prompt':'What is on Sunday?'}
    resolved={'scenes':[],'checks':[update,recall]}
    def assessment(check):
        return {'check_id':check['id'],'event_date':'2026-11-01','alternatives':['none'],
            'ambiguous':False,'source_evidence':[{'written_at':update['at'],'quote':update['prompt']}]}
    verdict={'assessments':[assessment(update),assessment(recall)]}
    assert validation_errors(verdict,[],resolved)==[]
    assert all(e['code']=='timestamp_quote_no_match' for e in
        validation_errors(verdict,[],resolved,version='source-realism-v7'))
    recall['at']='2026-10-26T16:00:00-04:00'
    assert validation_errors(verdict,[],resolved)==[
        {'path':'assessments[recall].source_evidence[0]','code':'source_after_question'}]
    update['trigger']='clarification'
    assert all(e['code']=='timestamp_quote_no_match' for e in validation_errors(verdict,[],resolved))
    assert 'clarification' in review_prompt([],resolved)
    assert 'conditional inputs' in review_prompt([],resolved)


def test_public_input_order_dependencies_and_cross_session_intended_state():
    from pacbench.corpus_review import validation_errors
    rows=[{'id':'first','at':'2026-10-26T16:00:00-04:00','prompt':'The pickup is Sunday at 11am.',
           'session':'one'},
          {'id':'second','at':'2026-10-26T16:00:00-04:00','prompt':'What about Sunday?',
           'session':'fresh'}]
    def verdict(target,evidence):
        return {'assessments':[{'check_id':r['id'],'event_date':'2026-11-01','alternatives':['none'],
            'ambiguous':False,'source_evidence':[{'written_at':evidence['at'],'quote':evidence['prompt']}]}
            for r in target]}
    resolved={'checks':rows}
    assert validation_errors(verdict(rows,rows[0]),[],resolved)==[]
    assert any(e['code']=='timestamp_quote_no_match' for e in validation_errors(verdict(rows,rows[1]),[],resolved))
    rows[0]['trigger']='clarification'
    rows[1]['requires']=['first']
    rows.append({'id':'third','at':'2026-10-26T17:00:00-04:00','prompt':'Latest plan?',
                 'requires':['second']})
    assert all(e['code']=='timestamp_quote_no_match' for e in validation_errors(verdict(rows,rows[1]),[],resolved))


def test_large_corpus_deadline_is_confined_and_does_not_change_review_request(tmp_path,monkeypatch):
    from pacbench.model_calls import ModelClient
    msg={'source_id':'m','written_at':'2026-10-01T09:00:00-04:00','collected_at':'2026-10-01T09:00:00-04:00',
         'channel':'imessage','thread':'devon','sender':'Devon','from_owner':False,'text':'Friday at seven.'}
    checks=[{'id':f'check-{i}','at':'2026-10-02T09:00:00-04:00','prompt':'When is it?'} for i in range(9)]
    resolved={'scenes':[],'checks':checks}
    verdict={'consistent':True,'questions_answerable':True,'natural_enough':True,'issues':[],
        'assessments':[{'check_id':c['id'],'event_date':'2026-10-02','source_evidence':[{'written_at':msg['written_at'],'quote':'Friday at seven.'}],
            'alternatives':['none'],'ambiguous':False} for c in checks]}
    seen=[]
    def completed(self,prompt):
        seen.append((self.timeout_s,prompt))
        path=tmp_path/'actual-call.json';path.write_text(json.dumps({'client_wall_deadline_s':self.timeout_s,'client_max_attempts':self.max_attempts}))
        return SimpleNamespace(text=json.dumps(verdict),record_path=str(path),usage={})
    monkeypatch.setattr(ModelClient,'__call__',completed)
    client=ModelClient(key='inert',model='offline',record_dir=tmp_path)
    result=review_corpus([msg],resolved,client,tmp_path)
    assert client.timeout_s==180 and client.max_attempts==2 and seen==[(900,review_prompt([msg],resolved))]
    assert result['execution_condition']=={'version':'corpus-review-real-deadline-v1','wall_deadline_s':900,'max_attempts':1}
