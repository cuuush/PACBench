import json
import hashlib
from pathlib import Path

import pytest

from pacbench.model_calls import ModelOutput
from pacbench.rejudge import prepare, run, audit, controls, publish_audit, validate_published_audit
from pacbench.semantic_grade import Claim, grade_semantic


def test_overlay_preserves_original_and_rejects_changed_inputs(tmp_path):
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
                   record_path=directory/'judgment.json')
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
