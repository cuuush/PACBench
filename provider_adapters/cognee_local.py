"""Single-document native Cognee qualification, not a completed benchmark arm.

The caller supplies the pinned plugin's HttpBackend connected to a verified local
server. No extraction hints, authored facts, fake assistant turn or alternate
transport are introduced. A fresh dataset is required to avoid stale completion.
"""
from __future__ import annotations

import hashlib
import time

from pacbench.cognee_memobase_setup import loopback_url


class QualificationError(RuntimeError):
    pass


def qualify_document(backend, *, service_url, dataset, text, timeout=300,
                     poll_interval=1, clock=time.monotonic, sleep=time.sleep):
    """Deliver once and witness native extraction and identical stored source.

    Errors are deliberately not retried: a timeout may hide a completed write.
    The backend must implement the official Hermes plugin's MemoryBackend API.
    Locality is verified against the backend's actual URL as well as the caller's
    declared URL; a declaration alone is not proof of transport configuration.
    """
    expected_url = loopback_url(service_url)
    actual_url = loopback_url(str(getattr(backend, 'url', '')))
    if actual_url != expected_url:
        raise QualificationError('Backend URL differs from the declared local service')
    if not dataset.startswith('pacbench_') or len(dataset) > 100:
        raise QualificationError('An isolated pacbench_ dataset is required')
    if not isinstance(text, str) or not text:
        raise QualificationError('Nonempty unmodified source text is required')
    if timeout <= 0 or poll_interval <= 0:
        raise ValueError('Positive deadline and polling interval required')
    deadline = clock() + timeout

    def remaining():
        value = deadline - clock()
        if value <= 0:
            raise QualificationError('Native Cognee qualification deadline elapsed; do not resubmit')
        return value

    backend.ensure_dataset(dataset=dataset, timeout=remaining())
    rows = [row for row in backend.list_datasets(timeout=remaining())
            if row.get('name') == dataset]
    if len(rows) != 1 or not rows[0].get('id'):
        raise QualificationError('Cannot resolve exactly one native dataset')
    dataset_id = rows[0]['id']
    existing = backend.list_dataset_data(dataset_id=dataset_id, timeout=remaining(), limit=1, offset=0)
    if existing:
        raise QualificationError('Dataset is not empty; use a fresh isolated qualification scope')
    previous = backend.dataset_pipeline_status(dataset_id=dataset_id,
                                               pipeline='cognify_pipeline', timeout=remaining())
    if previous:
        raise QualificationError('Fresh dataset has prior pipeline state; completion would be ambiguous')
    # This shipped backend uploads UTF-8 bytes as a content-hash-named multipart
    # text file and starts the native cognify pipeline in the background.
    result = backend.remember_permanent(text=text, dataset=dataset,
                                        session_ids=[], timeout=remaining())
    raw = getattr(result, 'raw', {})
    accepted_status = raw.get('status') if isinstance(raw, dict) else None
    if accepted_status not in ('running', 'completed'):
        raise QualificationError('Native write lacks an explicit accepted status; outcome uncertain')
    statuses = []
    while True:
        status = backend.dataset_pipeline_status(dataset_id=dataset_id,
                                                 pipeline='cognify_pipeline', timeout=remaining())
        if not statuses or statuses[-1] != status:
            statuses.append(status)
        # These are Cognee's native dataset pipeline state names, not generic
        # process success or the code-graph pipeline.
        if status == 'DATASET_PROCESSING_ERRORED':
            raise QualificationError('Native Cognee document extraction failed')
        if status == 'DATASET_PROCESSING_COMPLETED':
            break
        sleep(min(poll_interval, remaining()))
    docs = backend.list_dataset_data(dataset_id=dataset_id, timeout=remaining(), limit=2, offset=0)
    if len(docs) != 1 or not docs[0].get('id'):
        raise QualificationError('Single-source dataset has unexpected stored document count')
    data_id = docs[0]['id']
    stored = backend.read_raw_data(dataset_id=dataset_id, data_id=data_id, timeout=remaining())
    if stored != text:
        raise QualificationError('Native stored raw document differs from delivered source')
    return {
        'protocol': 'cognee-native-document-qualification-v1',
        'service': expected_url, 'dataset': dataset, 'dataset_id': dataset_id,
        'data_id': data_id, 'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'accepted_status': accepted_status, 'pipeline': 'cognify_pipeline',
        'observed_statuses': statuses, 'stored_source_verified': True,
        'processing_complete': True,
        'fresh_session_recall': 'not-run',
        'provider_qualified': False,
    }


def qualify_incremental_document(backend, *, service_url, dataset, text, prior,
        processing_status, timeout=600, poll_interval=1,
        clock=time.monotonic, sleep=time.sleep):
    """Witness this new item's extraction, independent of an older run status.

    `prior` contains only previously verified source IDs and raw-content hashes.
    The per-item completion endpoint is read-only; no pipeline is forced or
    resubmitted. A pre-existing completion for another item cannot pass.
    """
    expected_url = loopback_url(service_url)
    if loopback_url(str(getattr(backend, 'url', ''))) != expected_url:
        raise QualificationError('Backend URL differs from the declared local service')
    if not dataset.startswith('pacbench_') or len(dataset)>100 or not isinstance(text,str) or not text:
        raise QualificationError('Isolated dataset and nonempty unmodified source required')
    if timeout<=0 or poll_interval<=0:raise ValueError('Positive deadline and polling interval required')
    deadline=clock()+timeout
    def remaining():
        left=deadline-clock()
        if left<=0:raise QualificationError('Native incremental extraction deadline elapsed; do not resubmit')
        return left
    backend.ensure_dataset(dataset=dataset,timeout=remaining())
    datasets=[r for r in backend.list_datasets(timeout=remaining()) if r.get('name')==dataset]
    if len(datasets)!=1 or not datasets[0].get('id'):
        raise QualificationError('Cannot resolve exactly one native dataset')
    dataset_id=datasets[0]['id']
    def documents():
        rows=[]
        for offset in range(0,100001,100):
            page=backend.list_dataset_data(dataset_id=dataset_id,timeout=remaining(),limit=100,offset=offset)
            if not isinstance(page,list):raise QualificationError('Native document listing unavailable')
            rows.extend(page)
            if len(page)<100:break
        else:raise QualificationError('Native document listing exceeds bound')
        ids=[r.get('id') for r in rows]
        if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):
            raise QualificationError('Native document IDs missing or duplicated')
        return rows
    before=documents()
    expected={p['data_id']:p['source_sha256'] for p in prior}
    if len(expected)!=len(prior) or set(expected)!={r['id'] for r in before}:
        raise QualificationError('Dataset differs from verified prior source receipts')
    for identifier,digest in expected.items():
        actual=backend.read_raw_data(dataset_id=dataset_id,data_id=identifier,timeout=remaining())
        if hashlib.sha256(actual.encode()).hexdigest()!=digest:
            raise QualificationError('Previously delivered native source changed')
    source_hash=hashlib.sha256(text.encode()).hexdigest()
    if source_hash in expected.values():
        raise QualificationError('Duplicate source; uncertain writes must not be resubmitted')
    result=backend.remember_permanent(text=text,dataset=dataset,session_ids=[],timeout=remaining())
    raw=getattr(result,'raw',{})
    if not isinstance(raw,dict) or raw.get('status') not in ('running','completed'):
        raise QualificationError('Native write lacks accepted status; outcome uncertain')
    statuses=[];observations=[];data_id=None
    while True:
        status=backend.dataset_pipeline_status(dataset_id=dataset_id,pipeline='cognify_pipeline',timeout=remaining())
        if not statuses or statuses[-1]!=status:statuses.append(status)
        if status=='DATASET_PROCESSING_ERRORED':raise QualificationError('Native incremental extraction failed')
        docs=documents()
        if not set(expected)<={r['id'] for r in docs} or len(docs)>len(expected)+1:
            raise QualificationError('Unexpected native document addition/removal')
        added=[r for r in docs if r['id'] not in expected]
        if added:
            identifier=added[0]['id']
            if data_id is not None and data_id!=identifier:raise QualificationError('Native write identity changed')
            data_id=identifier
            stored=backend.read_raw_data(dataset_id=dataset_id,data_id=data_id,timeout=remaining())
            if stored!=text:raise QualificationError('Native stored raw document differs from delivered source')
        observed=processing_status(dataset_id=dataset_id,timeout=remaining())
        items=observed.get('items',[])
        ids=[i.get('id') for i in items]
        if (len(ids)!=len(set(ids)) or any(type(i.get('completed')) is not bool for i in items) or
                observed.get('total')!=len(items) or
                observed.get('completed')!=sum(i['completed'] for i in items) or
                observed.get('pending')!=sum(not i['completed'] for i in items)):
            raise QualificationError('Invalid native per-item completion response')
        if not observations or observations[-1]!=observed:observations.append(observed)
        if (data_id and status=='DATASET_PROCESSING_COMPLETED' and
                set(ids)==set(expected)|{data_id} and all(i['completed'] for i in items)):
            break
        sleep(min(poll_interval,remaining()))
    return {'protocol':'cognee-native-incremental-document-v2',
        'service':expected_url,'dataset':dataset,'dataset_id':dataset_id,
        'data_id':data_id,'source_sha256':source_hash,'prior_sources':prior,
        'accepted_status':raw['status'],'pipeline':'cognify_pipeline',
        'observed_statuses':statuses,'item_completion_observations':observations,
        'stored_source_verified':True,'processing_complete':True,
        'fresh_session_recall':'not-run','provider_qualified':False}
