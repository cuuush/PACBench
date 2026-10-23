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
    current=review_prompt([],resolved)
    assert 'Scripted public user messages are real planned inputs when delivered' in current
    assert 'Do not assume the assistant successfully wrote memory' in current


def test_realism_rejection_is_preserved_and_stops_preparation(tmp_path):
    messages=[{'source_id':'m1','written_at':'2026-10-19T09:00:00-04:00',
               'sender':'Jen','text':'Friday is off.'}]
    resolved={'scenes':[], 'checks':[{'prompt':'Is Friday on?', 'expect':'Yes.'}]}
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
