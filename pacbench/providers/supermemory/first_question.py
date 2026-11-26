"""Qualify one uncoached stock question on the verified native source snapshot."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import time
from urllib.parse import quote
from urllib.request import Request

from pacbench import harness
from pacbench.workflows import capability as cap
from ...canary_capability import binding, sha
from ...catalog_plugins import verify_retained_catalog_provider
from ...checkpoint_clock import CheckpointClock, atomic
from ...context_isolation import validate_recall_environment
from ...execution_capability import selected
from ...gateway.transport import open_without_redirect
from ...gateway_client import client_settings
from ...gateway_reuse import PersistentGateway
from ...live_dispatch import CONDITION, process_identity
from ...observational_receipts import COHORT_STOP, ENV as JOURNAL
from ...openrouter_direct import direct_environment
from ...openrouter_route import ENDPOINT, MODEL, PROFILE_NAME
from ...openrouter_run import fetch_catalog
from ...providers import provider_module
from .question_admission import PROFILE, PROTOCOL, PROVIDER, QUESTION_RUN, SCOPE, SOURCE_RUN, compare_source, original_source, projects, snapshot_identity, terminal_response
from .source_admission import source_release
from .source_effect import BOUNDARY
from .source_qualify import account_lock, check_runtime, check_stops, publish_membership, verify_prepaid
from .observation import PENDING, read_release_processing, verify_native_version
from .. import run_environment
from pacbench.workflows.native import WarmHermes
from pacbench.workflows.recording import receipt_summary
from pacbench.workflows.runner import Condition, captured_turn, inspect_source_cutoff
from pacbench.campaigns.pizza_week import SPEC
from pacbench.workflows.tracks import HERMES_TOOLS


def read_source(client, env, root, scope, original, *, capture=()):
    first, _, _ = source_release()
    endpoint, token = env['SUPERMEMORY_BASE_URL'], env['SUPERMEMORY_API_KEY']
    namespace = scope['namespace']
    def native_readback(doc, wire):
        native = scope['source']['native_ids'][doc.filename]
        request = Request(endpoint + '/v3/documents/' + quote(native, safe=''),
                          headers={'Authorization': 'Bearer ' + token})
        with open_without_redirect(request, timeout=5) as response:
            return verify_native_version(json.load(response), doc, native_id=native, namespace=namespace, wire=wire)

    if not capture:
        publish_membership(endpoint, token, root, namespace, first)
        seen = [json.loads(path.read_text()) for path in (root / 'source-membership').glob('*.json')]
        if {row['public_id']: row['native_id'] for row in seen} != scope['source']['native_ids']:
            raise ValueError('Reopened Supermemory namespace has different accepted IDs')
    proof = read_release_processing(client, namespace, first, deadline=time.monotonic() + 20,
                                    other_ids=capture, native_readback=native_readback,
                                    before_read=lambda: check_stops(root.parent.parent))
    compare_source(original, proof)
    return proof


def conversation_capture(client, namespace, surface, turn):
    from supermemory import NotFoundError

    plugin = provider_module(PROVIDER)
    sid = surface['session_id']
    ident = plugin._capture_doc_id(sid)
    expected = plugin._format_turn(plugin._clean_text_for_capture(turn.prompt),
                                   plugin._clean_text_for_capture(surface['answer']))
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            row = client.documents.get(namespace, ident, request_options={'max_retries': 0, 'timeout': 5})
        except NotFoundError:
            time.sleep(.25)
            continue
        value = row.model_dump(mode='json', by_alias=True)
        system, meta = value['system'], value.get('metadata') or {}
        if value['id'] != ident or system.get('status') not in PENDING | {'done'}:
            raise ValueError('Stock Supermemory conversation processing failed')
        if system.get('status') == 'done':
            if ((meta.get('session_id'), meta.get('type'), meta.get('docId')) != (sid, 'conversation', ident)
                    or value.get('content') != expected):
                raise ValueError('Stock Supermemory conversation capture differs from the exact answered turn')
            return dict(id=ident, status='done', dreaming_status=system.get('dreamingStatus'),
                        capture_sha256=cap.digest(expected))
        time.sleep(.25)
    raise TimeoutError('No completed stock Supermemory conversation capture')


def ask(root, scope, env, client, original, turn, driver_marker, ledger, result):
    home = root / 'hermes-home'
    tools = HERMES_TOOLS[PROVIDER]
    child = harness.turn_env(scope['cutoff'], home, ','.join(tools), {
        **env, 'PACBENCH_WORKFLOW_CAPTURE': PROTOCOL,
        'PACBENCH_API_INPUT_TRACE': str(root / 'api-inputs.jsonl'), 'PACBENCH_PROVIDER_ONLY': '1',
        'HERMES_ENABLE_PROJECT_PLUGINS': '0', 'OPENROUTER_API_KEY': driver_marker, 'TZ': 'America/New_York',
    })
    clock = CheckpointClock(home, turn.at, scope['spec_sha256'])
    with TemporaryDirectory(prefix='pacbench-supermemory-first-chat-') as cwd:
        with PersistentGateway(home, harness.ISOLATED_BIN.parent / 'python', child, cwd=cwd,
                               checkpoint=scope['spec_sha256'], observation_clock=clock, provider=PROVIDER) as owner:
            w = owner.witness
            atomic(root / 'question-gateway.json', dict(process=process_identity(w['pid']), controller=scope['controller'],
                   witness={key: w[key] for key in ('pid', 'start_time', 'process_adoption')}))

            def idle_start():
                sample = owner.fence()
                if sample['sessions'] or any(not row.get('finished') for row in ledger.snapshot()):
                    raise ValueError('Supermemory opening requires a clean, fresh Hermes session')
                return dict(gateway_identity=[w['pid'], w['start_time']], pending_native_writes=0,
                            inflight_requests=0, native_errors=[], readback=sample)

            check_stops(root.parent.parent)
            frontend = WarmHermes(owner, clock, idle_start)
            surface = frontend.ask(turn.prompt, at=turn.at, session_id=None,
                                   on_return=lambda raw: atomic(root / 'native-return.private.json', raw))
            atomic(root / 'answer-visible.private.json', surface)
            result['answers_received'] = 1
            result['answer_latency_seconds'] = surface['timing']['cli_result_received_s']
            result['tool_attempts'] = len(surface['tool_calls'])
            result['automatic_recall'] = surface['memory_delivery']['state']
            check_stops(root.parent.parent)
            frontend.end_chat(surface['session_id'])
            owner.fence(surface['session_id'])
            with run_environment({'HERMES_HOME': str(home)}):
                capture = conversation_capture(client, scope['namespace'], surface, turn)
            read_source(client, env, root, scope, original, capture=(capture['id'],))
            sources = [doc.filename for doc in source_release()[0]]
            attempts = {row['id']: row for row in ledger.snapshot()}
            surface.update(source_files=sources, write_barrier=dict(session_id=surface['session_id'], pending_writes=0,
                            readback=capture), attempts=[dict(id=req['request_id'], parent=None,
                            operation=attempts[req['request_id']]['dispatch_context']['operation_id'], origin='unknown')
                            for req in surface['model_requests']])
            condition = Condition(PROVIDER, 'nightly-document', 'continuing-account', 'Hermes ' + owner.pins['hermes'],
                                  MODEL, ENDPOINT, SCOPE, 'server-v0.0.9 / SDK 5.0.1', owner.pins['hermes_isolation_sha256'],
                                  'Physical provider clock, authored Hermes question clock', tools)
            captured_turn(surface, turn, None, condition, sources)
            inspect_source_cutoff(surface, cap.schedule(sha(SPEC), False)[0], sources, [turn.prompt])
            atomic(root / 'verified-turn.private.json', surface)
            result.update(question=turn.id, qualified_capture=True, readback_documents=len(sources),
                          answer_visible_requests=len(surface['model_requests']))


def run(campaign, checkpoint, approved_plugin_home):
    from supermemory import Supermemory
    from provider_adapters.supermemory_local import start

    campaign, checkpoint, approved_plugin_home = [Path(p).resolve(strict=True) for p in
                                                   (campaign, checkpoint, approved_plugin_home)]
    root = campaign / QUESTION_RUN / PROVIDER
    start_time = time.monotonic()
    key = os.environ['OPENROUTER_API_KEY']
    if (PROFILE_NAME != PROFILE or selected() is not None or any(os.environ.get(name) for name in
            (JOURNAL, 'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_JUDGE_OUTPUT_RECOVERY'))):
        raise ValueError('The Supermemory question requires an isolated Luna runner')
    with account_lock(campaign) as lock:
        check_stops(campaign)
        if root.parent.exists():
            raise RuntimeError('Preserve the previous first-question attempt and do not replay')
        source, proof, turn = original_source(campaign)
        snapshot = campaign / SOURCE_RUN / PROVIDER / 'supermemory-data'
        source_identity = snapshot_identity(snapshot)
        validate_recall_environment()
        verify_retained_catalog_provider(PROVIDER, approved_plugin_home)
        check_runtime()
        fetch_catalog(profile=PROFILE)
        confirmation, credit, prepaid = verify_prepaid(checkpoint, key)
        root.parent.mkdir(mode=0o700)
        root.mkdir(mode=0o700)
        for name in ('prepaid-verification', 'source-membership', 'fallback-home'):
            (root / name).mkdir(mode=0o700)
        credit_path = root / 'prepaid-verification' / (prepaid['receipt_sha256'] + '.private.json')
        with os.fdopen(os.open(credit_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
            stream.write(credit)
        shutil.copytree(snapshot, root / 'supermemory-data')
        scope = dict(admission=SCOPE, protocol=PROTOCOL, root=str(root), provider=PROVIDER, run_id=QUESTION_RUN,
                     namespace=proof['namespace'], cutoff=proof['cutoff'], source=source, copied_snapshot=source_identity,
                     native_effect=BOUNDARY, prepaid=prepaid, auto_recharge_evidence=confirmation, controller=process_identity(),
                     lock_fd=lock, projects=projects(root), spec_sha256=sha(SPEC), frozen_inputs=cap.schedule(sha(SPEC), False)[2])
        atomic(root / 'workflow-scope.json', scope)
        settings = {cap.ENV: json.dumps(binding(root / 'workflow-scope.json')), JOURNAL: str(root / 'receipts.jsonl'),
                    COHORT_STOP: str(campaign / 'ACCOUNT_STOP'), 'PACBENCH_STOP_FILE': str(root / 'STOP'),
                    'PACBENCH_DIRECT_STOP_ON_429': str(root / 'STOP'), 'PACBENCH_DISPATCH_TRACKING': CONDITION,
                    'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1', 'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768', 'PACBENCH_SUPERMEMORY_STOCK_SOURCE': '1',
                    'PACBENCH_MEMORY_API_INPUT_TRACE_ROOT': str(root / 'memory-api-inputs'), 'PACBENCH_IMPORT_ROOT': str(root)}
        result = dict(state='incomplete', condition=SCOPE, question=turn.id, answers_received=0, cleanup='unverified')
        with run_environment(settings):
            ledger = cap.budget()
            try:
                with direct_environment(key):
                    home = root / 'hermes-home'
                    base, marker = client_settings(scope['projects'][PROVIDER]['driver'])
                    harness.provision_home(home, provider_plugin_src=approved_plugin_home / 'plugins' / PROVIDER,
                                           provider_name=PROVIDER, model=MODEL, model_provider='openrouter', model_base_url=base)
                    shutil.copyfile(approved_plugin_home / 'plugins' / '.install-metadata.json',
                                    home / 'plugins' / '.install-metadata.json')
                    verify_retained_catalog_provider(PROVIDER, home)
                    env = {'HOME': str(root / 'fallback-home')}
                    base, memory_marker = client_settings(scope['projects'][PROVIDER]['memory'])
                    stop = start(root, env, MODEL, base, memory_marker, resume=True)
                    try:
                        with closing(Supermemory(base_url=env['SUPERMEMORY_BASE_URL'], api_key=env['SUPERMEMORY_API_KEY'],
                                                 max_retries=0)) as client:
                            resumed = read_source(client, env, root, scope, proof)
                            setup = json.loads((root / 'native-server-setup.json').read_text())
                            atomic(root / 'question-ready-source.json', dict(source=source, process=setup['process_identity'],
                                                                           proof=resumed))
                            result['reopened_source_documents'] = len(resumed['documents'])
                            ask(root, scope, env, client, proof, turn, marker, ledger, result)
                    finally:
                        stop()
            except BaseException as exc:
                result['failure_type'] = type(exc).__name__
                raise
            finally:
                rows = ledger.snapshot()
                result['elapsed_seconds'] = time.monotonic() - start_time
                result['billing'] = receipt_summary(root, rows, QUESTION_RUN)
                result['transport'] = dict(
                    http_body_complete=sum(row.get('response_body_complete') is True for row in rows),
                    sse_done_observed=sum((row.get('response_validation') or {}).get('sse_done_observed') is True for row in rows),
                    terminal_verified=sum(terminal_response(row) for row in rows if row.get('finished')),
                )
                shutdown = root / 'native-service-stop.json'
                if shutdown.is_file():
                    result['cleanup'] = json.loads(shutdown.read_text())['state']
                billing = result['billing']
                if (result.get('qualified_capture') and 'failure_type' not in result and result['cleanup'] == 'verified-inventory-stopped'
                        and billing['attempts'] == billing['generations'] and billing['complete_cost_usd'] is not None
                        and result['transport']['terminal_verified'] == billing['attempts']
                        and all(row['served_model'] == MODEL and row['served_provider'] == 'Amazon Bedrock'
                                for row in rows)):
                    result['state'] = 'stock-first-question-qualified'
                atomic(root / 'result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('checkpoint', type=Path)
    parser.add_argument('approved_plugin_home', type=Path)
    args = parser.parse_args()
    result = run(args.campaign, args.checkpoint, args.approved_plugin_home)
    print(json.dumps({'state': result['state'], 'answers_received': result['answers_received'],
                      'settled_usd': result['billing']['complete_cost_usd'], 'cleanup': result['cleanup']}))
    return int(result['state'] != 'stock-first-question-qualified')


if __name__ == '__main__':
    raise SystemExit(main())
