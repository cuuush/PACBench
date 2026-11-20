"""Synthetic local receipts only. The original account and network are unused."""
import json
import os
import multiprocessing
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from types import SimpleNamespace

import pytest

from pacbench import receipt_projection as projection
from pacbench.model_calls import captured_attempt
from pacbench.observational_receipts import IndexedRunReceiptJournal, RunReceiptJournal
from pacbench.live_dispatch import OwnershipConflict


def row(i):
    return {'id':str(i),'project':'bench-memory','state':'receipt_pending','finished':None,
            'dispatch_context':{'operation_id':f'op-{i}'}}


def add(budget, ident):
    with budget.journal() as (_, append):
        append('prepared', str(ident), row(ident))


def patch(budget, ident, **values):
    with budget.journal() as (_, append):
        append('outcome', str(ident), values)


def test_explicit_initial_replay_and_keyed_dispatch_never_scan_history(tmp_path,monkeypatch):
    path=tmp_path/'receipts.jsonl'
    old=RunReceiptJournal(path)
    for i in range(10):
        add(old,i)
        patch(old,i,finished='done',state='settled',settled_charge_usd='0.001')
    before=path.read_bytes()
    new=IndexedRunReceiptJournal(path)
    with pytest.raises(ValueError,match='Initialize'):
        new.get_attempt('9')
    new.initialize_from_journal()
    assert new.snapshot()==old.snapshot()
    assert path.read_bytes()==before
    reads=[]
    load=json.loads
    monkeypatch.setattr(projection.json,'loads',lambda payload,**kw:(reads.append(1),load(payload,**kw))[1])
    monkeypatch.setattr(projection.IndexedAttempts,'values',lambda self:pytest.fail('unexpected historical scan'))
    assert captured_attempt(SimpleNamespace(receipt={'budget':new,'id':'9'}))['state']=='settled'
    with new.journal() as (rows,_):
        assert not rows.refused() and not rows.active()
        assert rows.previous('bench-memory','op-1')[0]['id']=='1'
    assert len(reads)==2


@pytest.mark.parametrize('bytes_written',['none','some','all'])
def test_interruption_completes_the_same_durable_event_once(tmp_path,monkeypatch,bytes_written):
    path=tmp_path/'receipts.jsonl'
    budget=IndexedRunReceiptJournal(path)
    add(budget,1)
    def interrupted(stream, db, rows, raw, written):
        amount={'none':0,'some':15,'all':len(raw)}[bytes_written]
        stream.write(raw[:amount])
        os.fsync(stream.fileno())
        raise RuntimeError('simulated killed writer')
    with monkeypatch.context() as m:
        m.setattr(projection.Projection,'_finish',staticmethod(interrupted))
        with pytest.raises(RuntimeError,match='killed'):
            patch(budget,1,finished='done',http_status=402,provider_rejection='insufficient_credits')
    assert budget.get_attempt('1')==RunReceiptJournal(path).snapshot()[0]
    with budget.journal() as (rows,_):
        assert rows.refused() and not rows.active()
    assert len(path.read_bytes().splitlines())==2


@pytest.mark.parametrize('mutation',['rewrite','truncate','replace','append'])
def test_modified_journal_cannot_authorize_new_calls(tmp_path,mutation):
    path=tmp_path/'receipts.jsonl';budget=IndexedRunReceiptJournal(path)
    for i in range(40):
        add(budget,i)
    original=path.read_bytes()
    if mutation=='rewrite':
        stamp=path.stat()
        with path.open('r+b') as f:
            f.seek(7); f.write(b'?')
        os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
    elif mutation=='truncate':
        path.write_bytes(original[:-30])
    elif mutation=='replace':
        foreign=path.with_name('other');foreign.write_bytes(original);foreign.replace(path)
    else:
        with path.open('ab') as stream:stream.write(b'{}\n')
    with pytest.raises(ValueError,match='journal'):
        budget.get_attempt('1')


def _process_writer(path_and_id):
    path, ident = path_and_id
    budget = IndexedRunReceiptJournal(path)
    add(budget,ident)
    patch(budget,ident,finished='done',http_status=200)


def test_independent_process_clients_serialize_appends(tmp_path):
    path = tmp_path/'receipts.jsonl'
    with ProcessPoolExecutor(4,mp_context=multiprocessing.get_context("spawn")) as executor:
        list(executor.map(_process_writer, [(path,i) for i in range(20)]))
    assert len(RunReceiptJournal(path).snapshot())==20
    assert IndexedRunReceiptJournal(path).snapshot()==RunReceiptJournal(path).snapshot()


def test_normal_admission_and_failed_retries_remain_closed(tmp_path,monkeypatch):
    from pacbench.canary_capability import CanaryBusy
    from tests.test_observational_receipts import response
    b=IndexedRunReceiptJournal(tmp_path/'receipts.jsonl')
    monkeypatch.setenv('PACBENCH_LUNA_COMPATIBILITY','temperature-omitted-default-v1')
    monkeypatch.setenv('PACBENCH_EFFECTIVE_OUTPUT_TOKENS','32768')
    monkeypatch.setenv('PACBENCH_DISPATCH_TRACKING','direct-process-evidence-v1')
    permit={'root':'offline','slot':{'kind':'ingest','provider':'memcal'},'capability':{}}
    monkeypatch.setattr('pacbench.execution_capability.admission',lambda *a:permit)
    monkeypatch.setattr('pacbench.execution_capability.fields',lambda p:{'workflow_slot':p['slot']})
    first={'model':b.model,'messages':[{'role':'user','content':'first'}]}
    other={'model':b.model,'messages':[{'role':'user','content':'second'}]}
    monkeypatch.setattr(projection.IndexedAttempts,'values',lambda self:pytest.fail('unexpected historical scan'))
    r=b.reserve('bench-memory',first); b.mark_dispatch_possible(r['id'])
    with pytest.raises(CanaryBusy):b.reserve('bench-memory',other,wait=False)
    b.settle(r['id'],402,b'{"error":{"code":"in_flight_budget_exhausted"}}',headers={'retry-after':'0'},body_complete=True)
    assert b.retry_delay(r['id'])==0
    again=b.reserve('bench-memory',first); b.mark_dispatch_possible(again['id'])
    b.settle(again['id'],200,response(b,'0.00123'),body_complete=True)
    assert b.get_attempt(again['id'])['settled_charge_usd']=='0.00123'
    with pytest.raises(OwnershipConflict):b.reserve('bench-memory',first)
    last=b.reserve('bench-memory',other); b.mark_dispatch_possible(last['id'])
    b.settle(last['id'],403,b'{}',body_complete=True)
    with pytest.raises(RuntimeError,match='refusal'):b.reserve('bench-memory',other)

def test_scoped_idle_and_request_lineage_need_only_selected_rows(tmp_path,monkeypatch):
    b = IndexedRunReceiptJournal(tmp_path/'receipts.jsonl')
    for i in range(4):
        add(b,i)
    patch(b,0,accounting_role='judge')
    patch(b,1,accounting_role='hermes',finished='done')
    patch(b,2,accounting_role='hermes',retry_of='1',finished='done')
    patch(b,3,accounting_role='hermes',retry_of='2',finished='done',upstream_request_id='offline-native-test')
    monkeypatch.setattr(projection.IndexedAttempts,'values',lambda self:pytest.fail('unexpected historical scan'))
    assert b.has_active_attempts() and b.has_active_attempts(('judge',))
    assert not b.has_active_attempts(('memory_provider','hermes'))
    assert b.has_offline_generations()
    assert [r['id'] for r in b.lineage_for_requests(['3','missing','2'])] == ['1','2','3']


def test_projection_only_sql_edit_cannot_hide_account_refusal(tmp_path):
    path=tmp_path/'receipts.jsonl';b=IndexedRunReceiptJournal(path)
    add(b,1);patch(b,1,finished='done',http_status=403)
    original=path.read_bytes()
    with sqlite3.connect(projection.Projection(path).index) as db:
        db.execute('DELETE FROM attempts')
    with pytest.raises(ValueError,match='projection changed'):
        b.has_active_attempts()
    assert path.read_bytes()==original
