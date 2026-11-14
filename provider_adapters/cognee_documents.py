"""Single-source native Cognee extraction; never synthesize ingestion turns."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from .cognee_completion import pipeline_rows, completed, require_idle

from provider_adapters.cognee_local import qualify_incremental_document
from provider_adapters.reme_memos_documents import document


def ingest(source, root):
    root = Path(root).resolve()
    config = json.loads((root / 'cognee-document-config.json').read_text())
    text, manifest, digest = document(Path(source))
    # Import exactly the installed native directory plugin used by Hermes.
    sys.path.insert(0, str(root / 'hermes-home/plugins/cognee'))
    from cognee_integration_hermes.http_backend import HttpBackend
    backend = HttpBackend()
    backend.connect(url=config['service_url'], api_key='', timeout=30)
    from .cognee_wire_audit import observe
    observe(backend,root,source,text,manifest)
    prior=[]
    for path in sorted((root/'ingestion').glob('*.json')):
        record=json.loads(path.read_text())
        recovery=root/'ingestion-recovery'/path.name
        if recovery.exists():
            import hashlib
            overlay=json.loads(recovery.read_text())
            if overlay.get('original_record_sha256')!=hashlib.sha256(path.read_bytes()).hexdigest():
                raise RuntimeError('Recovery evidence does not bind the original failed import')
            record=overlay['verified_record']
        native=record.get('result',{})
        if record.get('ready') is not True or native.get('processing_complete') is not True:
            raise RuntimeError('Prior Cognee ingestion is uncertain; do not continue or resubmit')
        prior.append({'data_id':native['data_id'],'source_sha256':native['source_sha256']})
    def processing_status(*,dataset_id,timeout):
        # Read-only documented native API; the shipped backend owns transport.
        from uuid import UUID
        identifier=str(UUID(dataset_id))
        return backend._request('GET',f'/api/v1/datasets/{identifier}/processing-status?pipeline=cognify_pipeline',timeout=timeout)
    before=pipeline_rows(root)
    require_idle(before)
    prior_ids={r['id'] for r in before}
    deadline=time.monotonic()+1800
    from .cognee_capture_witness import captures
    native_captures=captures(root,prior)
    result = qualify_incremental_document(backend, service_url=config['service_url'],
        dataset=config['dataset'], text=text, prior=prior, processing_status=processing_status, timeout=1800,native_captures=native_captures)
    while True:
        rows=pipeline_rows(root)
        if completed(rows,prior_ids):break
        if time.monotonic()>=deadline:raise RuntimeError('Native automatic improve did not finish; import remains incomplete')
        time.sleep(1)
    result['native_operation_completion']={'state':'pass','rows':[r for r in rows if r['id'] not in prior_ids]}
    return {**result, 'ready': result['processing_complete'],
            'interface': 'Native Cognee remember_permanent /api/v1/remember',
            'ingestion_condition': 'native-document-extraction-v1',
            'document_sha256': digest, 'source_ids': manifest['source_ids'],
            'source_at': manifest['at'], 'hermes_ingestion_turns': 0}
