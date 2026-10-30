"""Interrupted imports must not replay good data or overwrite answers."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pacbench.document_ingestion import VERSION,prepare_sources
from pacbench.followups import save
from pacbench.native_resume import ready_prefix
from tests.test_document_ingestion import message


def checkpoint(tmp_path):
    rows=[message('first'),{**message('second',stamp='2026-09-11T18:00:00-04:00'),
          'collected_at':'2026-09-11T23:00:00-04:00'}]
    schedule=prepare_sources(tmp_path,rows,'America/New_York')
    root=tmp_path/'repeat-1/openviking';row=schedule[0]
    meta=json.loads((tmp_path/'sources'/row['id']/'source.json').read_text())
    save(root/row['file'],{'provider':'openviking','ready':True,'state':'ready',
        'source_ids':row['source_ids'],'at':row['at'],'document_sha256':meta['document_sha256'],
        'result':{'result':{'result':{'status':'success','root_uri':'viking://resources/'+row['id'],
                                   'queue_status':{'Semantic':{'errors':[]}}}}}})
    return rows,root,{'ingestion_schedule':schedule,'ingestion_version':VERSION,
                     'process_timezone':'America/New_York'}


def test_resume_keeps_only_successful_prefix_and_rejects_changed_source(tmp_path):
    rows,root,report=checkpoint(tmp_path)
    assert ready_prefix(tmp_path,report,rows)==[('2026-09-10','viking://resources/2026-09-10')]
    (tmp_path/'sources/2026-09-10/messages.txt').write_text('Changed source')
    with pytest.raises(ValueError,match='source delivery changed'):ready_prefix(tmp_path,report,rows)


@pytest.mark.parametrize('damage',['failed-import','gap','native-error','prior-answer','prior-grade'])
def test_resume_rejects_partial_processing_or_prior_evaluation(tmp_path,damage):
    rows,root,report=checkpoint(tmp_path);path=root/report['ingestion_schedule'][0]['file']
    record=json.loads(path.read_text())
    if damage=='failed-import':record['ready']=False;save(path,record)
    if damage=='gap':
        row=report['ingestion_schedule'][1];save(root/row['file'],record);path.unlink()
    if damage=='native-error':
        record['result']['result']['result']['queue_status']['Semantic']['errors']=['failed'];save(path,record)
    if damage=='prior-answer':save(root/'turns/recall/turn.json',{'answer':'Already answered'})
    if damage=='prior-grade':save(root/'evaluation.json',{'checks':[{'verdict':'pass'}]})
    with pytest.raises(ValueError):ready_prefix(tmp_path,report,rows)


def test_resume_reopens_same_home_without_bootstrapping_new_memories(tmp_path):
    from pacbench.native_pilot import provision
    home=tmp_path/'hermes-home';home.mkdir();(home/'config.yaml').write_text('original config')
    with patch('pacbench.native_pilot.harness.provision_home') as bootstrap, \
         patch('provider_adapters.openviking_local.start',return_value=lambda:None):
        env=provision(tmp_path,'openviking','muse-spark-1.3-contributor',resume=True)
    bootstrap.assert_not_called()
    assert (home/'config.yaml').read_text()=='original config'
    assert env['PACBENCH_PROVIDER_ONLY']=='1'
    with pytest.raises(ValueError,match='Only an existing OpenViking'):
        provision(tmp_path,'mem0',resume=True)


def test_changed_frozen_identity_blocks_resume_before_backend_or_paid_work(tmp_path):
    from types import SimpleNamespace
    from pacbench.native_resume import resume
    rows,root,report=checkpoint(tmp_path)
    save(tmp_path/'summary.json',{**report,'state':'incomplete','source':'.',
        'results':[{'provider':'openviking','repetition':1}],
        'input_hash':'changed','contract_hash':'contract','bundle_hash':'bundle'})
    with patch('pacbench.native_pilot.read_prepared',return_value=(rows,
            SimpleNamespace(contract_hash='contract'),{'timezone':'America/New_York'})), \
         patch('pacbench.gateway_client.require_live_cost_boundary') as boundary, \
         patch('pacbench.native_pilot.provision') as backend:
        with pytest.raises(ValueError,match='frozen input identity'):resume(tmp_path)
    boundary.assert_not_called();backend.assert_not_called()


def test_resume_renews_deleted_workspace_only_before_any_native_session(tmp_path):
    import sqlite3
    from pacbench.native_resume import renew_empty_workspace
    from pacbench.context_isolation import VERSION
    root=tmp_path/'run';history=root/'resumes/1';history.mkdir(parents=True)
    save(root/'recall-workspace.json',{'version':VERSION,'workspace':str(tmp_path/'deleted')})
    workspace=renew_empty_workspace(root,history)
    assert workspace.is_dir() and list(workspace.iterdir())==[]
    assert json.loads((history/'recall-workspace.json').read_text())['workspace'].endswith('/deleted')
    home=root/'hermes-home';home.mkdir()
    with sqlite3.connect(home/'state.db') as db:
        db.execute('CREATE TABLE sessions (id TEXT)');db.execute("INSERT INTO sessions VALUES ('existing')")
    receipt=(root/'recall-workspace.json').read_bytes()
    with pytest.raises(RuntimeError,match='without ingestion conversations'):
        renew_empty_workspace(root,root/'resumes/2')
    assert (root/'recall-workspace.json').read_bytes()==receipt


@pytest.mark.parametrize('key', ['provenance_version', 'evidence_diagnostic_protocol'])
def test_resume_rejects_unknown_protocol_before_model_access(tmp_path, key):
    import json
    from pacbench.native_resume import resume
    (tmp_path / 'summary.json').write_text(json.dumps({key: 'unknown-protocol'}))
    with pytest.raises(ValueError, match='Unsupported'):
        resume(tmp_path)
