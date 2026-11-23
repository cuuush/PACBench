"""Qualify one uncoached Honcho answer after its exact native day-one upload."""
import argparse
from decimal import Decimal
import fcntl
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
from urllib.request import Request

from . import harness, honcho_daily_gate as gate, workflow_capability as capability
from .canary_capability import CanaryBusy, binding, read
from .catalog_plugins import verify_retained_catalog_provider
from .checkpoint_clock import CheckpointClock, atomic
from .context_isolation import validate_recall_environment
from .execution_capability import selected
from .gateway.transport import open_without_redirect
from .gateway_client import client_settings
from .gateway_reuse import PersistentGateway
from .honcho_first_answer_admission import arm, question, settled, TOOLS, verify_receipts
from .live_dispatch import CONDITION
from .observational_receipts import COHORT_STOP, ENV as JOURNAL
from .openrouter_direct import direct_environment
from .openrouter_route import MODEL, PROFILE_NAME
from .openrouter_run import fetch_catalog
from .workflow_memcal_live import run_environment
from .workflow_native import WarmHermes
from .workflow_receipts import receipt_summary

RUN = 'honcho-seven-daily-first-answer-v1'


def read_prepaid_evidence(checkpoint, key):
    """Use the original funded checkpoint and authoritative read-only account endpoints."""
    from .direct_accounting import now

    if not key or sha256(key.encode()).hexdigest() != (checkpoint/'execution-key-fingerprint.private.txt').read_text().strip():
        raise ValueError('Honcho execution key differs from the approved campaign')
    confirmation = binding(checkpoint/'operator-confirmation.json')
    capability.confirm_setting(read(confirmation))
    initial = json.loads((checkpoint/'key.private.json').read_text(),parse_float=Decimal)['data']
    observed = {}
    started = now()
    for endpoint in ('key','credits'):
        request = Request('https://openrouter.ai/api/v1/'+endpoint,headers={'Authorization':'Bearer '+key})
        with open_without_redirect(request,timeout=15) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError('OpenRouter account response too large')
        observed[endpoint] = json.loads(raw,parse_float=Decimal)['data']
    status, credits = observed['key'],observed['credits']
    identity = ('label','organization_id','workspace_id','creator_user_id','is_management_key',
                'is_provisioning_key','expires_at')
    if any(name not in status or name not in initial or status[name] != initial[name] for name in identity):
        raise ValueError('Authenticated Honcho execution account differs from the checkpoint')
    fields = {'autorecharge','autorechargeenabled','autotopup'}
    if any(name.lower().replace('_','') in fields and value is not None and value is not False and value != 'off'
           for data in observed.values() for name,value in data.items()):
        raise ValueError('Contrary Auto Recharge evidence')
    balance = Decimal(str(credits['total_credits'])) - Decimal(str(credits['total_usage']))
    limit, room = (Decimal(str(status[name])) for name in ('limit','limit_remaining'))
    if ('limit_reset' not in status or status['limit_reset'] is not None
            or not all(value.is_finite() and value > 0 for value in (balance,limit,room)) or room > limit):
        raise ValueError('Positive prepaid credit and finite nonresetting key room required')
    return confirmation, raw, dict(source='OpenRouter GET /api/v1/credits',remaining_usd=str(balance),
                                   request_started=started,observed_at=now(),receipt_sha256=sha256(raw).hexdigest())


def check_stops(campaign):
    stops = [campaign/'ACCOUNT_STOP']
    for name in (COHORT_STOP,'PACBENCH_STOP_FILE','PACBENCH_DIRECT_STOP_ON_429'):
        if os.environ.get(name):
            stops.append(Path(os.environ[name]))
    if any(path.exists() for path in stops):
        raise RuntimeError('An applicable execution or account stop is active')


def upload(root, scope, env):
    import httpx
    from honcho import Honcho
    from honcho.api_types import SessionPeerConfig
    from .workflow_spec import instant

    native = gate.runtime(root)
    host = native['host']
    at = scope['plan'][0]['at']
    with httpx.Client() as transport:
        client = Honcho(environment='local',base_url=env['HONCHO_BASE_URL'],
                        workspace_id=host['workspace'],max_retries=0,http_client=transport)
        session = client.session(scope['session'])
        owner, assistant = host['peerName'],host['aiPeer']
        peers = [(owner,SessionPeerConfig(observe_me=True,observe_others=True)),
                 (assistant,SessionPeerConfig(observe_me=True,observe_others=True))]
        session.add_peers(peers)
        filename, source = gate.begin(root,at)
        ack = session.upload_file((filename,source,'text/plain'),owner,
                                  metadata={'filename':filename},created_at=instant(at))
        gate.seal(root,at,session,ack)
    deadline = time.monotonic()+150
    while True:
        check_stops(root.parent.parent)
        try:
            return gate.finish(root,at)
        except CanaryBusy:
            if time.monotonic() >= deadline:
                raise TimeoutError('Honcho stock first-release representation or embedding remains pending') from None
            time.sleep(.3)


def ask(root, scope, env, marker, ledger, result):
    home, turn = root/'hermes-home',question(scope)
    child = harness.turn_env(scope['plan'][0]['at'],home,','.join(TOOLS),{
        **env,'PACBENCH_WORKFLOW_CAPTURE':scope['protocol'],'PACBENCH_PROVIDER_ONLY':'1',
        'PACBENCH_API_INPUT_TRACE':str(root/'api-inputs.jsonl'),'HERMES_ENABLE_PROJECT_PLUGINS':'0',
        'OPENROUTER_API_KEY':marker,'TZ':'America/New_York',
    })
    clock = CheckpointClock(home,turn.at,scope['spec_sha256'])
    with TemporaryDirectory(prefix='pacbench-honcho-question-') as cwd:
        with PersistentGateway(home,harness.ISOLATED_BIN.parent/'python',child,cwd=cwd,
                               checkpoint=scope['spec_sha256'],observation_clock=clock,provider='honcho') as owner:
            arm(scope,owner,turn)

            def idle_start():
                observed = owner.fence()
                if observed['sessions'] or any(not row.get('finished') for row in ledger.snapshot()):
                    raise RuntimeError('Honcho opening requires an empty gateway and completed source requests')
                return dict(gateway_identity=[owner.witness['pid'],owner.witness['start_time']],
                            pending_native_writes=0,inflight_requests=0,native_errors=[],readback=observed)

            frontend = WarmHermes(owner,clock,idle_start)
            surface = frontend.ask(turn.prompt,at=turn.at,session_id=None,
                                   on_return=lambda raw:atomic(root/'native-return.private.json',raw))
            atomic(root/'answer-visible.private.json',surface)
            result.update(answers_received=1,model_requests=len(surface['model_requests']),
                          tool_attempts=len(surface['tool_calls']),automatic_recall=surface['memory_delivery']['state'],
                          answer_latency_seconds=surface['timing']['cli_result_received_s'])
            frontend.end_chat(surface['session_id'])
            closed = owner.fence(surface['session_id'])
            atomic(root/'native-chat-close.json',closed)


def cleanup_verified(root):
    from provider_adapters.honcho_checkpoint import DOCKER
    from .live_dispatch import process_identity

    native = json.loads((root/gate.RUNTIME).read_text())
    for role in ('api','worker'):
        expected = native[role]
        try:
            if process_identity(expected['pid']) == expected:
                return False
        except (FileNotFoundError,ProcessLookupError):
            pass
    container = json.loads((root/'native-container-ownership.json').read_text())['container_id']
    deadline = time.monotonic()+10
    while True:
        listed = subprocess.check_output([*DOCKER,'ps','-a','--no-trunc','--filter','id='+container,
                                          '--format','{{.ID}}'],text=True,timeout=5)
        if container not in listed.splitlines():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.25)


def run(campaign, checkpoint, plugin_home):
    from provider_adapters.honcho_local import start

    check_stops(campaign)
    if (PROFILE_NAME != capability.PROFILE or version('honcho-ai') != '2.9.0' or selected() is not None
            or any(os.environ.get(name) for name in (JOURNAL,'PACBENCH_DIRECT','PACBENCH_DIRECT_UPSTREAM_KEY',
                   'PACBENCH_OFFLINE_CONFORMANCE','PACBENCH_JUDGE_OUTPUT_RECOVERY'))):
        raise ValueError('Honcho needs an isolated, pinned, ordinary Luna runner')
    validate_recall_environment()
    verify_retained_catalog_provider('honcho',plugin_home)
    root = campaign/RUN/'honcho'
    key = os.environ['OPENROUTER_API_KEY']
    with os.fdopen(os.open(campaign/'execution.lock',os.O_RDWR|os.O_NOFOLLOW),'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        check_stops(campaign)
        if root.parent.exists():
            raise RuntimeError('Retain the existing Honcho first-answer cohort without replay')
        fetch_catalog(profile=capability.PROFILE)
        confirmation, raw, prepaid = read_prepaid_evidence(checkpoint,key)
        started = time.monotonic()
        root.mkdir(mode=0o700,parents=True)
        for name in ('fallback-home','prepaid-verification'):
            (root/name).mkdir(mode=0o700)
        balance = root/'prepaid-verification'/(prepaid['receipt_sha256']+'.private.json')
        with os.fdopen(os.open(balance,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600),'wb') as file:
            file.write(raw)
        manifest = gate.materialize_canonical_files(root/'frozen-documents')
        bound = gate.create(root,execution_lock=lock,session_id='pizza-week-source',canonical_manifest=manifest['path'],
                            manifest_sha256=manifest['sha256'],prepaid=prepaid,auto_recharge_evidence=confirmation,
                            layout=gate.CANONICAL_LAYOUT,first_question=True)
        settings = {capability.ENV:json.dumps(bound),JOURNAL:str(root/'receipts.jsonl'),COHORT_STOP:str(campaign/'ACCOUNT_STOP'),
                    'PACBENCH_STOP_FILE':str(root/'STOP'),'PACBENCH_DIRECT_STOP_ON_429':str(root/'STOP'),
                    'PACBENCH_ROLE_ROUTES':'luna-all-roles-v1','PACBENCH_LUNA_COMPATIBILITY':'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS':'32768','PACBENCH_DISPATCH_TRACKING':CONDITION,
                    'PACBENCH_MEMORY_API_INPUT_TRACE_ROOT':str(root/'memory-api-inputs'),'PACBENCH_IMPORT_ROOT':str(root)}
        result = dict(state='incomplete',question='rsvp-before',documents_verified=0,answers_received=0)
        with run_environment(settings):
            ledger = capability.budget()
            scope = capability.load()
            try:
                with direct_environment(key):
                    check_stops(campaign)
                    home = root/'hermes-home'
                    roles = scope['projects']['honcho']
                    base, marker = client_settings(roles['driver'])
                    harness.provision_home(home,provider_plugin_src=plugin_home/'plugins/honcho',provider_name='honcho',
                                           model=MODEL,model_provider='openrouter',model_base_url=base)
                    shutil.copyfile(plugin_home/'plugins/.install-metadata.json',home/'plugins/.install-metadata.json')
                    verify_retained_catalog_provider('honcho',home)
                    env = {'HOME':str(root/'fallback-home')}
                    _, memory = client_settings(roles['memory'])
                    stop = start(root,env,MODEL,base,memory,daily_documents=True)
                    try:
                        result['source_ready'] = upload(root,scope,env)['sha256']
                        result['documents_verified'] = 1
                        result['source_records'] = len(scope['plan'][0]['source_ids'])
                        ask(root,scope,env,marker,ledger,result)
                    finally:
                        stop()
                        result['cleanup'] = 'owned-services-stopped' if cleanup_verified(root) else 'unverified'
                check_stops(campaign)
                rows = ledger.snapshot()
                verify_receipts({row['id']:row for row in rows})
                if result['cleanup'] != 'owned-services-stopped' or not rows or not all(map(settled,rows)):
                    raise RuntimeError('Honcho qualification lacks cleanup or settled receipt evidence')
                result['state'] = 'stock-first-answer-qualified'
            except BaseException as exc:
                result['failure_type'] = type(exc).__name__
                raise
            finally:
                rows = ledger.snapshot()
                result['billing'] = receipt_summary(root,rows,RUN)
                result['elapsed_seconds'] = time.monotonic()-started
                atomic(root/'result.json',result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('campaign','checkpoint','plugin_home'):
        parser.add_argument(name,type=Path)
    options = parser.parse_args()
    os.umask(0o077)
    result = run(*(getattr(options,name).resolve(strict=True) for name in ('campaign','checkpoint','plugin_home')))
    print(json.dumps(result,sort_keys=True))


if __name__ == '__main__':
    main()
