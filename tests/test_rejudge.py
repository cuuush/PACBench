import json
import hashlib
from pathlib import Path

import pytest

from pacbench.model_calls import ModelOutput
from pacbench.rejudge import prepare, run, audit, controls, publish_audit, validate_published_audit
from pacbench.semantic_grade import Claim, grade_semantic, RETRY_GUIDANCE


@pytest.mark.parametrize('protocol,suffix', [('semantic-judge-v1',''),
    ('semantic-judge-date-scope-v2',''), ('semantic-judge-date-scope-v2',RETRY_GUIDANCE)])
def test_overlay_preserves_original_and_rejects_changed_inputs(tmp_path, protocol, suffix):
    root = tmp_path/'run'
    directory = root/'repeat-1'/'memcal'/'turns'/'coffee'
    directory.mkdir(parents=True)
    calls = root/'model-calls'
    calls.mkdir()
    answer, question = 'Saturday', 'When?'
    (directory/'turn.json').write_text(json.dumps({'prompt':question,'answer':answer}))
    contract = root/'evaluations'/'coffee'/'contract.json'
    contract.parent.mkdir(parents=True)
    contract.write_text(json.dumps({'claims':[{'id':'day','statement':'Saturday','evidence':[]}]}))
    def original(prompt):
        text = json.dumps({'verdicts':[{'id':'day','verdict':'correct','reason':'Saturday.'}]})
        path = calls/'original.json'
        path.write_text(json.dumps({'status':'ok','text':text,'request':{'messages':[{'role':'user','content':prompt}]}}))
        return ModelOutput(text,'model',{},str(path))
    grade_semantic('coffee', question, answer, (Claim('day','Saturday'),), original,
                   record_path=directory/'judgment.json', judge_protocol=protocol, prompt_suffix=suffix)
    original_bytes = (directory/'judgment.json').read_bytes()
    overlay=root/'reviews'/'overlay'
    assert prepare(root, overlay) == 1
    def reviewer(prompt):
        verdict = 'misrepresented' if 'Answer: Saturday October 17.' in prompt or 'Answer: Saturday\n' in prompt else 'correct'
        result = {'verdicts':[{'id':'day','verdict':verdict,'reason':'Date checked.'}]}
        if 'evidence_diagnostic' in prompt:
            result['evidence_diagnostic'] = {'claims':[{'id':'day','status':'supported','reason':'Shown.',
                'citations':[{'id':'prefetch','quote':'Sat Oct 24'}]}], 'extra_assertions':[]}
        text = json.dumps(result)
        path = overlay/'model-calls'/(hashlib.sha256(prompt.encode()).hexdigest()+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'status':'ok','text':text,'request':{'messages':[{'role':'user','content':prompt}]}}))
        return ModelOutput(text,'model',{},str(path))
    controls(overlay,reviewer)
    run(overlay,reviewer)
    assert audit(overlay) == 1
    assert publish_audit(overlay) == 1
    assert validate_published_audit(overlay) == 1
    unsafe = json.loads((overlay/'manifest.json').read_text())
    old_manifest = (overlay/'manifest.json').read_text()
    unsafe['cases'][0]['files']['../../outside'] = 'fake'
    (overlay/'manifest.json').write_text(json.dumps(unsafe))
    with pytest.raises(ValueError): audit(overlay)
    (overlay/'manifest.json').write_text(old_manifest)
    (overlay/'escape').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'): audit(overlay)
    (overlay/'escape').unlink()
    assert (directory/'judgment.json').read_bytes() == original_bytes
    contract.write_text(contract.read_text()+' ')
    with pytest.raises(ValueError,match='original file changed'):
        audit(overlay)


def test_stop_waits_for_saved_case_and_prevents_retry_until_resumed(tmp_path, monkeypatch):
    import pacbench.rejudge as review
    overlay = tmp_path/'run'/'reviews'/'review'; overlay.mkdir(parents=True)
    cases = [{'judgment':'repeat-1/arm/turns/a/judgment.json'},
             {'judgment':'repeat-1/arm/turns/b/judgment.json'}]
    monkeypatch.setattr(review, '_manifest', lambda _:({'cases':cases}, tmp_path/'run'))
    monkeypatch.setattr(review, '_original', lambda root, rel: ({
        'checkpoint':rel.split('/')[-2], 'question':'When?', 'answer':'Saturday',
        'claims':[{'id':'day','description':'Saturday','evidence':[]}], 'prompt':'legacy'}, None))
    calls=[]
    def judge(prompt):
        calls.append(prompt)
        text = '{"verdicts":' if len(calls)==1 else json.dumps({'verdicts':[{'id':'day','verdict':'correct','reason':'Shown.'}]})
        directory=overlay/'model-calls';directory.mkdir(exist_ok=True)
        path=directory/f'call-{len(calls)}.json'
        path.write_text(json.dumps({'status':'ok','text':text,'request':{'messages':[{'role':'user','content':prompt}]}}))
        if len(calls)==1: (overlay/'STOP').write_text('stop')
        return ModelOutput(text,'model',{},str(path))
    assert review.run(overlay,judge)['state']=='stopped'
    case=overlay/'cases/arm/turns/a'
    first=(case/'attempt-1.json').read_bytes()
    assert len(calls)==1 and not (case/'result.json').exists()
    assert review.run(overlay,judge)['state']=='stopped' and len(calls)==1
    (overlay/'STOP').unlink()
    assert review.run(overlay,judge)['state']=='complete'
    assert len(calls)==3 and (case/'attempt-1.json').read_bytes()==first
    assert (case/'attempt-2.json').exists() and (case/'result.json').exists()


def test_stop_after_success_does_not_start_next_case(tmp_path, monkeypatch):
    import pacbench.rejudge as review
    overlay=tmp_path/'run/reviews/review';overlay.mkdir(parents=True)
    cases=[{'judgment':f'repeat-1/arm/turns/{name}/judgment.json'} for name in ('a','b')]
    monkeypatch.setattr(review,'_manifest',lambda _:({'cases':cases},tmp_path/'run'))
    monkeypatch.setattr(review,'_original',lambda root,rel:({'checkpoint':'a','question':'When?',
        'answer':'Saturday','claims':[{'id':'day','description':'Saturday','evidence':[]}],'prompt':'legacy'},None))
    calls=[]
    def judge(prompt):
        calls.append(prompt); (overlay/'STOP').write_text('stop')
        return json.dumps({'verdicts':[{'id':'day','verdict':'correct','reason':'Shown.'}]})
    assert review.run(overlay,judge)['state']=='stopped'
    assert len(calls)==1 and (overlay/'cases/arm/turns/a/result.json').exists()
    assert not (overlay/'cases/arm/turns/b').exists()


def test_review_stop_honors_inherited_operator_flag(tmp_path, monkeypatch):
    import pacbench.rejudge as review
    overlay = tmp_path/'run/reviews/review'; overlay.mkdir(parents=True)
    flag = tmp_path/'global-stop'; flag.touch()
    monkeypatch.setenv('PACBENCH_STOP_FILE', str(flag))
    monkeypatch.setattr(review, '_manifest', lambda _:({'cases':[{'judgment':'repeat-1/arm/turns/a/judgment.json'}]}, tmp_path/'run'))
    assert review.run(overlay,lambda _:pytest.fail('stopped before call'))['state'] == 'stopped'
