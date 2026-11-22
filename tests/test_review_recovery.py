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
    assert summarize(dest/'workflow.jsonl')['coverage']['verified_turns'] == 2
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
    answers = {c['id']:json.dumps({'verdicts':[{'id':claim[0],
               'verdict':'missed','reason':'offline replay fixture'} for claim in c['claims']]}) for c in cases}
    compared = evaluate(cases, CONTEXT_JUDGE_PROTOCOL, answers, key)
    assert [k for k,v in compared.items() if v['agrees']] == ['B','F','H','I','J']


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


def test_document_return_and_trace_survive_failed_native_write_barrier(tmp_path):
    from pacbench.workflow_native import NativeAccount
    from pacbench.workflow_conformance import RecordingAccount
    from pacbench.workflow_runner import run_current_week
    from pacbench.workflow_progress import WorkflowProgress
    from pacbench.workflow_spec import compile_week
    from tests.test_workflow_realism import condition
    route = condition(p='reme')
    _, docs, turns, _ = compile_week(route.state)
    calls = []
    class Fixture(RecordingAccount, NativeAccount):
        ask = NativeAccount.ask
        def capture_answer(self, prompt, *, at, session_id, on_return):
            calls.append(prompt)
            on_return({'answer':'returned CLI answer'})
            return {'session_id':'fixture', 'answer':'returned CLI answer'}
        def observe_answer(self, trace):
            raise RuntimeError('unwitnessed write')
    dest = tmp_path/'week'
    with pytest.raises(RuntimeError, match='unwitnessed'):
        run_current_week(route, Fixture(route, docs), dest)
    key = json.loads((dest/'run.json').read_text())['run_key']
    with WorkflowProgress(dest, key) as p:
        assert p.observation('answer',turns[0].id,'cli-return')['answer'] == 'returned CLI answer'
        assert p.observation('answer',turns[0].id,'trace')['session_id'] == 'fixture'
        assert list(p.iter_verified()) == [] and p.claim_grade() is None
    assert calls == [turns[0].prompt]

def test_benchmark_rerun_keeps_published_judge_and_denominator():
    from pacbench.workflow_runner import judge_options
    from pacbench.workflow_spec import compile_week
    spec, _, turns, _ = compile_week('continuing-account')
    options = judge_options(turns[0], {'history':[{'role':'user','content':turns[0].prompt}],
                                     'conversation_times':[turns[0].at]})
    assert spec['judge'] == options['judge_protocol'] == 'semantic-judge-context-v4'
    assert len(turns) == 49 and sum(len(t.claims) for t in turns) == 192

def test_full_document_schedule_captures_all_turns_and_only_planned_grades(tmp_path):
    from tests.test_workflow_realism import condition
    from pacbench.workflow_conformance import RecordingAccount
    from pacbench.workflow_runner import run_current_week
    from pacbench.workflow_progress import WorkflowProgress
    from pacbench.workflow_spec import compile_week
    route = condition(p='reme')
    _, docs, turns, _ = compile_week(route.state)
    dest = tmp_path/'full'
    account = RecordingAccount(route,docs)
    summary = run_current_week(route, account, dest)
    account.ask = lambda *a,**kw: pytest.fail('sealed document resume dispatched a user turn')
    assert run_current_week(route, account, dest, resume=True) == summary
    key = json.loads((dest/'run.json').read_text())['run_key']
    with WorkflowProgress(dest,key) as progress:
        assert progress.execution_sealed and len(list(progress.iter_verified())) == 49
        ids = []
        while (item := progress.claim_grade()) is not None:
            turn, _ = item
            ids.append(turn.id)
            progress.finish_grade(turn.id, [dict(id=c.id,verdict='missed',reason='offline fixture') for c in turn.claims])
        assert ids == [turn.id for turn in turns if turn.claims]
        assert len(ids) == 47 and progress.claim_grade() is None
    report = summarize(dest/'workflow.jsonl')
    assert report['coverage']['graded_criteria'] == 192 and report['coverage']['full_accuracy'] is None

def test_document_judge_can_consume_verified_answer_with_original_controller_alive(tmp_path):
    import subprocess
    import sys
    from pacbench.canary_capability import binding
    from pacbench.live_dispatch import process_identity
    from pacbench.workflow_document_progress import PROTOCOL
    from pacbench.workflow_grading import judge_only_owner, claimed_request, grade_verified
    from tests.test_workflow_grading import checkpoints, Recorder, local_judge
    root = tmp_path/'reme'
    directory = root/'workflow'
    child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
    try:
        with checkpoints(directory,verified=1) as (p, turns):
            scope = dict(root=str(root), provider='reme', controller=process_identity(child.pid),
                         spec_sha256='offline', projects={'reme':{'judge':'test-judge'}})
            bound = root/'workflow-scope.json'
            bound.write_text(json.dumps(scope))
            p.bind('workflow_scope',binding(bound))
            p.bind('identity',dict(protocol=PROTOCOL,condition={'provider':'reme'},account=str(root),spec_sha256='offline'))
            calls = []
            def judge(prompt):
                claim = claimed_request(scope,'test-judge',{'messages':[{'role':'user','content':prompt}]})
                assert claim['question_id'] == turns[0].id
                return local_judge(directory,calls)(prompt)
            with judge_only_owner(p,scope):
                assert grade_verified(p,judge,directory,Recorder(),limit=1) == 1
            assert grade_verified(p,judge,directory,Recorder(),limit=1) == 0 and len(calls) == 1
    finally:
        child.terminate()
        child.wait()


def test_legacy_partial_exports_verified_prefix_without_inventing_grade(tmp_path):
    from pacbench.workflow_answer_export import export
    from pacbench.workflow_conformance import RecordingAccount
    from pacbench.workflow_runner import run_current_week
    from pacbench.workflow_spec import compile_week
    from tests.test_workflow_realism import condition
    route = condition(p='reme')
    docs = compile_week(route.state)[1]
    run = tmp_path/'week'
    run_current_week(route,RecordingAccount(route,docs),run)
    archived = tmp_path/'original'
    archived.mkdir()
    journal = (run/'workflow.jsonl').read_bytes().splitlines(keepends=True)
    idx = next(i for i,line in enumerate(journal) if json.loads(line)['phase']=='verified-turn')
    (archived/'workflow.jsonl').write_bytes(b''.join(journal[:idx+1])+b'{"phase":')
    summary = export(archived,tmp_path/'private')
    assert summary == dict(verified_answers=1,audited_judgments=0,graded_criteria=0,correct=0)
    row = json.loads((tmp_path/'private/answers.private.jsonl').read_bytes())
    assert row['checkpoint_grade_state'] == 'ungraded' and row['criteria'] is None
