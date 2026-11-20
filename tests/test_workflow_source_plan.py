from dataclasses import replace
import json

import pytest

from pacbench.semantic_grade import Claim
from pacbench.workflow_runner import inspect_source_cutoff
from pacbench.workflow_source_plan import SourcePlan
from pacbench.workflow_spec import Document, Turn


T1 = '2026-11-01T21:00:00-05:00'
T2 = '2026-11-02T21:00:00-05:00'
T3 = '2026-11-03T21:00:00-05:00'
T4 = '2026-11-04T21:00:00-05:00'


def doc(name, at, body, channel='imessage'):
    return Document(name, at, channel, body, (name,), (body,))


def turn(name, at, prompt, session='chat'):
    return Turn(name, at, prompt, session, 'initial', (Claim('slot', 'claim', ('one',)),))


def test_source_plan_binds_order_and_contents(tmp_path):
    docs = [doc('one',T1,'first'), doc('two',T1,'second','email'), doc('three',T3,'third')]
    turns = [turn('a', T1, 'first'), turn('b', T2, 'second', 'other'), turn('c', T3, 'third')]
    with SourcePlan(tmp_path, iter(docs), iter(turns)) as p:
        digest = p.digest
        assert p.turn_count == p.criteria_count == 3
        assert p.channels == ('email','imessage')
        assert list(p.iter_turns(1)) == [(1,turns[1],True),(2,turns[2],True)]
        assert not next(p.iter_turns())[2]
        assert p.get_turn('b') == turns[1]
        assert p.last_turn_id('chat') == 'c'
        assert p.last_turn_id('missing') is None
        assert list(p.iter_nights(through=T2)) == [(T1,docs[:2])]
        assert list(p.iter_nights(after_cutoff=T1)) == [(T3,docs[2:])]
        p.check_inventory([], None)
        p.check_inventory(iter(['one','two']), '2026-11-02T02:00:00+00:00')
        for wrong in ([], ['two','one'], ['one','two','three'], ['one','one']):
            with pytest.raises(ValueError, match='inventory'):
                p.check_inventory(wrong, T1)
    with SourcePlan(tmp_path, iter(docs), iter(turns)) as reopened:
        assert reopened.digest == digest
    for changed_docs, changed_turns in [
        (docs[1::-1]+docs[2:], turns),
        (docs, [replace(turns[0], claims=(Claim('slot','claim',('two',)),)), *turns[1:]]),
        ([replace(docs[0], bodies=('changed',)), *docs[1:]], turns)]:
        with pytest.raises(ValueError, match='schedule changed'):
            SourcePlan(tmp_path, changed_docs, changed_turns)


def test_missing_full_source_and_interrupted_build_are_atomic(tmp_path):
    source = doc('one', T1, 'first')
    with pytest.raises(ValueError, match='absent'):
        SourcePlan(tmp_path, [source], [], all_source_documents=[])
    def broken():
        yield source
        raise RuntimeError('interrupted')
    with pytest.raises(RuntimeError, match='interrupted'):
        SourcePlan(tmp_path, broken(), [])
    with SourcePlan(tmp_path, [source], []) as plan:
        assert list(plan.iter_nights()) == [(T1, [source])]


def test_future_source_matches_reference_even_across_boundaries(tmp_path):
    first = 'the original source contains an already public quotation which is long enough'
    second = 'a second document marks the tail of the source collection'
    a = 'question one asks about a particular striking constellation'
    b = 'question two asks about an entirely different installment'
    hidden = 'this long unavailable string contains a quote " and a newline\ninside 🔑'
    bodies = [first[-45:], first[-20:]+'\n'+second[:24], second[-22:]+'\n'+a[:24],
              a[-24:]+'\n'+b[:24], a[:42], b[:42], hidden, 'short',
              second[-22:]+'\n'+b[:24], 'a' * 560]
    docs = [doc('one', T1, first), doc('two', T1, second,'email')]
    all_docs = docs + [Document('never-released', T4, 'email', '\n'.join(bodies), ('three',), tuple(bodies))]
    turns = [turn('a', T2, a), turn('b', T3, b)]
    with SourcePlan(tmp_path, docs, turns, all_source_documents=all_docs) as p:
        for index, t in enumerate(turns):
            for isolated in (False, True):
                public = [t.prompt] if isolated else [x.prompt for x in turns[:index+1]]
                for body in bodies:
                    for observed in (body, json.dumps(body, ensure_ascii=False)[1:-1]):
                        # Exercise matches across SQLite probe pages and in role-filtered wire and prefetch.
                        for role in ('system','user','tool','assistant','prefetch'):
                            value = 'x'*254 + observed + '--'
                            surface = {'model_requests':[{'messages':[{'role':role,'content':value}]}],
                                       'prefetch':value if role == 'prefetch' else ''}
                            try:
                                inspect_source_cutoff(surface, all_docs, [d.filename for d in docs], public)
                            except ValueError:
                                with pytest.raises(ValueError, match='unavailable source body'):
                                    p.verify_no_leak(surface, t.at, t.prompt, isolated=isolated)
                            else:
                                p.verify_no_leak(surface, t.at, t.prompt, isolated=isolated)
        with pytest.raises(ValueError, match='frozen prompt'):
            p.verify_no_leak({}, T2, 'invented quotation')
    with pytest.raises(ValueError, match='schedule changed'):
        SourcePlan(tmp_path, docs, turns, all_source_documents=all_docs[:-1])


def test_wal_checkpoint_while_consumer_paused_between_turns(tmp_path):
    import sqlite3
    docs = [doc('one', T1, 'first'), doc('two',T1,'second')]
    t = turn('a', T1, 'first')
    with pytest.raises(ValueError, match='order or content'):
        SourcePlan(tmp_path, reversed(docs), [t], all_source_documents=docs)
    with SourcePlan(tmp_path, docs, [t]) as plan:
        turns = plan.iter_turns()
        nights = plan.iter_nights()
        assert next(turns)[1] == t
        assert next(nights)[1] == docs
        db = sqlite3.connect(tmp_path/'workflow.sqlite3', timeout=0.1)
        try:
            db.execute('CREATE TABLE test_progress(state)')
            db.execute("INSERT INTO test_progress VALUES ('captured')")
            db.commit()
            assert db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
        finally:
            db.close()
