"""Offline durability and scale checks. All external effects here are test witnesses."""
from dataclasses import replace
from datetime import date, timedelta
from multiprocessing import get_context
import os
import sqlite3

import pytest

from pacbench.semantic_grade import Claim
from pacbench.workflow_spec import Turn
from pacbench.workflow_progress import UnknownEffect, WorkflowProgress


def plan(i, *, day='2026-01-01', session='chat'):
    return Turn(f'q/{i}', day + 'T17:00:00+00:00', f'Question {i}?', session, 'initial',
                (Claim('a', 'first criterion', ('source/1',)), Claim('b', 'second', ('source/2',))))


def surface(turn):
    return {'session_id': 'native/' + turn.session, 'gateway_identity': 'gateway',
            'model_requests': [{'request_id': 'request/' + turn.id}], 'answer': 'synthetic control'}


def verdicts():
    return [{'id': 'a', 'verdict': 'correct', 'reason': 'test control'},
            {'id': 'b', 'verdict': 'missed', 'reason': 'test control'}]


def abrupt_effect(path, phase, key):
    def perform():
        # The provider accepted a write, then this worker died before checkpointing it.
        with (path / 'write-witness').open('x') as witness:
            witness.write('one dispatch')
        os._exit(7)

    with WorkflowProgress(path, 'binding') as progress, progress.account_owner():
        progress.effect(phase, key, {'body': 'state change'}, perform)


def test_process_loss_during_effect_and_partial_grades(tmp_path):
    t0, t1 = plan(0), plan(1)
    with WorkflowProgress(tmp_path, 'binding', create=True) as progress:
        progress.plan_turn(0, t0)
        progress.plan_turn(1, t1)
        progress.effect('ingest', 'day-1', ['1'], lambda: {'native_id': 'first'})
        progress.verified_turn(t0, surface(t0), {'tools': []})
        assert not progress.complete

    child = get_context('spawn').Process(target=abrupt_effect, args=(tmp_path, 'close', t0.id))
    child.start()
    child.join(20)
    assert child.exitcode == 7
    assert (tmp_path / 'write-witness').read_text() == 'one dispatch'

    with WorkflowProgress(tmp_path, 'binding') as progress, progress.account_owner():
        assert progress.next_ordinal() == 0
        assert progress.effect('ingest', 'day-1', ['1'], lambda: pytest.fail('duplicate ingest')) == {'native_id': 'first'}
        with pytest.raises(ValueError, match='input changed'):
            progress.effect('ingest', 'day-1', ['changed'], lambda: pytest.fail('changed dispatch'))
        with pytest.raises(UnknownEffect, match='automatic replay disabled'):
            progress.effect('close', t0.id, {'body': 'state change'}, lambda: pytest.fail('duplicate close'))
        with pytest.raises(UnknownEffect):
            progress.get_effect('close', t0.id)
        assert progress.get_effect('other', 'x') is None
        # Native uncertainty cannot prevent independently settling a captured answer.
        assert progress.claim_grade() == (t0, surface(t0))
        progress.finish_grade(t0.id, verdicts())
        assert progress.summary() == {'initial': {'planned_turns': 2, 'planned_criteria': 4, 'answered': 1,
                                                'graded_criteria': 2, 'correct': 1, 'judge_failures': 0}}
        assert not progress.complete
        with pytest.raises(ValueError, match='incomplete'):
            progress.seal_execution(2, 4)


def test_binding_write_order_and_global_gate(tmp_path):
    t0, t1 = plan(0), replace(plan(1), claims=())
    with WorkflowProgress(tmp_path, 'binding', create=True) as p:
        p.bind('evidence_kind', 'recording-fake')
        p.bind('evidence_kind', 'recording-fake')
        with pytest.raises(ValueError, match='binding changed'):
            p.bind('evidence_kind', 'native')
        assert p.binding('evidence_kind') == 'recording-fake'
        for i, turn in enumerate((t0, t1)):
            p.plan_turn(i, turn)
            p.plan_turn(i, turn)
        with pytest.raises(ValueError, match='plan changed'):
            p.plan_turn(0, replace(t0, prompt='new question'))
        with pytest.raises(ValueError, match='out of account order'):
            p.verified_turn(t1, surface(t1), {})
        p.advance_cutoff('2026-01-01T16:00:00+00:00')
        p.advance_cutoff(p.last_cutoff())
        with pytest.raises(ValueError, match='forward'):
            p.advance_cutoff('2025-12-31T16:00:00+00:00')
        p.verified_turn(t0, surface(t0), {})
        p.verified_turn(t0, surface(t0), {})
        assert p.reused_request_ids(['request/q/0', 'other']) == ['request/q/0']
        assert p.session_owner('native/chat') == 'chat'
        assert list(p.iter_sessions(page_size=1)) == [('chat', 'native/chat')]
        assert p.binding('gateway') == 'gateway'
        assert p.latest_surface('chat', before=0) is None
        assert p.latest_surface('chat', before=1) == surface(t0)
        with pytest.raises(ValueError, match='evidence changed'):
            p.verified_turn(t0, surface(t0), {'tamper': 1})
        p.mark_turn_done(t0.id)
        with pytest.raises(ValueError, match='binding changed'):
            p.verified_turn(t1, dict(surface(t1), gateway_identity='other'), {})
        with pytest.raises(ValueError, match='session changed'):
            p.verified_turn(t1, dict(surface(t1), session_id='new-chat'), {})
        p.verified_turn(t1, surface(t1), {})
        p.mark_turn_done(t1.id)
        assert p.get_verified(t0.id) == (t0, surface(t0), {})
        assert [t.id for t, _, _ in p.iter_verified(page_size=1)] == [t0.id, t1.id]
        assert p.next_ordinal() is None
        assert p.claim_grade() == (t0, surface(t0))
        assert p.claim_grade() is None
        assert not p.complete
        p.seal_execution(2, 2)
        assert not p.complete

    with pytest.raises(ValueError, match='different run'):
        with WorkflowProgress(tmp_path, 'other binding', create=True):
            pytest.fail('opened a different run')
    with WorkflowProgress(tmp_path, 'binding') as p:
        assert p.claim_grade() is None
        assert p.get_claimed_grade(t0.id) == (t0, surface(t0))
        assert list(p.claimed_grades(page_size=1)) == [(t0, surface(t0))]
        assert list(p.claimed_grades(after=t0.id)) == []
        with pytest.raises(ValueError, match='planned criteria'):
            p.finish_grade(t0.id, verdicts()[::-1])
        p.finish_grade(t0.id, verdicts())
        p.finish_grade(t0.id, verdicts())
        assert p.execution_sealed and not p.complete
        assert p.grade_record(t0.id)['criteria'] == verdicts()
        assert p.grade_states() == {'graded': 1, 'unscored': 1}
        with pytest.raises(ValueError, match='already sealed'):
            p.plan_turn(2, plan(2))
        with pytest.raises(ValueError, match='already sealed'):
            p.effect('write', 'new', {}, lambda: pytest.fail('dispatch after seal'))
        with pytest.raises(ValueError, match='incomplete'):
            p.seal_execution(2, 20)


def test_independent_workers_never_duplicate_dispatch(tmp_path):
    t0, t1 = plan(0), plan(1)
    with WorkflowProgress(tmp_path, 'x', create=True) as a, WorkflowProgress(tmp_path, 'x') as b:
        with a.account_owner():
            with pytest.raises(RuntimeError, match='already has an owner'):
                with b.account_owner():
                    pytest.fail('two account owners')
            for i, turn in enumerate((t0, t1)):
                a.plan_turn(i, turn)
                a.verified_turn(turn, surface(turn), {})
                a.mark_turn_done(turn.id)
            assert a.claim_grade()[0] == t0
            assert b.claim_grade()[0] == t1
            assert b.claim_grade() is None
            assert [t.id for t, _ in b.claimed_grades(after=t0.id)] == [t1.id]
            b.finish_grade(t1.id, None, failure='UnusableJudgeResponse')
            a.finish_grade(t0.id, verdicts())
            with pytest.raises(ValueError, match='already settled'):
                b.finish_grade(t1.id, verdicts())

            def first_call():
                with pytest.raises(UnknownEffect):
                    b.effect('write', 'first', {}, lambda: pytest.fail('duplicate dispatch'))
                return {'done': True}

            assert a.effect('write', 'first', {}, first_call) == {'done': True}
        with b.account_owner():
            assert b.effect('write', 'first', {}, lambda: pytest.fail('duplicate dispatch')) == {'done': True}
        a.seal_execution(2, 4)
        assert not a.complete
        assert a.summary()['initial']['judge_failures'] == 1


def test_resume_requires_existing_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        with WorkflowProgress(tmp_path, 'missing'):
            pytest.fail('created replacement run')


def test_three_hundred_days_with_independent_paged_judgments(tmp_path):
    """Persist 3,000 synthetic source IDs, 3,000 questions and 6,000 grade slots."""
    def day(n):
        return (date(2026, 1, 1) + timedelta(days=n)).isoformat()

    def question(i):
        turn = plan(i, day=day(i // 10), session=f'chat-{i // 10}')
        return replace(turn, at=f'{day(i // 10)}T17:{i % 10:02}:00+00:00')

    dispatched, graded = 0, 0
    with WorkflowProgress(tmp_path, 'scale-fixture', create=True) as writer:
        for i in range(3000):
            writer.plan_turn(i, question(i))
        # Restart with a verified execution prefix and a partially consumed grade queue.
        for start, stop in [(0, 1217), (1217, 3000)]:
            with WorkflowProgress(tmp_path, 'scale-fixture') as native, native.account_owner():
                assert native.next_ordinal() == start
                for i in range(start, stop):
                    turn = question(i)
                    if i % 10 == 0:
                        cutoff = day(i // 10) + 'T16:00:00+00:00'
                        ids = [f'source-{n}' for n in range(i, i + 10)]
                        native.effect('ingest', cutoff, ids, lambda: {'readback': ids})
                        native.advance_cutoff(cutoff)
                    result = native.effect('answer', turn.id, [turn.prompt], lambda: surface(turn))
                    dispatched += 1
                    native.verified_turn(turn, result, {})
                    native.mark_turn_done(turn.id)
                    if i % 4 == 0:
                        saved, _ = writer.claim_grade()
                        writer.finish_grade(saved.id, verdicts())
                        graded += 1
        assert dispatched == 3000 and graded == 750
        assert not writer.complete
        assert writer.summary()['initial']['graded_criteria'] == 1500
        writer.seal_execution(3000, 6000)
    with WorkflowProgress(tmp_path, 'scale-fixture') as judge:
        while (work := judge.claim_grade()) is not None:
            judge.finish_grade(work[0].id, verdicts())
        assert sum(1 for _ in judge.iter_verified(page_size=7)) == 3000
        assert judge.execution_sealed and not judge.complete
        assert judge.summary() == {'initial': {'planned_turns': 3000, 'planned_criteria': 6000, 'answered': 3000,
                                              'graded_criteria': 6000, 'correct': 3000, 'judge_failures': 0}}


def test_attempt_bound_reconciliation_and_portable_blob_integrity(tmp_path):
    from shutil import copytree

    # Completed native data can be adopted only with verified evidence for the
    # interrupted operation. The test uses a local stand-in for that evidence.
    original = tmp_path / 'first'
    t = plan(0)
    with WorkflowProgress(original, 'binding', create=True) as p:
        p.plan_turn(0, t)
        args = {'prompt': t.prompt}
        with pytest.raises(ConnectionError):
            p.effect('ask', t.id, args, lambda: (_ for _ in ()).throw(ConnectionError()), role='hermes')
        p.record_observation('ask', t.id, 'cli-return', {'stdout': 'raw witness'})
        assert p.observation('ask', t.id, 'cli-return') == {'stdout': 'raw witness'}
        with pytest.raises(ValueError, match='observation changed'):
            p.record_observation('ask', t.id, 'cli-return', {'stdout': 'replacement'})
        claim = p.active_effect('ask', t.id, args)
        assert claim['role'] == 'hermes' and claim['run_key'] == 'binding' and len(claim['attempt_id']) == 32
        with pytest.raises(ValueError, match='input changed'):
            p.active_effect('ask', t.id, {'prompt': 'different'})
        with pytest.raises(ValueError, match='operation-bound'):
            p.reconcile_effect('ask', t.id, args, lambda c: (surface(t), {'native_evidence': {'status': 'ready'}}))
        with pytest.raises(ValueError, match='operation-bound'):
            p.reconcile_effect('ask', t.id, args, lambda c: (surface(t), {'operation': {**c, 'key': 'other'},
                                                                        'native_evidence': {'readback': 'wrong turn'}}))
        assert p.reconcile_effect('ask', t.id, args, lambda c: (
            surface(t), {'operation': c, 'native_evidence': {'observed_fixture': t.id}})) == surface(t)
        assert p.active_effect('ask', t.id) is None
        assert p.effect('ask', t.id, args, lambda: pytest.fail('duplicate paid ask'), role='hermes') == surface(t)
        p.verified_turn(t, surface(t), {})
        p.mark_turn_done(t.id)
        assert p.claim_grade() == (t, surface(t))
        assert p.active_grade(t.id)['role'] == 'judge'
        assert p.active_grade(t.id)['attempt_id'] != claim['attempt_id']
        assert p.audit_artifacts() == 4  # surface, reconciliation, raw CLI witness, activity
        # A rejected replacement may leave an unreferenced blob. Its bytes are never evidence.

    restored = tmp_path / 'restored'
    copytree(original, restored)
    with WorkflowProgress(restored, 'binding') as p:
        assert p.get_effect('ask', t.id) == surface(t)
        assert p.get_claimed_grade(t.id) == (t, surface(t))
        path = next(f for f in (restored / 'workflow-artifacts').rglob('*.json') if b'model_requests' in f.read_bytes())
        path.chmod(0o600)
        path.write_text('{}')
        with pytest.raises(ValueError, match='hash mismatch'):
            p.get_verified(t.id)
        with pytest.raises(ValueError, match='hash mismatch'):
            p.seal_execution(1, 2)
        assert not p.execution_sealed
