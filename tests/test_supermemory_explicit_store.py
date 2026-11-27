"""Check exact stock tool acknowledgments and separate native inventories."""

from types import SimpleNamespace

import pytest

from pacbench.providers import supermemory_evidence as store



from pacbench.providers.supermemory import stock_temporal_metadata

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
    if not fault:
        proof = store.verify_explicit_document(entry, native, wire, native, 'week')
        assert proof['chunk_anchor']['revision'] == 1 and proof['store_sha256'] == store.digest(entry)
    else:
        with pytest.raises(ValueError):
            store.verify_explicit_document(entry, native, wire, native, 'week')
