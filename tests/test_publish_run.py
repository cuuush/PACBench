"""Portable evidence and privacy tests, with synthetic fixtures only."""
import json
from pathlib import Path
import subprocess

import pytest
from pacbench import publish_run
from pacbench.followups import save


@pytest.fixture
def repo(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir();(root/'artifacts').mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True,capture_output=True)
    (root/'.gitignore').write_text('/artifacts/\n/runs/**/hermes-home/\n/runs/**/model-calls/private/\n')
    monkeypatch.setattr(publish_run,'REPO',root)
    return root


def component(repo,name,provider):
    root=repo/'artifacts'/name
    save(root/'summary.json',{'mode':'native-hermes','state':'diagnostic','input_hash':'same-input',
        'bundle_hash':'same-bundle','contract_hash':'same-contract','tool_policy':'same-policy',
        'source':'preparation','results':[{'provider':provider,'repetition':1,'state':'diagnostic'}],
        'ingestion_schedule':[]})
    for file in ('bundle.json','contract.json','resolved-spec.json','evaluation-plan.json'):
        save(root/file,{'synthetic':True})
    save(root/'native-audit.json',{'state':'pass','errors':[]})
    save(root/'preparation/spec.json',{'synthetic':True})
    save(root/'repeat-1'/provider/'status.json',{'state':'diagnostic'})
    save(root/'repeat-1'/provider/'hermes-home/credentials.json',{'private':'DO-NOT-PUBLISH'})
    save(root/'model-calls/same.json',{'text':'different actual call from '+name})
    save(root/'model-calls/private/replay.json',{'private':'DO-NOT-PUBLISH'})
    return root


def test_publishing_preserves_colliding_model_records_and_excludes_private_state(repo):
    first=component(repo,'core','memcal');second=component(repo,'other','honcho')
    output=publish_run.publish([first,second],repo/'runs/current')
    summary=json.loads((output/'summary.json').read_text())
    assert [r['provider'] for r in summary['results']]==['memcal','honcho']
    assert json.loads((output/'model-calls/same.json').read_text())['text'].endswith('core')
    assert json.loads((output/'components/other/model-calls/same.json').read_text())['text'].endswith('other')
    assert summary['component_runs'][1]=={'original_root':str(second),'public_root':'components/other',
        'selected_providers':['honcho']}
    assert not list(output.rglob('hermes-home')) and not list(output.rglob('private'))
    assert 'DO-NOT-PUBLISH' not in ''.join(p.read_text() for p in output.rglob('*.json'))


@pytest.mark.parametrize('change',['hash','file','duplicate','unfinished'])
def test_publishing_rejects_mixed_or_unfinished_evidence_without_partial_public_run(repo,change):
    first=component(repo,'first','memcal');second=component(repo,'second','honcho')
    s=json.loads((second/'summary.json').read_text())
    if change=='hash':s['input_hash']='different'
    elif change=='file':save(second/'bundle.json',{'synthetic':'different'})
    elif change=='duplicate':s['results'][0]['provider']='memcal'
    elif change=='unfinished':s['state']='running'
    save(second/'summary.json',s)
    with pytest.raises(ValueError):publish_run.publish([first,second],repo/'runs/current')
    assert not (repo/'runs/current').exists()


def test_native_canary_failure_is_kept_separate_from_month_answers(repo):
    first=component(repo,'core','memcal');canary=component(repo,'canary','supermemory')
    save(first/'evaluation-plan.json',{'checks':[{'id':'question','at':'2026-10-15T10:00:00-04:00','session':'fresh'}]})
    status=json.loads((canary/'summary.json').read_text());status.update(state='incomplete',input_hash='different-canary-input')
    status['results'][0].update(state='partial',capability={'available':True,'wiring_verified':True})
    save(canary/'summary.json',status)
    output=publish_run.publish([first],repo/'runs/current',preflight=canary)
    summary=json.loads((output/'summary.json').read_text());blocked=summary['results'][-1]
    assert blocked['state']=='preflight-failed' and blocked['provider']=='supermemory'
    assert blocked['preflight_input_hash']=='different-canary-input'
    assert blocked['expected_month_input_hash']=='same-input'
    evaluation=json.loads((output/'repeat-1/supermemory/evaluation.json').read_text())
    assert evaluation['counts']=={'pass':0,'fail':0,'ungraded':1}
    assert not list((output/'repeat-1/supermemory').rglob('turn.json'))
    assert (output/'preflight/summary.json').exists()


def test_healthy_canary_cannot_be_silently_omitted_from_month(repo):
    first=component(repo,'core','memcal');canary=component(repo,'canary','honcho')
    with pytest.raises(ValueError,match='healthy preflight'):
        publish_run.publish([first],repo/'runs/current',preflight=canary)
    assert not (repo/'runs/current').exists()


def test_explicit_replacement_keeps_old_attempt_separate_and_removes_stale_files(repo):
    first=component(repo,'interrupted','memcal');second=component(repo,'fresh','memcal')
    stopped=json.loads((first/'summary.json').read_text())
    stopped.update(state='interrupted',interruption='Original worker ended',spending_stop='Original stop')
    save(first/'summary.json',stopped)
    old=first/'repeat-1/memcal'
    save(old/'followups/old-only.json',{'original':'preserve separately'})
    output=publish_run.publish([first,second],repo/'runs/current',selection={'memcal':second})
    summary=json.loads((output/'summary.json').read_text())
    assert len(summary['results'])==1
    assert summary['state']=='diagnostic'
    assert 'interruption' not in summary and 'spending_stop' not in summary
    original=json.loads((output/'components/interrupted/summary.json').read_text())
    assert original['interruption']=='Original worker ended' and original['spending_stop']=='Original stop'
    assert summary['component_runs'][0]['selected_providers']==[]
    assert not (output/'repeat-1/memcal/followups/old-only.json').exists()
    attempt=summary['other_attempts'][0]
    assert attempt['selected_root']==str(second)
    assert (output/attempt['evidence']/'followups/old-only.json').exists()
    with pytest.raises(ValueError,match='selection'):
        publish_run.publish([first,second],repo/'runs/invalid',selection={'other':first})
    with pytest.raises(ValueError,match='Duplicate component'):
        publish_run.publish([first,first],repo/'runs/duplicate',selection={'memcal':first})
