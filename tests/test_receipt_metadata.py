"""Reviewed receipt helpers remain offline and do not depend on absent transport code."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from pacbench.receipt_costs import persist_metadata, retained_metadata, safe_response_metadata


def test_metadata_helpers_allowlist_content_and_keep_original_ledger_unchanged(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_bytes(b'original-accounting-evidence')
    metadata = safe_response_metadata({'id': 'gen-observed', 'usage': {'cost': '.001'},
                                       'choices': [{'content': 'PRIVATE'}]},
                                      {'Authorization': 'PRIVATE', 'x-generation-id': 'gen-observed'})
    persist_metadata(ledger, 'request', metadata)
    row = retained_metadata(ledger, {'id': 'request'})
    assert row['cost_receipts'][0]['usd'] == '0.001'
    assert row['receipt_generation_ids'] == ['gen-observed']
    assert 'PRIVATE' not in json.dumps(row)
    assert ledger.read_bytes() == b'original-accounting-evidence'


def test_concurrent_metadata_capture_is_idempotent(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_bytes(b'unchanged')
    metadata = safe_response_metadata({'id': 'gen-observed', 'usage': {'cost': '0'}})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: persist_metadata(ledger, 'request', metadata), range(8)))
    assert len(list((tmp_path / 'receipt-metadata/request').glob('*.json'))) == 1
    assert retained_metadata(ledger, {'id': 'request'})['cost_receipts'][0]['usd'] == '0'
    assert ledger.read_bytes() == b'unchanged'


def test_corrupt_sidecar_marks_incomplete_observability_without_ledger_mutation(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_bytes(b'unchanged')
    folder = tmp_path / 'receipt-metadata/request'
    folder.mkdir(parents=True)
    (folder / 'bad.json').write_text('{"payload":"PRIVATE"}')
    row = retained_metadata(ledger, {'id': 'request'})
    assert row['receipt_metadata_incomplete']
    assert 'PRIVATE' not in json.dumps(row)
    assert ledger.read_bytes() == b'unchanged'


def test_invalid_request_identity_cannot_write_metadata_outside_receipt_directory(tmp_path):
    with pytest.raises(ValueError):
        persist_metadata(tmp_path / 'ledger.json', '../escape', {})
    assert not (tmp_path / 'receipt-metadata').exists()
