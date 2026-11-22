"""Readback contracts. No server, provider credentials, or inference."""
import json
from types import SimpleNamespace

import pytest

from pacbench.workflow_hindsight_documents import HindsightDocumentReadback
from pacbench.workflow_hindsight import HindsightAccount
from pacbench.workflow_spec import compile_week


def test_native_list_fast_path_checks_ordered_file_receipts_and_current_hashes():
    _, documents, _, _ = compile_week('continuing-account')
    first, second = documents[:2]
    bank = 'test-bank'
    rows = [SimpleNamespace(id=first.filename, bank_id=bank, content_hash='native-hash',
                            original_text=first.text, memory_unit_count=1)]
    calls = []

    def list_documents(account, *, limit, offset, _request_timeout):
        calls.append(('list', account, offset))
        return SimpleNamespace(total=len(rows), offset=offset, total_is_estimate=False, items=rows)

    def get_document(account, identifier, *, _request_timeout):
        calls.append(('get', account, identifier))
        return rows[0]

    operations = SimpleNamespace(
        get_operation_status=lambda account, operation, _request_timeout: SimpleNamespace(status='completed'),
        list_operations=lambda account, status, limit, _request_timeout: SimpleNamespace(total=0))
    client = SimpleNamespace(documents=SimpleNamespace(list_documents=list_documents, get_document=get_document),
                             operations=operations)
    witness = HindsightDocumentReadback(client, bank, documents)
    assert witness.document(first, ['one'])['content_sha256'] == first.sha256
    assert witness.inventory()[0] == [first.filename]
    assert calls == [('get', bank, first.filename), ('list', bank, 0)]
    rows.append(SimpleNamespace(id=second.filename,bank_id=bank,content_hash='future'))
    with pytest.raises(RuntimeError, match='verified source prefix'):
        witness.inventory()


def test_native_cli_return_survives_failed_hindsight_bank_barrier(tmp_path):
    account = object.__new__(HindsightAccount)
    account.witness = SimpleNamespace(root=tmp_path)
    raw = {'turn_nonce':'a'*32, 'stdout':'offline CLI fixture'}

    def capture(prompt, *, at, session_id, on_return):
        on_return(raw)
        return {'answer':'response fixture'}

    def bank_barrier(trace):
        path = tmp_path/'native-cli-returns'/('a'*32+'.json')
        assert json.loads(path.read_text())==raw
        assert path.stat().st_mode & 0o777 == 0o600
        raise TimeoutError('native test bank still processing')

    account.capture_answer = capture
    account.observe_answer = bank_barrier
    with pytest.raises(TimeoutError, match='still processing'):
        account.ask('offline query',at='2026-10-05T10:00:00-04:00',session_id=None)
    assert len(list((tmp_path/'native-cli-returns').glob('*.json')))==1


def test_readiness_timings_cover_native_operation_drain_and_exact_file(monkeypatch):
    from pacbench import workflow_hindsight_documents as module

    now = [0.0]
    def advance(seconds):
        now[0] += seconds

    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda:now[0], sleep=advance))
    _, documents, _, _ = compile_week('continuing-account')
    first = documents[0]
    states = iter(['pending', 'completed'])

    def status(bank, operation, **kw):
        assert (bank, operation) == ('bank', 'op')
        advance(.125)
        return SimpleNamespace(status=next(states))

    def list_operations(bank, **kw):
        advance(.25)
        return SimpleNamespace(total=0)

    def document(bank, identifier, **kw):
        advance(.375)
        return SimpleNamespace(bank_id=bank, id=identifier, original_text=first.text,
                               content_hash='hash', memory_unit_count=1)

    client = SimpleNamespace(operations=SimpleNamespace(get_operation_status=status, list_operations=list_operations),
                             documents=SimpleNamespace(get_document=document))
    reader = HindsightDocumentReadback(client, 'bank', documents)
    result = reader.document(first, ['op'])
    assert result['timing'] == {'file_operations_s':.75, 'bank_drain_s':1., 'document_readback_s':.375}
    assert result['bank_jobs'] == dict(pending=0, processing=0, failed=0, cancelled=0)


def test_observer_spans_include_each_drain_and_receipt_check(tmp_path, monkeypatch):
    from pacbench import workflow_hindsight as module
    from unittest.mock import Mock

    now = [1_000_000_000]
    def advance(seconds):
        now[0] += round(seconds * 1e9)

    monkeypatch.setattr(module, 'time', SimpleNamespace(
        monotonic_ns=lambda:now[0], monotonic=lambda:now[0]/1e9, sleep=advance))
    owner = SimpleNamespace(witness={'pid':1,'start_time':2}, home=tmp_path,
                            pins={'reuse_observer_sha256':'fixture'})
    sample = dict(**owner.witness, protocol=module.PROTOCOL, native_failures=[], native_release_threads=0,
                  observed_monotonic_ns=now[0], sessions=[], observer_sha256='fixture')
    (tmp_path/'reuse-observer.json').write_text(json.dumps(sample))
    active = iter([True, False])

    def receipts():
        advance(.25)
        return [{'accounting_role':'memory_provider', 'finished':not next(active)}]

    def drain(*, deadline):
        advance(.5)
        return dict(pending=0, processing=0, failed=0, cancelled=0)

    reader = SimpleNamespace(documents=[], drain=Mock(side_effect=drain))
    witness = module.HindsightWitness(tmp_path, owner, 'local', 'bank', None, reader,
                                      SimpleNamespace(snapshot=receipts))
    proof = witness.idle()['readback']
    assert proof['timing'] == {'gateway_observer_wait_s':.1, 'bank_drain_s':1., 'receipt_check_s':.5}
    assert reader.drain.call_count == 2
