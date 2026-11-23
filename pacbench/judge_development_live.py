"""Run the single frozen Luna A/B batch through PACBench's prepaid judge transport."""
import argparse
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request

from . import judge_development as plan
from .canary_capability import binding, read, sha
from .live_dispatch import CONDITION, process_identity
from .observational_receipts import COHORT_STOP, ENV as JOURNAL, RunReceiptJournal
from .workflow_receipts import receipt_summary

SCOPE = 'luna-judge-development-ab-v1'
# Preserve the zero-dispatch launch in its original single-use directory.
NAME = 'luna-judge-development-v1-lock-fix'
PROJECT = 'pacbench-luna-development-ab-judge'
REVISION = '73fcf99f557b20effd6cdb5f54e28e1964ddafe8'
MANIFEST_SHA = 'db5b743ca618f2aa053de021a714e1659eee81498a3d64b6610ee024b57880d0'
PROFILE = 'luna-bedrock-global'
CAMPAIGN = Path('/workspace/scratch/pizza-v2-live-20261121')
CHECKPOINT = CAMPAIGN.parent/'pizza-v2-handoff/billing-resume'


def frozen_requests():
    if sha(plan.MANIFEST) != MANIFEST_SHA:
        raise ValueError('Reviewed A/B request manifest differs')
    cells = plan.requests()
    expected = json.loads(plan.MANIFEST.read_text())['requests']
    identities = [{'id': cell['id'], 'body_sha256': cell['body_sha256']} for cell in cells]
    if len(cells) != 20 or len({cell['body_sha256'] for cell in cells}) != 20 or identities != expected:
        raise ValueError('Frozen A/B request order or body differs')
    return cells


def check_checkout():
    repo = Path(__file__).resolve().parents[1]
    subprocess.run(['git', 'merge-base', '--is-ancestor', REVISION, 'HEAD'], cwd=repo, check=True)
    for path in (Path(plan.__file__), plan.MANIFEST, plan.FIXTURE):
        relative = path.relative_to(repo).as_posix()
        if subprocess.check_output(['git', 'show', f'{REVISION}:{relative}'], cwd=repo) != path.read_bytes():
            raise ValueError('A/B inputs or renderer differ from reviewed commit')
    frozen_requests()


def check_lock(fd):
    from .workflow_capability import _has_flock

    if type(fd) is int and fd >= 0:
        try:
            target = (CAMPAIGN/'execution.lock').stat()
            if os.path.samestat(os.fstat(fd), target) and _has_flock(os.getpid(), fd):
                return
        except OSError:
            pass  # A closed descriptor or missing lock file cannot establish ownership.
    raise RuntimeError('A/B requires the shared execution lock')


@contextmanager
def account_lock():
    with os.fdopen(os.open(CAMPAIGN/'execution.lock', os.O_RDWR | os.O_NOFOLLOW), 'r+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        check_lock(stream.fileno())
        yield stream.fileno()


def check_stops(root):
    for var in ('PACBENCH_STOP_FILE', 'PACBENCH_DIRECT_STOP_ON_429', COHORT_STOP):
        value = os.environ.get(var)
        if value and Path(value).exists():
            raise RuntimeError('Applicable OpenRouter stop is active')
    if any(path.exists() for path in (CAMPAIGN/'ACCOUNT_STOP', root/'STOP')):
        raise RuntimeError('Applicable OpenRouter stop is active')


def validate(scope, bound):
    from .workflow_capability import confirm_setting
    root = CAMPAIGN/NAME
    if (scope.get('admission') != SCOPE or scope.get('root') != str(root)
            or root != root.resolve() or scope.get('provider') != NAME
            or scope.get('run_id') != CAMPAIGN.name
            or scope.get('reviewed_commit') != REVISION or scope.get('manifest_sha256') != MANIFEST_SHA
            or Path(bound['path']) != root/'workflow-scope.json'
            or scope.get('projects') != {NAME: {'judge': PROJECT}}
            or scope.get('controller') != process_identity()
            or os.environ.get(COHORT_STOP) != str(CAMPAIGN/'ACCOUNT_STOP')
            or os.environ.get('PACBENCH_STOP_FILE') != str(root/'STOP')
            or os.environ.get('PACBENCH_DISPATCH_TRACKING') != CONDITION):
        raise ValueError('Unbound Luna development judge scope')
    check_lock(scope.get('execution_lock_fd'))
    check_stops(root)
    proof = scope['auto_recharge_evidence']
    if Path(proof['path']) != CHECKPOINT/'operator-confirmation.json':
        raise ValueError('Independent account checkpoint required')
    confirm_setting(read(proof))
    key = os.environ.get('PACBENCH_DIRECT_UPSTREAM_KEY') or os.environ.get('OPENROUTER_API_KEY', '')
    require_checkpoint_key(key)


def require_checkpoint_key(key):
    expected = (CHECKPOINT/'execution-key-fingerprint.private.txt').read_text().strip()
    if not key or hashlib.sha256(key.encode()).hexdigest() != expected:
        raise ValueError('Execution key differs from approved account checkpoint')


def admit(scope, project, body):
    from .workflow_capability import ENV
    cell = next((cell for cell in frozen_requests() if cell['body'] == body), None)
    if project != PROJECT or cell is None:
        raise ValueError('Unreviewed Luna development request')
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                slot=dict(kind='judge', provider=NAME, question_id=cell['id']), projects=[PROJECT])


def prefix_summary(root, rows, cells):
    for row, cell in zip(rows, cells):
        if (row['workflow_slot']['question_id'] != cell['id']
                or row['request_sha256'] != cell['body_sha256']
                or row['route_profile'] != PROFILE
                or row['dispatch_context']['attempt_number'] != 1
                or row.get('retry_of')):
            raise RuntimeError('A/B request order, identity, or attempt differs')
    if len(rows) > len(cells):
        raise RuntimeError('Too many A/B attempts')
    summary = receipt_summary(root, rows, CAMPAIGN.name)
    if (summary['missing_attempt_ids'] or summary['generations'] != len(rows)
            or any(row.get('http_status') != 200 for row in rows)):
        raise RuntimeError('A/B prefix has an unsuccessful, ambiguous, or non-unique receipt; stop')
    for row in rows:
        verify_stream(root, row)
    return summary


def verify_stream(root, row):
    from .chat_stream import parse_chat_stream
    from .openrouter_route import PROFILES
    # ModelClient may stop reading at the SSE terminator without a socket EOF.
    # Authenticate the saved bytes and require the normal complete-stream parser.
    item = row.get('direct_body_evidence', {})
    path = root/item.get('relative_path', '')
    if path != path.resolve() or path.parent != root/'response-bodies':
        raise ValueError('Missing or foreign A/B response evidence')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != item.get('sha256'):
        raise ValueError('A/B response evidence changed')
    served, _ = parse_chat_stream(raw, PROFILES[PROFILE]['model'])
    if served.get('id') != row['upstream_request_id']:
        raise ValueError('A/B response and billing generation differ')
    if served.get('provider') != 'Amazon Bedrock' or row.get('served_model') != PROFILES[PROFILE]['model']:
        raise ValueError('A/B receipt does not confirm the pinned served model and provider')


class JudgeJournal(RunReceiptJournal):
    def prepare(self, project, body, **kwargs):
        # Validate the original body too, before the shared transport applies its normalization.
        if project != PROJECT or not any(cell['body'] == body for cell in frozen_requests()):
            raise ValueError('Unreviewed Luna development request')
        return super().prepare(project, body, **kwargs)

    def check_prepared(self, rows, row):
        cells = frozen_requests()
        prefix = list(rows.values())
        prefix_summary(self.path.parent, prefix, cells)
        if len(prefix) == len(cells) or row['workflow_slot']['question_id'] != cells[len(prefix)]['id']:
            raise RuntimeError('Only the next unused A/B cell may dispatch')

    def mark_dispatch_possible(self, identifier, **kwargs):
        from .workflow_capability import load
        load()
        super().mark_dispatch_possible(identifier, **kwargs)

    def retry_delay(self, identifier):
        return None


def account_data(endpoint, key):
    from .gateway.transport import open_without_redirect
    req = Request('https://openrouter.ai/api/v1/'+endpoint, headers={'Authorization': 'Bearer '+key})
    with open_without_redirect(req, timeout=15) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('Unexpected OpenRouter account response size')
    return raw, json.loads(raw, parse_float=Decimal)['data']


def verify_prepaid(root, key, proof):
    from .direct_accounting import now
    from .workflow_capability import confirm_setting
    require_checkpoint_key(key)
    confirm_setting(read(proof))
    reference = json.loads((CHECKPOINT/'key.private.json').read_text(), parse_float=Decimal)['data']
    started = now()
    _, status = account_data('key', key)
    raw, credit = account_data('credits', key)
    identity = ('label', 'organization_id', 'workspace_id', 'creator_user_id',
                'is_management_key', 'is_provisioning_key', 'expires_at')
    if any(field not in status or field not in reference or status[field] != reference[field] for field in identity):
        raise ValueError('Authenticated account/key identity differs from checkpoint')
    for data in (status, credit):
        for field, value in data.items():
            if field.lower().replace('_', '') in {'autorecharge', 'autorechargeenabled', 'autotopup'}:
                if value is not False and value is not None and value != 'off':
                    raise ValueError('Account returned contrary Auto Recharge evidence')
    limit = Decimal(str(status['limit']))
    key_room = Decimal(str(status['limit_remaining']))
    prepaid = Decimal(str(credit['total_credits'])) - Decimal(str(credit['total_usage']))
    if ('limit_reset' not in status or status['limit_reset'] is not None
            or any(not amount.is_finite() or amount <= 0 for amount in (limit, key_room, prepaid))
            or key_room > limit):
        raise ValueError('Positive existing prepaid credit and finite nonresetting key room required')
    digest = hashlib.sha256(raw).hexdigest()
    folder = root/'prepaid-verification'
    folder.mkdir(mode=0o700)
    with (folder/(digest+'.private.json')).open('xb') as stream:
        stream.write(raw)
    return {'request_started': started, 'observed_at': now(), 'remaining_usd': str(prepaid),
            'receipt_sha256': digest, 'source': 'OpenRouter GET /api/v1/credits'}


def run():
    from . import workflow_capability as cap
    from .execution_capability import selected
    from .gateway_client import client_settings
    from .model_calls import ModelClient
    from .openrouter_direct import direct_environment
    from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from .workflow_memcal_live import run_environment

    root = CAMPAIGN/NAME
    check_checkout()
    if PROFILE_NAME != PROFILE or selected() is not None or any(os.environ.get(var) for var in (
            'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_R2_LIVE_SCOPE',
            'PACBENCH_JUDGE_OUTPUT_RECOVERY', JOURNAL)):
        raise ValueError('Fresh standalone Luna Bedrock Global judge environment required')
    cells = frozen_requests()
    with account_lock() as lock_fd:
        check_stops(root)
        key = os.environ['OPENROUTER_API_KEY']
        require_checkpoint_key(key)
        proof = binding(CHECKPOINT/'operator-confirmation.json')
        cap.confirm_setting(read(proof))
        if root.exists():
            raise RuntimeError('A/B run already exists; preserve its evidence and do not replay')
        catalog, _ = fetch_catalog(profile=PROFILE)
        if catalog['max_completion_tokens'] < 32768:
            raise ValueError('Pinned route does not support the frozen output setting')
        root.mkdir(mode=0o700, exist_ok=False)
        settings = {JOURNAL: str(root/'receipts.jsonl'), COHORT_STOP: str(CAMPAIGN/'ACCOUNT_STOP'),
                    'PACBENCH_STOP_FILE': str(root/'STOP'), 'PACBENCH_DISPATCH_TRACKING': CONDITION,
                    'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1',
                    'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768'}
        scope = dict(root=str(root), admission=SCOPE, provider=NAME, run_id=CAMPAIGN.name,
                     reviewed_commit=REVISION, manifest_sha256=MANIFEST_SHA, projects={NAME: {'judge': PROJECT}},
                     controller=process_identity(), prepaid=verify_prepaid(root, key, proof),
                     auto_recharge_evidence=proof, execution_lock_fd=lock_fd)
        path = root/'workflow-scope.json'
        with path.open('x') as stream:
            json.dump(scope, stream)
        settings[cap.ENV] = json.dumps(binding(path))
        observations = []
        with run_environment(settings), direct_environment(key):
            ledger = cap.budget()
            _, marker = client_settings(PROJECT)
            client = ModelClient(key=marker, model=MODEL, record_dir=root/'model-calls',
                                 url=CHAT_COMPLETIONS_URL, max_attempts=1, timeout_s=180)
            with (root/'observations.jsonl').open('x') as stream:
                for index, cell in enumerate(cells, 1):
                    answer = client._request(cell['body'], protocol='chat')
                    rows = ledger.snapshot()
                    billing = prefix_summary(root, rows, cells)
                    if len(rows) != index:
                        raise RuntimeError('Cell returned without its unique settled request')
                    obs = dict(case_id=cell['case_id'], arm=cell['arm'], response=answer.text)
                    stream.write(json.dumps(obs)+'\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                    observations.append(obs)
        result = plan.evaluate_saved(observations)
        result['billing'] = billing
        with (root/'result.json').open('x') as stream:
            json.dump(result, stream, indent=2)
        return result


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = run()
    print(json.dumps({'breakdown': result['breakdown'], 'billing': result['billing']}))


if __name__ == '__main__':
    main()
