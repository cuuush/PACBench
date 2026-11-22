"""Exercise the first canonical Week release through the stock SDK offline."""
import json
from types import SimpleNamespace

import httpx
import pytest

from pacbench import supermemory_week_observation as observation
from pacbench.workflow_delivery import submit_document
from pacbench.workflow_spec import compile_week


@pytest.mark.parametrize(('fault', 'error'), [
    (None, None), ('ack', ValueError), ('identity', ValueError), ('content', ValueError),
    ('version', ValueError), ('failed', ValueError), ('inventory', ValueError), ('pending', TimeoutError),
])
def test_stock_sdk_first_evening(monkeypatch, fault, error):
    sdk = pytest.importorskip('supermemory')
    _, exports, _, _ = compile_week('continuing-account')
    batch = [doc for doc in exports if doc.cutoff == exports[0].cutoff]
    assert len(batch) == 12 and sum(len(doc.source_ids) for doc in batch) == 39
    last = batch[-1].filename
    saved, acks = {}, {}
    reads = {doc.filename: 0 for doc in batch}
    clock = [0.]
    monkeypatch.setattr(observation, 'time', SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda delay: clock.__setitem__(0, clock[0] + delay)))

    def respond(request):
        assert request.url.host == 'offline.invalid'
        path = request.url.path
        if (request.method, path) == ('POST', '/ns/smoke/document'):
            doc = batch[len(saved)]
            assert not request.url.query
            assert json.loads(request.content) == {'id': doc.filename, 'content': doc.text, 'date': doc.cutoff}
            saved[doc.filename] = dict(id=doc.filename, type='text', content=doc.text, metadata={},
                                       system=dict(status='done', createdAt=doc.cutoff, updatedAt=doc.cutoff))
            ack_id = batch[0].filename if fault == 'ack' and doc.filename == last else doc.filename
            return httpx.Response(200, json=dict(id=ack_id, status='queued'))
        if (request.method, path) == ('POST', '/ns/smoke/list/documents'):
            rows = list(saved.values())
            if rows and fault == 'inventory':
                rows[-1] = {**rows[-1], 'id': 'unreleased'}
            return httpx.Response(200, json=dict(documents=rows, chunks=[], memories=[],
                pagination=dict(currentPage=1, totalPages=int(bool(rows)), totalItems=len(rows))))
        ident = path.rsplit('/', 1)[-1]
        assert request.method == 'GET' and path == '/ns/smoke/document/' + ident
        reads[ident] += 1
        row = {**saved[ident], 'system': dict(saved[ident]['system'])}
        if ident == last:
            row['system']['dreamingStatus'] = 'dreaming'
            if fault == 'pending' or reads[ident] == 1:
                row.update(content=None)
                row['system']['status'] = 'embedding'
            elif fault == 'identity':
                row['id'] = 'other'
            elif fault == 'content':
                row['content'] = row['content'].rstrip('\n')
            elif fault == 'version':
                del row['system']['updatedAt']
            elif fault == 'failed':
                row['system']['status'] = 'failed'
        # JSON escape spelling does not change the decoded canonical text.
        return httpx.Response(200, content=json.dumps(row, ensure_ascii=True))

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = sdk.Supermemory(base_url='http://offline.invalid', api_key='fixture',
                                  max_retries=0, httpx_client=transport)
        observation.verify_release_inventory(client, 'smoke', (), deadline=2.)
        for doc in batch:
            acks[doc.filename] = submit_document('supermemory', client, 'smoke', doc)
        if error:
            with pytest.raises(error):
                observation.wait_for_batch_processing(client, 'smoke', batch, acks, deadline=2.)
            return
        result = observation.wait_for_batch_processing(client, 'smoke', batch, acks, deadline=2.)

    assert list(result['documents']) == list(acks)
    assert sum(reads.values()) == 13
    for doc in batch:
        assert result['documents'][doc.filename] == dict(
            status='done', sha256=doc.sha256, updated_at=doc.cutoff,
            dreaming_status='dreaming' if doc.filename == last else None)
