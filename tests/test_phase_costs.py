import hashlib,json
from pacbench.phase_costs import report

def test_shared_judge_deduplicated_and_unknown_charge_not_zero(tmp_path):
    c=tmp_path/'cohort/mem0';arm=c/'repeat-1/mem0';arm.mkdir(parents=True)
    (c/'summary.json').write_text(json.dumps({'mode':'native-import-validation','state':'processing-complete','results':[]}))
    key=hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12]
    calls=[dict(id='one',project=f'pacbench-{key}-mem0-memory',state='abandoned_unknown',accounted='0.17',input_tokens=None,output_tokens=None),dict(id='judge',project='pacbench-judge',state='settled',accounted='0.02',input_tokens=3,output_tokens=4)]
    r=report(calls,[c]);assert r['shared_judging']['requests']==1
    p=r['attempts'][0]['phases']['import_processing'];assert p['actual_reported_usd']=='0' and p['unresolved_bound_usd']=='0.17' and p['requests_missing_token_counts']==1
    assert r['attempts'][0]['score']['graded_questions']==0
    assert r['all_in_scope']['requests']==2


def test_stale_summary_uses_persisted_question_score_once(tmp_path):
    c=tmp_path/'cohort/honcho';arm=c/'repeat-1/honcho';f=arm/'turns/q/followups/recovery.json';f.parent.mkdir(parents=True)
    (c/'summary.json').write_text(json.dumps({'mode':'native','state':'running','results':[]}))
    f.write_text(json.dumps({'score':{'initial_correct':2,'max_points':3,'points':1}}))
    r=report([], [c]);s=r['attempts'][0]['score'];assert s['graded_questions']==1 and s['initial_correct_criteria']==2 and not s['complete']


def test_duplicate_ledger_ids_rejected():
    import pytest
    with pytest.raises(ValueError):report([{'id':'same'},{'id':'same'}],[])
