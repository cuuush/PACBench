"""Offline interruption/deduplication controls. Fixture verdicts are never a benchmark result."""
from contextlib import contextmanager
import json
import subprocess
import sys

import pytest

from pacbench import workflow_grading as grading
from pacbench.canary_capability import binding, digest
from pacbench.live_dispatch import process_identity
from pacbench.model_calls import ModelOutput
from pacbench.workflow_metrics import coverage, progress_metrics, summarize
from pacbench.workflow_progress import WorkflowProgress
from pacbench.workflow_spec import compile_week


class Recorder:
    def __init__(self):
        self.rows = []

    def write(self, phase, **fields):
        self.rows.append({'phase':phase, **fields})


def local_judge(directory, calls):
    def respond(prompt):
        claims = json.loads(prompt.split('Claims (hidden from the assistant): ')[1])
        verdict = json.dumps({'verdicts':[dict(id=c['id'], verdict='missed', reason='offline control') for c in claims]})
        model, endpoint = 'openai/gpt-6-luna', 'amazon-bedrock/global'
        request = {'model':model,'messages':[{'role':'user','content':prompt}]}
        sent = {**request, 'provider':{'allow_fallbacks':False,'require_parameters':True,
                                       'order':[endpoint], 'only':[endpoint]}}
        calls.append(prompt)
        record = {'status':'ok','url':'https://openrouter.ai/api/v1/chat/completions',
                  'model':model,'request':request,'text':verdict,'judge_transport':{
                      'id':f'offline-attempt-{len(calls)}','request':sent,'endpoint':endpoint,
                      'request_sha256':digest(sent)}}
        filename = directory/'model-calls'/(digest(prompt)+'.json')
        filename.parent.mkdir(exist_ok=True)
        filename.write_text(json.dumps(record))
        return ModelOutput(verdict, model, {}, str(filename))
    return respond


@contextmanager
def checkpoints(directory, *, verified=2):
    _, _, turns, _ = compile_week('continuing-account')
    with WorkflowProgress(directory, 'offline-fixture', create=True) as progress:
        progress.bind('evidence_kind','native')  # Simulate only this unit's upstream verification boundary.
        for i, turn in enumerate(turns):
            progress.plan_turn(i, turn)
            if i < verified:
                surface = {'session_id':turn.session, 'gateway_identity':'unit fixture','model_requests':[],
                           'question_at':turn.at, 'conversation_times':[turn.at], 'tool_calls':[], 'tool_results':[],
                           'history':[{'role':'user','content':turn.prompt},{'role':'assistant','content':'fixture'}],
                           'answer':'fixture'}
                activity = {'explicit_attempts':0, 'repeat_requests':0, 'driver_receipts':{'coverage':'incomplete'}}
                progress.verified_turn(turn, surface, activity)
                progress.mark_turn_done(turn.id)
        yield progress, turns


def test_bounded_continuation_preserves_partial_scores(tmp_path):
    calls, journal = [], Recorder()
    with checkpoints(tmp_path) as (p, turns):
        judge = local_judge(tmp_path, calls)
        assert grading.grade_verified(p, judge, tmp_path, journal, limit=1)==1
        result = progress_metrics(p, limit=1)
        assert len(result['turns'])==1 and result['coverage']=={
            'planned_turns':49,'verified_turns':2,'planned_criteria':192,
            'graded_criteria':len(turns[0].claims),'ungraded_criteria':192-len(turns[0].claims),
            'correct':0,'accuracy_on_graded':0.0,'full_accuracy':None}
        assert grading.grade_verified(p, judge, tmp_path, journal, limit=4)==1
        assert grading.grade_verified(p, judge, tmp_path, journal, limit=4)==0
        assert len(calls)==2 and p.grade_states()['waiting']==47


@pytest.mark.parametrize('artifact', ['complete','only-response','tampered','missing'])
def test_reconcile_claim_does_not_request_a_second_judgment(tmp_path, monkeypatch, artifact):
    calls, journal = [], Recorder()
    with checkpoints(tmp_path, verified=1) as (p, turns):
        finish = p.finish_grade
        def interrupted(*args, **kwargs):
            raise InterruptedError('fixture interruption before SQLite settlement')
        monkeypatch.setattr(p, 'finish_grade', interrupted)
        with pytest.raises(InterruptedError):
            grading.grade_verified(p, local_judge(tmp_path,calls), tmp_path, journal, limit=1)
        monkeypatch.setattr(p, 'finish_grade', finish)
        output = tmp_path/'judgments'/turns[0].id/'judgment.json'
        if artifact in ('only-response','missing'):
            output.unlink()
        if artifact in ('tampered','missing'):
            path, = (tmp_path/'model-calls').glob('*.json')
            path.write_text('{}')
        report = grading.reconcile_claimed(p, tmp_path, limit=1)
        recovered = artifact in ('complete','only-response')
        assert report['recovered']==int(recovered)
        assert report['uncertain']==([] if recovered else [turns[0].id])
        assert grading.grade_verified(p, local_judge(tmp_path,calls), tmp_path, journal, limit=1)==0
        assert len(calls)==1
        if artifact == 'only-response':
            from pacbench.workflow_answer_export import checkpoint_rows
            row, = checkpoint_rows(p, tmp_path, tmp_path/'offline-audit')
            assert row['answer'] == 'fixture' and row['criteria'] == p.grade_record(turns[0].id)['criteria']
            assert row['judge_attempts'][0]['request_id'] == 'offline-attempt-1'


def test_unknown_dispatch_remains_claimed_and_missing_receipt_stays_ungraded(tmp_path):
    calls, journal = [], Recorder()
    with checkpoints(tmp_path) as (p, turns):
        def disconnect(prompt):
            calls.append(prompt)
            raise ConnectionError('simulated uncertain send')
        with pytest.raises(ConnectionError):
            grading.grade_verified(p, disconnect, tmp_path, journal, limit=1)
        assert grading.reconcile_claimed(p, tmp_path, limit=2)['uncertain']==[turns[0].id]
        def no_receipt(prompt):
            calls.append(prompt)
            return json.dumps({'verdicts':[dict(id=c.id,verdict='correct',reason='fixture') for c in turns[1].claims]})
        assert grading.grade_verified(p, no_receipt, tmp_path, journal, limit=2)==1
        assert len(calls)==2 and coverage(p.summary())['graded_criteria']==0
        assert p.grade_states()=={'claimed':1,'failed':1,'waiting':47}


def test_judge_recovery_owner_never_can_send_for_driver(tmp_path):
    root = tmp_path/'memcal'
    directory = root/'workflow'
    with checkpoints(directory,verified=1) as (p, turns):
        # A short-lived child supplies a real, now exited process identity.
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            original = process_identity(child.pid)
            scope = {'root':str(root),'provider':'memcal','controller':original,
                     'projects':{'memcal':{'judge':'test-judge','driver':'test-driver'}}}
            path = root/'workflow-scope.json'
            path.write_text(json.dumps(scope))
            p.bind('workflow_scope',binding(path))
            with pytest.raises(ValueError,match='still running'), grading.judge_only_owner(p,scope):
                pass
        finally:
            child.terminate()
            child.wait()
        calls = []
        def checked(prompt):
            body = {'messages':[{'role':'user','content':prompt}]}
            slot = grading.claimed_request(scope,'test-judge',body)
            assert slot['question_id']==turns[0].id
            assert grading.active_claim(scope,'test-judge',slot['checkpoint_effect'])
            assert not grading.active_claim(scope,'test-driver',slot['checkpoint_effect'])
            assert not grading.active_claim(scope,'test-judge',{**slot['checkpoint_effect'],'attempt_id':'stale'})
            with pytest.raises(ValueError,match='own'):
                grading.claimed_request(scope,'test-driver',body)
            with pytest.raises(ValueError,match='wire'):
                grading.claimed_request(scope,'test-judge',{'messages':[{'role':'user','content':'wrong'}]})
            return local_judge(directory,calls)(prompt)
        with grading.judge_only_owner(p, scope):
            with pytest.raises(BlockingIOError), grading.judge_only_owner(p, scope):
                pass
            assert grading.grade_verified(p, checked, directory, Recorder(), limit=1)==1
        assert len(calls)==1


def test_metrics_excludes_answer_visible_without_verification(tmp_path):
    from pacbench.canary_capability import sha
    from pacbench.workflow_spec import SPEC
    _, _, turns, basis = compile_week('continuing-account')
    rows = [
        {'phase':'spec','sha256':sha(SPEC),'source_basis':basis,
         'schedule':[dict(id=t.id,at=t.at,chat=t.session,measure=t.measure) for t in turns]},
        {'phase':'condition','condition':{'state':'continuing-account','channels':['email','imessage']}},
        {'phase':'dispatch','question':turns[0].id},
        {'phase':'answer-visible','question':turns[0].id,'surface':{}},
        {'phase':'stopped'}]
    path = tmp_path/'workflow.jsonl'
    path.write_text(''.join(json.dumps({**r, 'elapsed_s':i})+'\n' for i,r in enumerate(rows)))
    result = summarize(path)
    assert result['verified_turns']==0 and result['unverified_answers']==1
    assert result['coverage']['planned_criteria']==192 and result['coverage']['full_accuracy'] is None


@pytest.mark.parametrize('foreign', [False, True])
def test_saved_direct_response_before_consumer_receives_it(tmp_path, foreign):
    from pacbench.openrouter_route import CHAT_COMPLETIONS_URL
    with checkpoints(tmp_path, verified=1) as (p, turns):
        calls = []
        def interrupted(prompt):
            output = local_judge(tmp_path, calls)(prompt)
            request = {'model':'openai/gpt-6-luna','temperature':0.0,'messages':[{'role':'user','content':prompt}],
                       'response_format':{'type':'json_object'}, 'stream':True, 'stream_options':{'include_usage':True}}
            record = json.loads(open(output.record_path).read())
            claim = p.active_grade(turns[0].id)
            record['judge_transport']['checkpoint_effect'] = {**claim, 'attempt_id':'foreign'} if foreign else claim
            record.update(request=request, accounting_receipt={'id':record['judge_transport']['id']})
            filename = tmp_path/'model-calls'/(digest({'url':CHAT_COMPLETIONS_URL,'body':request})+'.json')
            filename.write_text(json.dumps(record))
            raise ConnectionError('model recorder persisted response before consumer returned')
        with pytest.raises(ConnectionError):
            grading.grade_verified(p, interrupted, tmp_path, Recorder(), limit=1)
        assert p.binding('judge-response/'+turns[0].id+'/'+digest(calls[0])) is None
        result = grading.reconcile_claimed(p,tmp_path,limit=1)
        assert result['recovered'] == int(not foreign)
        assert grading.grade_verified(p,interrupted,tmp_path,Recorder(),limit=1)==0
        assert len(calls)==1


def test_complete_criteria_do_not_turn_an_unsealed_arm_into_a_full_score():
    row = {'planned_turns':4, 'answered':4, 'planned_criteria':7, 'graded_criteria':7, 'correct':6}
    assert coverage({'smoke':row}, complete=False)['full_accuracy'] is None
    assert coverage({'all':row}, complete=True)['full_accuracy'] == 6/7


@pytest.mark.parametrize('judge_during_run',[False, True])
def test_native_stream_first_answer_can_be_graded_after_second_dispatch_fails(tmp_path, monkeypatch, capsys,
                                                                           judge_during_run):
    """Exercise real stream, progress, provenance checks, judge and CLI with in-memory site boundaries."""
    from types import SimpleNamespace
    from pacbench import workflow_stream as stream
    from pacbench.openrouter_route import with_route
    from pacbench.workflow_conformance import RecordingAccount
    from pacbench.workflow_runner import Condition

    _, docs, turns, basis = compile_week('continuing-account')
    condition = Condition('memcal','first-party-connector','continuing-account','unit fixture','openai/gpt-6-luna',
                          'amazon-bedrock/global','unit fixture','unit fixture','unit fixture','scenario',())
    directory = tmp_path/'workflow'
    public, judged = [], []
    class TestSite(RecordingAccount):
        evidence_kind = 'native'  # Unit-test boundary only. No deployed connector or real driver.
        def process_sync(self):
            return {'unit_test':True}
        def observe_sync(self, accepted, dream):
            return self.sync_readback(accepted)
        def capture_answer(self, prompt, *, at, session_id, on_return):
            public.append(prompt)
            if len(public)==2:
                raise ConnectionError('interrupted second native dispatch')
            result = self.ask(prompt, at=at, session_id=session_id)
            for request in result['model_requests']:
                request['request'] = with_route({'messages':request['messages']},profile='luna-bedrock-global')
            on_return({'fixture':True})
            return result
        def observe_answer(self, result):
            return result

    account = TestSite(condition, docs)
    account.frontend = SimpleNamespace(gateway_identity=lambda:account.gateway)
    monkeypatch.setattr(stream,'qualify_account',lambda *args:({'unit_test':True},account.identity))
    try:
        with pytest.raises(ConnectionError,match='second native'):
            stream.run_account_stream(condition, account, docs, turns, directory, spec_digest='unit fixture',
                                      source_basis=basis, judge=local_judge(directory,judged) if judge_during_run else None)
    finally:
        account.close()
    assert len(judged)==int(judge_during_run)
    meta = json.loads((directory/'run.json').read_text())
    with WorkflowProgress(directory, meta['run_key']) as progress:
        assert progress.summary()['initial']['answered']==1
        assert grading.grade_verified(progress, local_judge(directory,judged),directory,Recorder(),limit=2)==int(not judge_during_run)
        with pytest.raises(RuntimeError,match='uncertain'):
            progress.get_effect('answer',turns[1].id)
    grading.main([str(directory),'--reconcile','--limit','2'])
    result = json.loads(capsys.readouterr().out)
    assert result['coverage']['graded_criteria']==len(turns[0].claims)
    assert result['coverage']['planned_criteria']==192 and result['coverage']['full_accuracy'] is None
    assert len(public)==2 and len(judged)==1 and not result['complete']
    meta['identity']['store'] = 'another store'
    (directory/'run.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='original incremental'):
        grading.main([str(directory)])
