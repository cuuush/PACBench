"""Verify native Cognee observation credentials and turn persistence."""
import hashlib
from pathlib import Path
import sys
import time

from pacbench.judging.retained_scope import read_canary_binding as read
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


def verified_promotions(backend, dataset_id, sealed, native_session, history):
    # Stock promotions consist of a contiguous QA window. Only suffixes ending
    # in this exact turn are evidence of its promotion at close.
    header = f'Session ID: {native_session}\n\n'
    entries = [f"Question: {q['prompt']}\n\nAnswer: {q['answer']}\n\n" for q in history]
    hashes = {hashlib.sha256((header + ''.join(entries[start:])).encode()).hexdigest()
              for start in range(len(entries))}
    listed = set()
    captured = []
    for offset in range(0, 100001, 100):
        page = backend.list_dataset_data(dataset_id=dataset_id, timeout=75, limit=100, offset=offset)
        if not isinstance(page, list) or len(page) > 100:
            raise GatewayLifecycleError('Invalid native document page')
        for row in page:
            identifier = row.get('id')
            if not isinstance(identifier, str) or not identifier or identifier in listed:
                raise GatewayLifecycleError('Duplicate or invalid Cognee document ID')
            listed.add(identifier)
            raw = backend.read_raw_data(dataset_id=dataset_id, data_id=identifier, timeout=75)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            if identifier in sealed:
                if digest != sealed[identifier]:
                    raise GatewayLifecycleError('Cognee observation write changed a sealed source document')
            elif digest in hashes:
                captured.append(identifier)
        if len(page) < 100:
            break
    else:
        raise GatewayLifecycleError('Cognee checkpoint inventory exceeds bound')
    if not listed.issuperset(sealed) or len(captured) > 1:
        raise GatewayLifecycleError('Cognee checkpoint missing a sealed source or promotion is ambiguous')
    return captured


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
        native_session = session.get('native_session')
        if not native_session or not turn.answer:
            raise GatewayLifecycleError('Missing Cognee native session or exact answer')
        history = observation.get('verified_qa_history') or [
            {'prompt':turn.prompt, 'answer':turn.answer, 'native_session':native_session}]
        if (any(row['native_session'] != native_session for row in history)
                or history[-1]['prompt'] != turn.prompt or history[-1]['answer'] != turn.answer):
            raise GatewayLifecycleError('Cognee history differs from the selected native turn')
        if sum(row['prompt'] == turn.prompt for row in history) > 1 and not history[-1].get('qa_id'):
            raise GatewayLifecycleError('Repeated Cognee prompt needs a native QA identity')
        dispatches = [dispatch for row in drain['sessions'] if row['session_id'] == turn.session_id
                      for dispatch in row.get('sync_dispatches', [])
                      if dispatch['prompt_sha256'] == prompt_sha256]
        if len(dispatches) != 1 and not history[-1].get('qa_id'):
            raise GatewayLifecycleError('Ambiguous Cognee dispatch for this prompt')
        captured = verified_promotions(backend, dataset_id, observation['source_documents'], native_session, history)
        if captured:
            return {'native_promoted_documents': captured, 'source_readback': 'unchanged'}
        if wants_improve is not False or not session.get('native_session'):
            raise GatewayLifecycleError('Cognee native promoted user question was not readable')
        cache = backend.get_session(session_id=session['native_session'], timeout=20) or {}
        entries = [q['qa_id'] for q in cache.get('qas', []) if q.get('question') == turn.prompt
                   and q.get('answer') == turn.answer and q.get('qa_id')
                   and q['qa_id'] == history[-1].get('qa_id', q['qa_id'])]
        if len(entries) != 1:
            raise GatewayLifecycleError('Cognee close skipped promotion and its native Q&A write was not readable')
        return {'native_session_qa_ids': entries, 'source_readback': 'unchanged',
                'promotion': 'stock close did not request improve'}
    finally:
        backend.close(timeout=5)
