"""Entry point for a fresh stock first-release ingestion qualification only."""
import argparse
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import time
from urllib.request import Request

from . import workflow_capability as cap
from .canary_capability import binding, read, sha
from .gateway.transport import open_without_redirect
from .gateway_client import client_settings
from .gateway_reuse import atomic
from .live_dispatch import CONDITION, process_identity
from .observational_receipts import COHORT_STOP, ENV as JOURNAL
from .openrouter_direct import direct_environment
from .openrouter_route import MODEL, PROFILE_NAME
from .openrouter_run import fetch_catalog
from .supermemory_source_admission import BINARY_SHA, PROFILE, PROVIDER, SCOPE, project_name, source_release
from .supermemory_source_effect import BOUNDARY, NATIVE_ID
from .supermemory_week_observation import verify_release_inventory, wait_for_batch_processing
from .workflow_delivery import submit_document
from .workflow_memcal_live import run_environment
from .workflow_receipts import receipt_summary
from .workflow_spec import SPEC


@contextmanager
def account_lock(campaign):
    lock = campaign/'execution.lock'
    with os.fdopen(os.open(lock, os.O_RDWR | os.O_NOFOLLOW), 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield stream.fileno()


def read_account(endpoint, key):
    request = Request('https://openrouter.ai/api/v1/'+endpoint, headers={'Authorization':'Bearer '+key})
    with open_without_redirect(request, timeout=15) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('OpenRouter account response too large')
    return raw, json.loads(raw, parse_float=Decimal)['data']


def verify_prepaid(checkpoint, key):
    from .direct_accounting import now

    if not key or hashlib.sha256(key.encode()).hexdigest() != (
            checkpoint/'execution-key-fingerprint.private.txt').read_text().strip():
        raise ValueError('Execution key differs from the approved campaign')
    confirmation = binding(checkpoint/'operator-confirmation.json')
    cap.confirm_setting(read(confirmation))
    initial = json.loads((checkpoint/'key.private.json').read_text(), parse_float=Decimal)['data']
    started = now()
    _, status = read_account('key', key)
    raw, credits = read_account('credits', key)
    identity = ('label','organization_id','workspace_id','creator_user_id','is_management_key',
                'is_provisioning_key','expires_at')
    if any(name not in status or name not in initial or status[name] != initial[name] for name in identity):
        raise ValueError('Authenticated execution account differs from checkpoint')
    names = {'autorecharge', 'autorechargeenabled', 'autotopup'}
    for data in (status, credits):
        if any(field.lower().replace('_','') in names and value is not False and value is not None and value != 'off'
               for field, value in data.items()):
            raise ValueError('Contrary Auto Recharge evidence')
    remaining = Decimal(str(credits['total_credits'])) - Decimal(str(credits['total_usage']))
    limit = Decimal(str(status['limit']))
    room = Decimal(str(status['limit_remaining']))
    if ('limit_reset' not in status or status['limit_reset'] is not None
            or any(not value.is_finite() or value <= 0 for value in (remaining, limit, room)) or room > limit):
        raise ValueError('Positive prepaid credit and finite nonresetting key room required')
    return confirmation, raw, {'request_started':started, 'observed_at':now(), 'remaining_usd':str(remaining),
            'receipt_sha256':hashlib.sha256(raw).hexdigest(), 'source':'OpenRouter GET /api/v1/credits'}


def publish_membership(endpoint, key, root, namespace, accepted):
    """Bind native IDs with the stock namespace listing before a worker can spend."""
    request = Request(endpoint+'/v3/documents/list', headers={'Authorization':'Bearer '+key,
        'Content-Type':'application/json'}, data=json.dumps(
            {'containerTags':[namespace], 'includeContent':False, 'page':1, 'limit':100}).encode())
    with open_without_redirect(request, timeout=5) as response:
        page = json.load(response)
    known = {document.filename:document for document in accepted}
    rows, pagination = page['memories'], page['pagination']
    if (pagination['currentPage'] != 1 or pagination['totalPages'] != 1 or len(rows) != len(known)):
        raise ValueError('Incomplete native Supermemory membership listing')
    seen = set()
    native_ids = set()
    mappings = []
    for row in rows:
        public, native = row.get('customId'), row.get('id')
        if (public not in known or public in seen or row.get('containerTags') != [namespace]
                or not isinstance(native, str) or not NATIVE_ID.fullmatch(native) or native in native_ids):
            raise ValueError('Foreign Supermemory document in membership listing')
        seen.add(public)
        native_ids.add(native)
        doc = known[public]
        value = dict(native_id=native, public_id=public, submitted_sha256=doc.sha256,
                     cutoff=doc.cutoff, namespace=namespace)
        mappings.append(value)
    for value in mappings:
        path = root/'source-membership'/f"{value['native_id']}.json"
        if path.exists():
            if json.loads(path.read_text()) != value:
                raise ValueError('Native Supermemory document binding changed')
        else:
            atomic(path, value)


def check_stops(campaign):
    paths = [campaign/'ACCOUNT_STOP']
    paths += [Path(os.environ[name]) for name in (
        'PACBENCH_DIRECT_STOP_ON_429','PACBENCH_STOP_FILE',COHORT_STOP) if os.environ.get(name)]
    if any(path.exists() for path in paths):
        raise RuntimeError('Applicable OpenRouter stop is active')


def check_runtime():
    from provider_adapters.embedding_health import check

    vendor = Path(__file__).resolve().parents[1]/'vendor'
    binary = vendor/'supermemory-local/supermemory-server-linux-x64'
    if sha(binary) != BINARY_SHA or version('supermemory') != '5.0.1':
        raise ValueError('Expected the pinned stock Supermemory server and SDK')
    pins = json.loads((vendor/'EMBEDDING_PINS.json').read_text())
    with open_without_redirect(Request('http://127.0.0.1:11435/api/tags'), timeout=5) as response:
        tags = json.load(response)['models']
    models = [row for row in tags if row['name'] == pins['nomic']['name']+':latest']
    if len(models) != 1 or models[0]['digest'] != pins['nomic']['manifest_sha256']:
        raise ValueError('Local Supermemory embedding manifest changed')
    result = check(timeout=10)
    if result['state'] != 'pass' or result['dimensions'] != 768:
        raise ValueError('Pinned local embedding health failed')


def run(campaign, checkpoint):
    from supermemory import Supermemory
    from provider_adapters.supermemory_local import start
    from .execution_capability import selected

    campaign = Path(campaign).resolve(strict=True)
    checkpoint = Path(checkpoint).resolve(strict=True)
    one_shot = campaign/'supermemory-source-qualification-v1'
    root = one_shot/'supermemory'
    first, _, basis = source_release()
    namespace = 'supermemory-data-'+one_shot.name+'-supermemory'
    key = os.environ['OPENROUTER_API_KEY']
    if (PROFILE_NAME != PROFILE or selected() is not None
            or any(os.environ.get(s) for s in (JOURNAL, 'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY',
                                               'PACBENCH_R2_LIVE_SCOPE', 'PACBENCH_JUDGE_OUTPUT_RECOVERY'))):
        raise ValueError('A fresh isolated Luna provider process is required')
    with account_lock(campaign) as fd:
        check_stops(campaign)
        if one_shot.exists():
            raise RuntimeError('Preserve the existing source qualifier and do not replay')
        check_runtime()
        fetch_catalog(profile=PROFILE)
        proof, raw, prepaid = verify_prepaid(checkpoint, key)
        one_shot.mkdir(mode=0o700)
        root.mkdir(mode=0o700)
        for d in ('prepaid-verification', 'source-membership', 'hermes-home'):
            (root/d).mkdir(mode=0o700)
        (root/'prepaid-verification'/(prepaid['receipt_sha256']+'.private.json')).write_bytes(raw)
        project = project_name(root)
        scope = dict(root=str(root), run_id=one_shot.name, provider=PROVIDER, admission=SCOPE,
                     projects={PROVIDER:{'memory':project}}, spec_sha256=sha(SPEC), frozen_inputs=basis,
                     controller=process_identity(), lock_fd=fd, cutoff=first[0].cutoff, namespace=namespace,
                     native_effect=BOUNDARY, prepaid=prepaid, auto_recharge_evidence=proof)
        atomic(root/'workflow-scope.json',scope)
        settings = {cap.ENV:json.dumps(binding(root/'workflow-scope.json')), JOURNAL:str(root/'receipts.jsonl'),
                    COHORT_STOP:str(campaign/'ACCOUNT_STOP'), 'PACBENCH_STOP_FILE':str(root/'STOP'),
                    'PACBENCH_DIRECT_STOP_ON_429':str(root/'STOP'),
                    'PACBENCH_DISPATCH_TRACKING':CONDITION, 'PACBENCH_ROLE_ROUTES':'luna-all-roles-v1',
                    'PACBENCH_LUNA_COMPATIBILITY':'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS':'32768', 'PACBENCH_SUPERMEMORY_STOCK_SOURCE':'1'}
        report = {'provider':PROVIDER, 'condition':SCOPE, 'state':'incomplete', 'questions_sent':0}
        with run_environment(settings):
            ledger = cap.budget()
            stop = None
            try:
                with direct_environment(key):
                    env = {}
                    base, marker = client_settings(project)
                    stop = start(root, env, MODEL, base, marker)
                    endpoint = env['SUPERMEMORY_BASE_URL']
                    native_token = env['SUPERMEMORY_API_KEY']
                    with Supermemory(base_url=endpoint, api_key=native_token, max_retries=0) as client:
                        deadline = time.monotonic()+1200
                        verify_release_inventory(client, namespace, (), deadline=deadline)
                        acknowledgements = {}
                        with (root/'acceptances.jsonl').open('x') as journal:
                            for index, doc in enumerate(first, 1):
                                check_stops(campaign)
                                ack = submit_document(PROVIDER, client, namespace, doc)
                                if ack.id != doc.filename or ack.status != 'queued':
                                    raise ValueError('Stock source add did not acknowledge the pending frozen document')
                                acknowledgements[doc.filename] = ack
                                journal.write(json.dumps(dict(id=ack.id, cutoff=doc.cutoff, sha256=doc.sha256))+'\n')
                                journal.flush()
                                os.fsync(journal.fileno())
                                publish_membership(endpoint, native_token, root, namespace, first[:index])
                        report['release'] = wait_for_batch_processing(
                            client, namespace, first, acknowledgements, deadline=deadline,
                            before_read=lambda:check_stops(campaign))
                        # Normal document readiness is independent of dreaming.
                        # An already admitted request must still get its ordinary
                        # response boundary before the owned worker is stopped.
                        while any(not row.get('finished') for row in ledger.snapshot()):
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Supermemory worker did not finish admitted transport')
                            time.sleep(0.25)
            except BaseException as exc:
                report['failure_type'] = type(exc).__name__
                raise
            finally:
                if stop is not None:
                    stop()
                rows = ledger.snapshot()
                report['billing'] = receipt_summary(root, rows, one_shot.name)
                report['cleanup'] = (json.loads((root/'native-service-stop.json').read_text())['state']
                                     if (root/'native-service-stop.json').is_file() else 'unverified')
                if ('release' in report and not report.get('failure_type')
                        and report['cleanup'] == 'verified-inventory-stopped'
                        and all(row.get('dispatch_execution') == 'response_ended'
                                and row.get('http_status') == 200 for row in rows)):
                    report['state'] = 'stock-first-release-processed'
                report['receipted_native_effects'] = sum(row.get('state') == 'settled'
                    and row.get('served_provider') == 'Amazon Bedrock' and row.get('served_model') == MODEL for row in rows)
                report['warm_compatibility'] = 'not exercised'
                atomic(root/'result.json', report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    parser.add_argument('checkpoint', type=Path)
    args = parser.parse_args()
    report = run(args.campaign, args.checkpoint)
    print(json.dumps({key:report[key] for key in ('state','billing','cleanup')}))
    return int(report['state'] != 'stock-first-release-processed')


if __name__ == '__main__':
    raise SystemExit(main())
