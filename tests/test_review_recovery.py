import json
import pytest
from pacbench.workflow_metrics import summarize


def test_torn_utf8_tail_keeps_prior_stop(tmp_path):
    path = tmp_path/'workflow.jsonl'
    stop = {'phase':'stopped','elapsed_s':5,'error':'NativeFailure','automatic_replay':False}
    prefix = (json.dumps(stop)+'\n').encode()
    tail = b'{"phase":"\xe2'
    path.write_bytes(prefix + tail)
    report = summarize(path)
    assert report['status'] == 'stopped'
    assert report['terminal_event'] == stop
    assert report['incomplete_tail']['offset'] == len(prefix)
    assert path.read_bytes() == prefix + tail
    path.write_bytes(prefix + tail + b'\n' + prefix)
    with pytest.raises(ValueError):
        summarize(path)


def test_document_close_crash_preserves_grade_queue_and_denominator(tmp_path):
    from tests.test_workflow_realism import condition
    from pacbench.workflow_conformance import RecordingAccount
    from pacbench.workflow_runner import run_current_week
    from pacbench.workflow_progress import WorkflowProgress
    from pacbench.workflow_spec import compile_week
    _, docs, turns, _ = compile_week('continuing-account')
    route = condition(p='reme')

    class Interrupted(RecordingAccount):
        def end_chat(self, session_id):
            raise RuntimeError('ambiguous close')

    dest = tmp_path/'workflow'
    with pytest.raises(RuntimeError, match='ambiguous close'):
        run_current_week(route, Interrupted(route, docs), dest)
    key = json.loads((dest/'run.json').read_text())['run_key']
    with WorkflowProgress(dest, key) as progress:
        turn, surface, activity = progress.get_verified(turns[0].id)
        assert surface['answer'] and activity['driver_receipts']
        assert progress.claim_grade() == (turn, surface)
        assert sum(row['planned_criteria'] for row in progress.summary().values()) == 192
        assert not progress.complete
    with pytest.raises(ValueError, match='resume'):
        run_current_week(route, Interrupted(route, docs), dest, resume=True)


def test_private_export_checks_grade_and_returns_only_counts(tmp_path):
    from pacbench.workflow_answer_export import comparison_row, export
    from pacbench.workflow_grading import grade_verified
    from tests.test_workflow_grading import checkpoints, Recorder, local_judge
    from pacbench.workflow_stream import fingerprint
    from pacbench.workflow_progress import WorkflowProgress
    run = tmp_path/'workflow'
    with checkpoints(run, verified=1) as (p, turns):
        grade_verified(p, local_judge(run, []), run, Recorder(), limit=1)
        identity = {'condition':'fixture'}
        p.bind('identity', identity)
        p.db.execute('UPDATE run SET run_key=?', (fingerprint(identity),))
        (run/'run.json').write_text(json.dumps({'identity':identity,'run_key':fingerprint(identity)}))
    out = tmp_path/'out'
    summary = export(run, out)
    row = json.loads((out/'answers.private.jsonl').read_text())
    assert row['answer'] == 'fixture' and row['judge_attempts'][0]['request_id'] == 'offline-attempt-1'
    assert row['conversation_times'] == [turns[0].at]
    assert row['criteria'] and all(type(v) is int for v in summary.values())
    assert summary == json.loads((out/'summary.public.json').read_text())
    assert out.stat().st_mode & 0o077 == 0
    assert (out/'answers.private.jsonl').stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        export(run,out)
    (run/'judgments'/turns[0].id/'judgment.json').unlink()
    with pytest.raises(ValueError, match='checkpoint grade differs'):
        export(run,tmp_path/'corrupt')
    assert not (tmp_path/'corrupt').exists()


def test_blind_packet_and_saved_verdict_comparison():
    from pathlib import Path
    from pacbench.judge_blind_controls import evaluate
    from pacbench.semantic_grade import CONTEXT_JUDGE_PROTOCOL, DATE_SCOPED_JUDGE_PROTOCOL
    root = Path(__file__).parent/'fixtures/grader-review'
    cases = json.loads((root/'cases.json').read_text())
    key = json.loads((root/'proposed-key.json').read_text())['verdicts']
    packets = [evaluate(cases, p) for p in (CONTEXT_JUDGE_PROTOCOL, DATE_SCOPED_JUDGE_PROTOCOL)]
    assert packets[0]['A'] != packets[1]['A']
    assert all(c['answer'] in packet[c['id']] for c in cases for packet in packets)
    answers = {c['id']:json.dumps({'verdicts':[{'id':c['claims'][0][0],
               'verdict':'missed','reason':'offline replay fixture'}]}) for c in cases}
    compared = evaluate(cases, CONTEXT_JUDGE_PROTOCOL, answers, key)
    assert [k for k,v in compared.items() if v['agrees']] == ['B','F']


def test_legacy_checkpoint_reads_wal_and_rejects_changed_imessage(tmp_path):
    import sqlite3
    import uuid
    from pacbench.imessage_fixture import identity
    from pacbench.native_checkpoint_sources import GatewayLifecycleError, memcal_archive_readback
    source = tmp_path/'sources/night'
    source.mkdir(parents=True)
    guid = str(uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-message:one')).upper()
    message = dict(channel='imessage',source_id='one',sender='Sam',thread='Direct',text='At seven',
                   from_owner=False,written_at='2026-10-05T12:00:00-04:00')
    (source/'messages.json').write_text(json.dumps([message]))
    (source/'chat.fixture.json').write_text(json.dumps({'messages':{'one':{'guid':guid}},
            'threads':{'Direct':{'participants':['Me','Sam']}}}))
    handle = identity('Sam')['handle']
    db = sqlite3.connect(tmp_path/'memcal.db')
    try:
        db.execute('pragma journal_mode=wal')
        db.execute('create table archive(channel,external_id,text,ts,from_me,handle,thread)')
        db.execute('insert into archive values (?,?,?,?,?,?,?)',('imessage',guid,'At seven',message['written_at'],0,handle,'id:'+handle))
        db.commit()
        assert memcal_archive_readback(tmp_path,['night'],tmp_path)['native_text_unchanged']
        for field in ('text','handle','thread','from_me'):
            db.execute('savepoint change')
            db.execute(f'update archive set {field}=?',('different',))
            db.commit()
            with pytest.raises(GatewayLifecycleError,match='attribution'):
                memcal_archive_readback(tmp_path,['night'],tmp_path)
            db.execute(f'update archive set {field}=?',({'text':'At seven','handle':handle,'thread':'id:'+handle,'from_me':0}[field],))
            db.commit()
    finally:
        db.close()


def test_shutdown_observation_preserves_primary_failure_and_both_cleanup_errors(monkeypatch, tmp_path):
    from pacbench import native_pilot, process_cleanup
    def failed():
        raise TimeoutError('secret detail')
    def cleanup(root):
        raise OSError()
    monkeypatch.setitem(native_pilot._ARM_SHUTDOWNS, tmp_path.resolve(), failed)
    monkeypatch.setattr(process_cleanup, 'cleanup_run_processes', cleanup)
    with pytest.raises(ValueError, match='original'):
        try:
            raise ValueError('original')
        finally:
            native_pilot.stop_arm_model_work(tmp_path)
    assert json.loads((tmp_path/'shutdown-observation.json').read_text()) == {
        'original_error_type':'ValueError','native_hook':'failed','native_error_type':'TimeoutError',
        'fallback_cleanup':{'state':'failed','error_type':'OSError'}}
