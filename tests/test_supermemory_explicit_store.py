"""Check exact stock tool acknowledgments and separate native inventories."""
import fcntl
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pacbench.providers.supermemory import explicit_store as store
from pacbench.dispatch_ownership import OwnershipConflict
from pacbench.providers.supermemory.week import StockWeekWitness
from pacbench.providers.supermemory.observation import stock_temporal_metadata


def test_driver_receipts_can_be_read_during_locked_dispatch_confirmation(tmp_path):
    row = dict(id='parent', accounting_role='hermes', finished='now',
               workflow_slot=dict(supermemory_week_claim={'attempt_id': 'claim'}))
    event = dict(kind='prepared', id='parent', row=row)
    path = tmp_path / 'receipts.jsonl'
    path.write_text(json.dumps(event) + '\n{"kind":')
    with path.open() as journal:
        fcntl.flock(journal, fcntl.LOCK_EX)
        output = subprocess.run(
            [sys.executable, '-c', 'from pathlib import Path\n'
             'import sys\n'
             'from pacbench.providers.supermemory.explicit_store import driver_receipts\n'
             'assert driver_receipts(Path(sys.argv[1]), {"attempt_id":"claim"})[0]["id"] == "parent"',
             str(tmp_path)], timeout=10, capture_output=True, text=True)
    assert output.returncode == 0, output.stderr


@pytest.mark.parametrize('fault', [None, 'foreign-id', 'foreign-call', 'changed-request', 'unmatched-add', 'lost-ack'])
def test_native_store_ack_is_bound_to_the_exact_driver_round(tmp_path, monkeypatch, fault):
    for name in ('explicit-adds', 'explicit-tool-operations'):
        (tmp_path / name).mkdir()
    native_id = 'D' * 22
    claim = {'attempt_id': 'operation'}
    call = 'call_save'
    owner = {'pid': 42}
    payload = {'content': 'The porch list is Saturday only.'}
    info = dict(protocol=store.PROTOCOL, namespace='week', owner=owner, claim=claim, question_id='correction',
                session_id='session', nonce='nonce', submission='submission', parent_receipt='first',
                tool='supermemory_store', tool_call_id='different' if fault == 'foreign-call' else call)
    prepared = dict(**info, request_sha256='request', payload_sha256=store.digest(payload))
    path = store.operation_path(tmp_path, claim, info['tool_call_id'])
    store.write_once(path, prepared)
    with pytest.raises(FileExistsError):
        store.write_once(path, prepared)
    if fault == 'changed-request':
        prepared['request_sha256'] = 'changed'
    entry = dict(**prepared, native_id=native_id, payload=payload, acknowledgment={'id': native_id, 'status': 'queued'},
                 acknowledged_at_ms=1, response_sha256='response')
    if fault != 'lost-ack':
        store.write_once(tmp_path / 'explicit-adds' / f'{native_id}.json', entry)
    parent = dict(id='first', dispatch_owner=owner, http_status=200, dispatch_execution='response_ended',
                  response_validation=dict(envelope_errors=[], wire_parse_errors=0, finish_reasons=['tool_calls']))
    monkeypatch.setattr(store, 'driver_receipts', lambda root, claim: [parent])
    slot = dict(question_id='correction', supermemory_week_claim=claim, supermemory_first_question=dict(
        session_id='session', turn_nonce='nonce', native_request_id='submission', tool_rounds=[[call]]))
    msgs = [{'role': 'assistant', 'tool_calls': [{'id': call, 'function': {
                 'name': 'supermemory_store', 'arguments': json.dumps(payload)}}]},
            {'role': 'tool', 'tool_call_id': call, 'content': json.dumps({
                'saved': fault not in ('unmatched-add', 'lost-ack'),
                'id': 'F' * 22 if fault == 'foreign-id' else native_id})}]
    scope = dict(root=str(tmp_path), namespace='week')
    if fault is None:
        assert store.match_driver(scope, slot, {'messages': msgs}) == [native_id]
    else:
        with pytest.raises(OwnershipConflict):
            store.match_driver(scope, slot, {'messages': msgs})


@pytest.mark.parametrize('fault', [None, 'unowned', 'foreign-namespace', 'duplicate', 'retargeted'])
def test_catalog_keeps_explicit_native_ids_separate_and_preserves_rejections(tmp_path, fault):
    (tmp_path / 'inventory-rejections').mkdir()
    witness = StockWeekWitness.__new__(StockWeekWitness)
    witness.root, witness.namespace = tmp_path, 'week'
    tool_id = 'D' * 22
    witness.explicit_writes = {tool_id: {}}
    source = dict(id='S' * 22, customId='frozen.txt', containerTags=['week'])
    tool = dict(id=tool_id, customId=None, containerTags=['week'])
    rows = [source, tool]
    if fault == 'unowned':
        rows.append(dict(id='X' * 22, customId=None, containerTags=['week']))
    if fault == 'foreign-namespace':
        tool['containerTags'] = ['foreign']
    if fault == 'duplicate':
        rows.append(tool.copy())
    if fault == 'retargeted':
        tool['customId'] = 'frozen.txt'
    response = dict(memories=rows, pagination=dict(currentPage=1, totalPages=1))
    witness.read_native = lambda path, body: response
    if not fault:
        assert witness.catalog(['frozen.txt']) == {'frozen.txt': source}
        assert not list((tmp_path / 'inventory-rejections').iterdir())
        return
    with pytest.raises(ValueError, match='exact source, capture and tool'):
        witness.catalog(['frozen.txt'])
    artifact, = (tmp_path / 'inventory-rejections').glob('*.json')
    saved = json.loads(artifact.read_text())
    assert saved == dict(api='v3', namespace='week', expected=['frozen.txt'],
                         authorized_tool_ids=[tool_id], response=response)


@pytest.mark.parametrize('fault', [None, 'changed-body', 'changed-metadata', 'second-revision'])
def test_explicit_readback_verifies_stock_routing_metadata_projection(tmp_path, fault):
    (tmp_path / 'inventory-rejections').mkdir()
    identifier = 'D' * 22
    at = '2026-10-09T09:18:00-04:00'
    text = 'The porch list covers Saturday only.'
    payload = dict(content=text, date=at, metadata={'type': 'other', 'sm_source': 'hermes'})
    entry = dict(native_id=identifier, payload=payload, question_id='weekend-correction', tool_call_id='call_save')
    returned = text if fault != 'changed-body' else text + ' And Sunday.'
    metadata = {'type': 'other', **stock_temporal_metadata(at)}
    if fault == 'changed-metadata':
        metadata['additional'] = 'unowned'
    wire = dict(id=identifier, content=returned, metadata=metadata, system=dict(
        status='done', createdAt=at, updatedAt=at), chunks=[dict(id='C' * 22, position=0, content=returned)])
    native = dict(id=identifier, customId=None, containerTags=['week'], content=returned, metadata=metadata,
                  status='done', createdAt=at, updatedAt=at, latestRevision=2 if fault == 'second-revision' else 1,
                  activeContentUpdateId='A' * 22)
    witness = StockWeekWitness.__new__(StockWeekWitness)
    witness.root, witness.namespace, witness.explicit_writes = tmp_path, 'week', {}
    witness.read_native = lambda path: native
    witness.sdk = SimpleNamespace(documents=SimpleNamespace(get=lambda *args, **kwargs: SimpleNamespace(
        model_dump=lambda **options: wire)))
    if not fault:
        proof = witness.read_tool_document(entry)
        assert proof['chunk_anchor']['revision'] == 1 and proof['store_sha256'] == store.digest(entry)
    else:
        with pytest.raises(ValueError):
            witness.read_tool_document(entry)
