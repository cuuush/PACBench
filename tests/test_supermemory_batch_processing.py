"""Exercise the first canonical Week release through the stock SDK offline."""
from hashlib import sha256
import json
from types import SimpleNamespace

import httpx
import pytest

from pacbench.providers.supermemory import observation as observation
from pacbench.workflows.delivery import submit_document
from pacbench.campaigns.pizza_week import compile_week


@pytest.mark.parametrize(('fault', 'error'), [
    (None, None), ('framing-lf', None), ('ack', ValueError), ('identity', ValueError),
    ('content', ValueError), ('interior-whitespace', ValueError), ('metadata', ValueError),
    ('metadata-omitted', ValueError), ('metadata-null', ValueError), ('metadata-empty', ValueError),
    ('wrong-source-date', ValueError), ('wrong-boundary', ValueError),
    ('version', ValueError), ('failed', ValueError), ('inventory', ValueError), ('pending', TimeoutError),
    ('status-timeout', TimeoutError),
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
            saved[doc.filename] = dict(id=doc.filename, type='text', content=doc.text, metadata=observation.stock_temporal_metadata(doc.cutoff),
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
        if fault == 'status-timeout' and ident == last:
            raise httpx.ReadTimeout('native status read', request=request)
        row = {**saved[ident], 'system': dict(saved[ident]['system'])}
        if ident == last:
            row['system']['dreamingStatus'] = 'dreaming'
            if fault == 'pending' or reads[ident] == 1:
                row.update(content=None)
                row['system']['status'] = 'embedding'
            elif fault == 'identity':
                row['id'] = 'other'
            elif fault == 'framing-lf':
                row['content'] = row['content'][:-1]
            elif fault == 'content':
                row['content'] = batch[0].text
            elif fault == 'interior-whitespace':
                row['content'] = row['content'].replace('\n', '\r\n', 1)
            elif fault == 'metadata':
                row['metadata'] = {**row['metadata'], 'foreign': True}
            elif fault == 'metadata-empty':
                row['metadata'] = {}
            elif fault == 'wrong-source-date':
                row['metadata']['temporalContext'] = {'documentDate':'2026-10-06', 'eventDate':None}
            elif fault == 'wrong-boundary':
                row['metadata']['temporalEventEndMs'] += 1
            elif fault == 'metadata-omitted':
                del row['metadata']
            elif fault == 'metadata-null':
                row['metadata'] = None
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
            with pytest.raises(error) as err:
                observation.wait_for_batch_processing(client, 'smoke', batch, acks, deadline=2.)
            metadata_states = {'metadata':'mapping-with-4-fields', 'metadata-null':'null',
                               'metadata-omitted':'omitted'}
            if fault in metadata_states:
                assert 'metadata_state='+metadata_states[fault] in str(err.value)
                assert last in str(err.value) and 'foreign' not in str(err.value)
            if fault == 'status-timeout':
                assert clock[0] == 2. and reads[last] == 4
            return
        result = observation.wait_for_batch_processing(client, 'smoke', batch, acks, deadline=2.)

    assert list(result['documents']) == list(acks)
    assert sum(reads.values()) == 13
    for doc in batch:
        omitted = int(fault == 'framing-lf' and doc.filename == last)
        assert result['documents'][doc.filename] == dict(
            status='done', sha256=doc.sha256, updated_at=doc.cutoff,
            metadata=observation.stock_temporal_metadata(doc.cutoff), native_revision=None,
            original_bytes=len(doc.text.encode()), canonical_bytes=len(doc.text.encode())-omitted,
            removed_trailing_lf_bytes=omitted,
            returned_sha256=(sha256(doc.text[:-1].encode()).hexdigest()
                             if fault == 'framing-lf' and doc.filename == last else doc.sha256),
            source_projection=('single-final-lf-omitted' if fault == 'framing-lf' and doc.filename == last else 'exact'),
            dreaming_status='dreaming' if doc.filename == last else None)


def test_pinned_temporal_projection_keeps_submitted_calendar_date():
    assert observation.stock_temporal_metadata('2026-10-05T21:00:00-04:00') == {
        'temporalContext': {'documentDate': '2026-10-05', 'eventDate': None},
        'temporalEventEndMs': 1791244800000,
        'temporalEventStartMs': 1791158400000,
    }


@pytest.mark.parametrize('field', [None, 'id', 'customId', 'containerTags', 'latestRevision',
                                    'updatedAt', 'createdAt', 'content', 'metadata'])
def test_native_version_is_bound_to_accepted_readback(field):
    document = compile_week('continuing-account')[1][0]
    metadata = observation.stock_temporal_metadata(document.cutoff)
    wire = dict(id=document.filename, content=document.text[:-1], metadata=metadata,
                system=dict(status='done', createdAt=document.cutoff, updatedAt=document.cutoff))
    native = {'id':'d'*22, 'customId':document.filename, 'containerTags':['only-owned'],
              'latestRevision':0, 'status':'done', 'createdAt':document.cutoff, 'updatedAt':document.cutoff,
              'content':wire['content'], 'metadata':{**metadata, 'sm_internal-hidden':True}}
    bad = {'id':'f'*22, 'customId':'other', 'containerTags':['another'], 'latestRevision':-1,
           'updatedAt':'2026-10-08T00:00:00Z', 'createdAt':'2026-10-03T00:00:00Z', 'content':'changed',
           'metadata':{**metadata, 'extra-user-metadata':True}}
    if field is not None:
        with pytest.raises(ValueError):
            observation.verify_native_version({**native, field:bad[field]}, document,
                native_id='d'*22, namespace='only-owned', wire=wire)
    else:
        assert observation.verify_native_version(native, document,
            native_id='d'*22, namespace='only-owned', wire=wire) == 0
