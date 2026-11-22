"""Read-only ordinary document processing on the stock Supermemory v5 API."""
from hashlib import sha256
from math import ceil, isfinite
import time

from .document_audit import same_submitted_text
from .workflow_spec import instant

PENDING = {'queued', 'extracting', 'processing', 'chunking', 'embedding', 'indexing'}


def _wire(response):
    return response.model_dump(mode='json', by_alias=True, exclude_unset=True)


def _options(deadline):
    remaining = deadline - time.monotonic()
    if not isfinite(remaining) or remaining <= 0:
        raise TimeoutError('Supermemory document-processing deadline expired')
    return {'timeout': min(10, remaining), 'max_retries': 0}


def verify_release_inventory(client, namespace, expected_ids, *, deadline):
    """Require the exact namespace inventory, including previously witnessed IDs."""
    expected = set(expected_ids)
    if not namespace or len(expected) != len(expected_ids):
        raise ValueError('invalid Supermemory release inventory')
    seen = set()
    pages = max(1, ceil(len(expected) / 100))
    for page in range(1, pages + 1):
        result = _wire(client.list(namespace, 'documents', page=page, limit=100,
                                   sort='createdAt', order='asc', request_options=_options(deadline)))
        _options(deadline)
        pagination = result['pagination']
        rows = result['documents']
        ids = {row['id'] for row in rows}
        if (pagination['currentPage'] != page or pagination['totalItems'] != len(expected)
                or pagination['totalPages'] not in ({pages} if expected else {0, 1})
                or len(rows) != min(100, len(expected) - len(seen))
                or len(ids) != len(rows) or seen & ids or not ids <= expected):
            raise ValueError('unexpected documents in Supermemory namespace')
        seen.update(ids)


def wait_for_batch_processing(client, namespace, documents, acknowledgements, *, deadline, previous_ids=()):
    """Verify one release in an exclusively owned namespace using ordered add responses."""
    expected = {doc.filename: doc for doc in documents}
    if (not namespace or not expected or len(expected) != len(documents)
            or len({doc.cutoff for doc in documents}) != 1 or tuple(acknowledgements) != tuple(expected)
            or len(set(previous_ids)) != len(previous_ids) or set(previous_ids) & expected.keys()):
        raise ValueError('expected one newly acknowledged release with distinct IDs')
    for ident, ack in acknowledgements.items():
        if ack.id != ident or ack.status not in PENDING | {'done'}:
            raise ValueError(f'invalid Supermemory add acknowledgement for {ident}')

    observed = {}
    while len(observed) < len(expected):
        for ident, doc in expected.items():
            if ident in observed:
                continue
            row = _wire(client.documents.get(namespace, ident, request_options=_options(deadline)))
            _options(deadline)
            if row['id'] != ident:
                raise ValueError(f'foreign Supermemory document readback for {ident}')
            system = row['system']
            status = system.get('status')
            if status in PENDING:
                continue
            if status != 'done':
                raise ValueError(f'Supermemory document {ident} processing unsuccessful: {status!r}')
            content = row.get('content')
            returned_sha = sha256(content.encode('utf-8')).hexdigest() if isinstance(content, str) else None
            if not same_submitted_text(content, doc.text) or row.get('metadata') != {}:
                raise ValueError(f'Supermemory v5 source mismatch for {ident} at {status}: '
                                 f'expected_sha256={doc.sha256}, returned_sha256={returned_sha}, '
                                 f'metadata_matches={row.get("metadata") == {}}')
            version = system.get('updatedAt')
            if not isinstance(version, str):
                raise ValueError(f'Supermemory document {ident} lacks a version timestamp')
            instant(version)
            observed[ident] = dict(status=status, sha256=doc.sha256, updated_at=version,
                                   returned_sha256=returned_sha,
                                   source_projection=('exact' if content == doc.text else 'single-final-lf-omitted'),
                                   dreaming_status=system.get('dreamingStatus'))
        if len(observed) < len(expected):
            time.sleep(min(0.5, _options(deadline)['timeout']))
    verify_release_inventory(client, namespace, (*previous_ids, *expected), deadline=deadline)
    return dict(condition='stock-document-processing-v1', namespace=namespace,
                cutoff=documents[0].cutoff, documents=observed)
