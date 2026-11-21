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
