"""Verify native Cognee observation credentials and turn persistence."""
import hashlib
from pathlib import Path
import sys
import time

from pacbench.canary_capability import read
from pacbench.gateway_reuse import atomic
from pacbench.hermes_gateway import GatewayLifecycleError
from .cognee_completion import completed, pipeline_rows, require_idle


def native(endpoint, dataset, home):
    sys.path.insert(0, str(home / 'plugins/cognee'))
    from cognee_integration_hermes.http_backend import HttpBackend
    backend = HttpBackend(agent_session_name='pacbench-native-checkpoint')
    backend.connect(url=endpoint, api_key='', timeout=25)
    options = [d for d in backend.list_datasets(timeout=75) if d['name'] == dataset]
    if len(options) != 1:
        backend.close(timeout=5)
        raise GatewayLifecycleError('Cognee native dataset identity differs')
    return backend, options[0]['id']


def documents(backend, dataset_id):
    inventory = backend.list_dataset_data(dataset_id=dataset_id, timeout=75, limit=100, offset=0)
    if len(inventory) >= 100:
        raise GatewayLifecycleError('Cognee checkpoint inventory exceeds native page')
    return {row['id']: hashlib.sha256(backend.read_raw_data(dataset_id=dataset_id, data_id=row['id'],
                    timeout=75).encode()).hexdigest() for row in inventory}


def bind_profile(home, observation, endpoint):
    # Use the credential with which this specific restored native endpoint has
    # already answered authenticated reads. Keep it out of turn/export receipts.
    auth = observation['native_auth']
    expected = Path(observation['native_root']) / 'native-auth.private.json'
    if Path(auth['path']) != expected or expected.stat().st_mode & 0o077:
        raise GatewayLifecycleError('Cognee local observation credential is not private')
    data = read(auth)
    if data['endpoint'] != endpoint or not isinstance(data['api_key'], str) or not data['api_key'].strip():
        raise GatewayLifecycleError('Cognee credential does not bind this native endpoint')
    atomic(home / 'cognee.json', {'service_url': endpoint, 'dataset': observation['dataset'],
                                  'transport': 'http', 'embedded': False, 'recall_mode': 'pinned',
                                  'api_key': data['api_key']})


def verify_turn(observation, turn, drain):
    root = Path(observation['native_root'])
    prompt_sha256 = hashlib.sha256(turn.prompt.encode()).hexdigest()
    sessions = [p for s in drain['sessions'] if s['session_id'] == turn.session_id
                and any(dispatch['prompt_sha256'] == prompt_sha256 for dispatch in s.get('sync_dispatches', []))
                for p in s['providers'] if p['name'] == 'cognee']
    matches = [p for p in sessions if p['endpoint'] == observation['endpoint'] and p['dataset'] == observation['dataset']]
    if len(matches) != 1:
        raise GatewayLifecycleError('Cognee native observation endpoint or dataset differs')
    session = matches[0]
    deadline = time.monotonic() + 180
    wants_improve = session.get('close_requested_improve')
    prior_ids = set(observation['prior_pipeline_ids'])
    rows = pipeline_rows(root)
    while wants_improve is not False and not completed(rows, prior_ids):
        if time.monotonic() >= deadline:
            raise GatewayLifecycleError('Cognee native session promotion did not reach completion')
        time.sleep(.5)
        rows = pipeline_rows(root)
    require_idle(rows)
    if any(r.get('outcome') not in {None, 'succeeded'} for r in rows if r['id'] not in prior_ids):
        raise GatewayLifecycleError('Cognee submitted native work did not succeed')
    backend, dataset_id = native(observation['endpoint'], observation['dataset'], root / 'hermes-home')
    try:
        actual = documents(backend, dataset_id)
        if any(actual.get(k) != v for k, v in observation['source_documents'].items()):
            raise GatewayLifecycleError('Cognee observation write changed a sealed source document')
        captured = [k for k in actual if k not in observation['source_documents'] and
                    turn.prompt in backend.read_raw_data(dataset_id=dataset_id, data_id=k, timeout=15)]
        if captured:
            return {'native_promoted_documents': captured, 'source_readback': 'unchanged'}
        if wants_improve is not False or not session.get('native_session'):
            raise GatewayLifecycleError('Cognee native promoted user question was not readable')
        cache = backend.get_session(session_id=session['native_session'], timeout=20) or {}
        entries = [q['qa_id'] for q in cache.get('qas', []) if q.get('question') == turn.prompt and q.get('answer')]
        if not entries:
            raise GatewayLifecycleError('Cognee close skipped promotion and its native Q&A write was not readable')
        return {'native_session_qa_ids': entries, 'source_readback': 'unchanged',
                'promotion': 'stock close did not request improve'}
    finally:
        backend.close(timeout=5)
