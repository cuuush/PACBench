"""Stock Supermemory document and processing observations."""
from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from datetime import time as midnight
from hashlib import sha256
from math import ceil, isfinite

from pacbench.hermes_gateway import GatewayLifecycleError
from pacbench.workflows.spec import instant


def qualify_source(raw,*,identifier,text,metadata):
    canonical=raw.get('content');actual=raw.get('metadata')
    normalization='none' if canonical==text else 'native-single-terminal-lf-elision-v1' if text.endswith('\n') and canonical==text[:-1] else None
    if (raw.get('id')!=identifier or normalization is None or not isinstance(actual,dict) or
        any(actual.get(k)!=v for k,v in metadata.items()) or
        set(actual)-set(metadata)-{'temporalContext','temporalEventStartMs','temporalEventEndMs'}):
        raise ValueError('Native source or submitted metadata differs beyond witnessed canonicalization')
    return normalization


def qualify(raw,status,*,identifier,text,metadata):
    qualify_source(raw,identifier=identifier,text=text,metadata=metadata)
    if (
        status.get('id')!=identifier or status.get('status')!='done' or status.get('dreaming_status')!='done'):
        raise ValueError('Accepted native source is not verified fully processed and exact')


def accepted_failure(record):
    native=record.get('result',{});failure=native.get('native_error',{})
    identifier=native.get('result',{}).get('id')
    explicit=failure.get('commit_state')=='accepted' and failure.get('phase')=='document-status'
    timed_out=(native.get('reason')=='Native memory processing deadline exceeded' and
        native.get('result',{}).get('status')=='queued' and
        bool(native.get('processing_status')) and all(r.get('id')==identifier and
        r.get('status') in {'queued','processing','done'} for r in native['processing_status']))
    if record.get('ready') is not False or record.get('state')!='failed' or not identifier or not (explicit or timed_out):
        raise ValueError('Only an identified accepted status-read failure or witnessed pending timeout is recoverable')
    return identifier


PENDING = {'queued', 'extracting', 'processing', 'chunking', 'embedding', 'indexing'}


def stock_temporal_metadata(cutoff):
    """Stock v0.0.9 projection of an Add request with date and no user metadata."""
    date = datetime.fromisoformat(cutoff).date()
    start = datetime.combine(date, midnight.min, tzinfo=timezone.utc)
    return {
        'temporalContext': {'documentDate': date.isoformat(), 'eventDate': None},
        'temporalEventStartMs': int(start.timestamp()) * 1000,
        'temporalEventEndMs': int((start + timedelta(days=1)).timestamp()) * 1000,
    }


def verify_native_version(native, document, *, native_id, namespace, wire):
    """Bind the live v5 projection to the accepted database ID and revision."""
    if ((native.get('id'), native.get('customId'), native.get('containerTags'))
            != (native_id, document.filename, [namespace])):
        raise ValueError('Foreign Supermemory native readback identity or namespace')
    revision = native.get('latestRevision')
    system = wire['system']
    if (type(revision) is not int or revision < 0 or native.get('status') != system.get('status')
            or instant(native['updatedAt']) != instant(system['updatedAt'])
            or instant(native['createdAt']) != instant(system['createdAt'])):
        raise ValueError('Supermemory native revision or version timestamp differs from v5')
    if native.get('content') != wire.get('content'):
        raise ValueError('Supermemory native content differs from its v5 projection')
    # These internal keys are deliberately excluded by the pinned v5 serializer.
    # Every returned caller-visible value must still match the live native row.
    metadata = native.get('metadata')
    if not isinstance(metadata, dict):
        raise ValueError('Missing Supermemory native metadata')
    public = {k: value for k, value in metadata.items() if not k.startswith('sm_') and k != 'commonQuestions'}
    if public != wire.get('metadata'):
        raise ValueError('Supermemory native public metadata differs from v5')
    return revision


def _wire(response):
    return response.model_dump(mode='json', by_alias=True, exclude_unset=True)


def read_options(deadline, *, max_timeout=10):
    remaining = deadline - time.monotonic()
    if not isfinite(remaining) or remaining <= 0:
        raise TimeoutError('Supermemory document-processing deadline expired')
    return {'timeout': min(max_timeout, remaining), 'max_retries': 0}


def verify_release_inventory(client, namespace, expected_ids, *, deadline, on_rejection=None):
    """Require the exact namespace inventory, including previously witnessed IDs."""
    expected = set(expected_ids)
    if not namespace or len(expected) != len(expected_ids):
        raise ValueError('invalid Supermemory release inventory')
    seen = set()
    pages = max(1, ceil(len(expected) / 100))
    for page in range(1, pages + 1):
        result = _wire(client.list(namespace, 'documents', page=page, limit=100,
                                   sort='createdAt', order='asc', request_options=read_options(deadline)))
        read_options(deadline)
        try:
            pagination = result['pagination']
            rows = result['documents']
            ids = {row['id'] for row in rows}
            if (pagination['currentPage'] != page or pagination['totalItems'] != len(expected)
                    or pagination['totalPages'] not in ({pages} if expected else {0, 1})
                    or len(rows) != min(100, len(expected) - len(seen))
                    or len(ids) != len(rows) or seen & ids or not ids <= expected):
                raise ValueError('unexpected documents in Supermemory namespace')
        except (KeyError, TypeError, ValueError):
            if on_rejection is not None:
                on_rejection('v5', expected, result)
            raise
        seen.update(ids)


def wait_for_batch_processing(client, namespace, documents, acknowledgements, *, deadline,
                              previous_ids=(), before_read=None, native_readback=None):
    """Verify one release in an exclusively owned namespace using ordered add responses."""
    if tuple(acknowledgements) != tuple(doc.filename for doc in documents):
        raise ValueError('expected one newly acknowledged release with distinct IDs')
    for ident, ack in acknowledgements.items():
        if ack.id != ident or ack.status not in PENDING | {'done'}:
            raise ValueError(f'invalid Supermemory add acknowledgement for {ident}')
    return read_release_processing(client, namespace, documents, deadline=deadline, other_ids=previous_ids,
                                   before_read=before_read, native_readback=native_readback)


def read_release_processing(client, namespace, documents, *, deadline, other_ids=(),
                            before_read=None, native_readback=None, on_rejection=None):
    """Read processing state and content for one exact release without doing an add."""
    from supermemory import APITimeoutError

    expected = {doc.filename: doc for doc in documents}
    if (not namespace or not expected or len(expected) != len(documents)
            or len({doc.cutoff for doc in documents}) != 1
            or len(set(other_ids)) != len(other_ids) or set(other_ids) & expected.keys()):
        raise ValueError('expected one Supermemory release with distinct IDs')

    observed = {}
    while len(observed) < len(expected):
        for ident, doc in expected.items():
            if ident in observed:
                continue
            if before_read is not None:
                before_read()
            try:
                response = client.documents.get(namespace, ident, request_options=read_options(deadline))
            except APITimeoutError:
                continue  # Only retry this observation within the release deadline.
            row = _wire(response)
            read_options(deadline)
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
            metadata = row.get('metadata')
            expected_metadata = stock_temporal_metadata(doc.cutoff)
            if not same_submitted_text(content, doc.text) or metadata != expected_metadata:
                metadata_state = ('omitted' if 'metadata' not in row else 'null' if metadata is None
                                  else f'mapping-with-{len(metadata)}-fields' if isinstance(metadata, dict)
                                  else 'unexpected-type')
                raise ValueError(f'Supermemory v5 source mismatch for {ident} at {status}: '
                                 f'expected_sha256={doc.sha256}, returned_sha256={returned_sha}, '
                                 f'metadata_state={metadata_state}')
            version = system.get('updatedAt')
            created = system.get('createdAt')
            if (not isinstance(version, str) or not isinstance(created, str)
                    or instant(version) < instant(created)):
                raise ValueError(f'Supermemory document {ident} lacks a valid version timestamp')
            native_revision = native_readback(doc, row) if native_readback is not None else None
            original_size, retained_size = len(doc.text.encode('utf-8')), len(content.encode('utf-8'))
            observed[ident] = dict(status=status, sha256=doc.sha256, updated_at=version,
                                   returned_sha256=returned_sha, original_bytes=original_size,
                                   canonical_bytes=retained_size, removed_trailing_lf_bytes=original_size-retained_size,
                                   source_projection=('exact' if content == doc.text else 'single-final-lf-omitted'),
                                   metadata=metadata, native_revision=native_revision,
                                   dreaming_status=system.get('dreamingStatus'))
        if len(observed) < len(expected):
            time.sleep(min(0.5, read_options(deadline)['timeout']))
    if before_read is not None:
        before_read()
    verify_release_inventory(client, namespace, (*other_ids, *expected), deadline=deadline,
                             on_rejection=on_rejection)
    return dict(condition='stock-document-processing-v1', namespace=namespace,
                cutoff=documents[0].cutoff, documents=observed)


def wait_for_settled_checkpoint(endpoint, key, namespace):
    """Read the legacy checkpoint inventory until every document is done."""
    deadline = time.monotonic() + 100
    while True:
        request = urllib.request.Request(
            endpoint + '/v3/documents/list',
            data=json.dumps({'containerTags': [namespace], 'includeContent': False, 'page': 1, 'limit': 50}).encode(),
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        inventory = [{'id': row.get('customId', row.get('custom_id')) or row['id'],
                      'status': (row.get('system') or {}).get('status') or row.get('status')}
                     for row in result['memories']]
        if not inventory or len(inventory) >= 50 or any(row['status'] in ('failed', 'error', None) for row in inventory):
            raise GatewayLifecycleError('Supermemory native document readiness unavailable')
        if all(row['status'] == 'done' for row in inventory):
            return inventory
        if time.monotonic() >= deadline:
            raise GatewayLifecycleError('Supermemory native writes remained queued')
        time.sleep(.5)


def same_submitted_text(observed,text):
    # Native storage trims the export's single framing LF. Message bodies and
    # all internal whitespace must still match; never normalize their text.
    return isinstance(observed,str) and (observed==text or text.endswith('\n') and observed==text[:-1])


def audit_supermemory_receipt(result, day, text):
    statuses=result.get('processing_status',[])
    final=statuses[-1] if statuses else {}
    errors=[]
    if result.get('delivery_options') is not None and result['delivery_options']!={'dreaming':'instant'}:
        errors.append('Supermemory ingestion mode differs from the declared daily-batch connector')
    if (final.get('status')!='done' or
            final.get('dreaming_status',final.get('dreamingStatus'))!='done'):
        errors.append('Supermemory memory consolidation did not complete')
    v5=result.get('native_api_protocol')=='supermemory-v5-documents-v1'
    system=final.get('native_system') or {}
    native_phase=system.get('dreaming_status',system.get('dreamingStatus'))
    witness=final.get('native_completion_witness') or {}
    if v5 and native_phase is None:
        content=final.get('content')
        if (witness.get('protocol')=='supermemory-native-v3-completion-observer-v1'
                and witness.get('namespace')==result.get('container_tag')
                and witness.get('public_id')==day and witness.get('native_id')
                and witness.get('status')=='done' and witness.get('processing_forced') is False
                and witness.get('metadata')==final.get('metadata') and isinstance(content,str)
                and witness.get('content_sha256')==hashlib.sha256(content.encode()).hexdigest()):
            native_phase=witness.get('dreaming_status')
    identity_ok=(final.get('id')==day and final.get('request_namespace')==result.get('request_namespace')==result.get('container_tag')
                 and (final.get('metadata') or {}).get('collected_at')
                 and (final.get('native_system') or {}).get('status')=='done'
                 and native_phase=='done') if v5 else (
                     final.get('custom_id',final.get('customId'))==day and result.get('container_tag') in final.get('container_tags',final.get('containerTags',[])))
    if (not final.get('id') or final.get('id')!=result.get('result',{}).get('id') or not identity_ok or
            not same_submitted_text(final.get('content'),text) or
            not result.get('container_tag')):
        errors.append('Supermemory completion receipt differs from submitted source or namespace')
    return errors
