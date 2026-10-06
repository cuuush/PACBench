"""Artifact projection tests; synthetic traces do not measure memory performance."""
import json
import pytest
from pacbench.frozen import write_bundle
from scripts import build_explorer_data as explorer


def test_native_judges_followup_subset_and_private_files(tmp_path, monkeypatch):
    monkeypatch.setattr(explorer, 'ROOT', tmp_path)
    root = tmp_path / 'runs' / 'trial'
    root.mkdir(parents=True)
    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    pinned = write_bundle({'lives': {'example': {'messages': [{'source_id':'m1','text':'Synthetic'}]}}}, root / 'bundle.json')
    save(root / 'summary.json', {'mode':'native-hermes','bundle_hash':pinned})
    contract = {'question':'What happened?', 'claims':[{'id':'one','statement':'First fact'}, {'id':'two','statement':'Second fact'}]}
    save(root / 'contract.json', contract)
    arm = root / 'repeat-1' / 'mem0'
    (arm / 'hermes-home').mkdir(parents=True)
    (arm / 'hermes-home' / '.env').write_text('SECRET=should-never-be-exported')
    save(arm / 'recall.json', {'answer':'First fact','tool_calls':[]})
    save(arm / 'grade.json', {'claims':[{'id':'one','verdict':'correct'}, {'id':'two','verdict':'missed'}]})
    save(arm / 'followups/recovery.json', {'attempts':[{'claim_ids':['two'],'metrics':{'operations':[]}}]})
    save(arm / 'followups/combined/turn.json', {'answer':'Second fact','tool_calls':[]})
    save(arm / 'followups/combined/grade.json', {'claims':[{'id':'two','verdict':'correct'}]})
    save(root / 'model-calls/one.json', {'response':{'output':[{'encrypted_content':'opaque-secret'}]}})
    result = explorer.native_run(root)
    exchanges = result['arms']['mem0-repeat-1']['exchanges']
    assert len(exchanges) == 2
    assert [c['id'] for c in exchanges[1]['contract']['claims']] == ['two']
    assert exchanges[0]['grade']['claims'][1]['verdict'] == 'missed'
    exported = json.dumps(result)
    assert 'should-never-be-exported' not in exported and 'opaque-secret' not in exported
    save(root / 'summary.json', {'mode':'native-hermes','bundle_hash':'tampered'})
    with pytest.raises(SystemExit, match='bundle mismatch'):
        explorer.native_run(root)
