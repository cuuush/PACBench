"""Run the single reviewed, prepaid Pizza Week development calibration."""
import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request

from pacbench.judging import decision_matrix as matrix
from pacbench import judge_development_challenge as challenge
from pacbench.judging import development_live as prepaid
from pacbench.workflows import capability as cap
from ..canary_capability import binding, read
from .decision_admission import DecisionsAdmission, _write_new, decision_dispatch
from ..execution_capability import selected
from ..gateway.transport import open_without_redirect
from ..gateway_client import client_settings
from ..model_calls import ModelClient, read_direct_response
from ..openrouter_direct import direct_environment
from ..openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
from ..openrouter_run import fetch_catalog
from pacbench.providers import run_environment
from ..workflows.recording import receipt_summary


SCOPE = 'luna-judge-development-challenge-v2'
NAME = 'pizza-week-judge-calibration-development-v2'
PROJECT = 'pacbench-pizza-week-development-matrix-judge'
REVISION = '2c0705911a8d0ad58d0ae937192221475edbf88d'
SEED = '9f' * 32
MANIFEST = '3e2beb4453788e8d844318cfe860ec5d521cbea9a61f203fa6e326b7b0c8f091'
USER_CATALOG = 'https://openrouter.ai/api/v1/models/user?output_modalities=decisions'
TIMEOUT = 180


def frozen_plan():
    plan = matrix.render_plan(SEED)
    if (plan.digest != MANIFEST or len(plan.cells) != 96
            or len({cell.digest for cell in plan.cells}) != 96):
        raise ValueError('Unreviewed four-arm development manifest')
    return plan


def check_checkout():
    prepaid.verify_reviewed_checkout(REVISION, (Path(challenge.__file__), challenge.CELLS, challenge.REFERENCE),
                                    'Frozen development inputs or request renderer changed')


def chat_cells():
    return [{'id': cell.id, 'body': json.loads(cell.body), 'body_sha256': cell.digest}
            for cell in frozen_plan().cells if cell.url == CHAT_COMPLETIONS_URL]


def validate(scope, bound):
    prepaid.validate_standalone(scope, bound, admission=SCOPE, name=NAME, project=PROJECT,
                               revision=REVISION, manifest_sha=MANIFEST)


def admit(scope, project, body):
    return prepaid.admit_frozen(scope, project, body, expected_project=PROJECT, cells=chat_cells())


class ChatJournal(prepaid.JudgeJournal):
    project = PROJECT

    def frozen_cells(self):
        return chat_cells()

    def check_wire(self, method, body):
        if method != 'POST' or not any(cell.url == CHAT_COMPLETIONS_URL and cell.body == body
                                      for cell in frozen_plan().cells):
            raise ValueError('Expected exact reviewed Chat Completions wire request')


def _stop(phase, cell, exc):
    if isinstance(exc, HTTPError):
        reason = f'HTTP {exc.code}'
    else:
        reason = str(exc).splitlines()[0][:320] if str(exc) else type(exc).__name__
    return {'phase': phase, 'cell': cell, 'error': type(exc).__name__, 'reason': reason}


def _chat(root, marker):
    ledger = cap.budget(project=PROJECT)
    client = ModelClient(key=marker, model=MODEL, record_dir=root/'model-calls',
                         url=CHAT_COMPLETIONS_URL, max_attempts=1, timeout_s=TIMEOUT)
    cells = chat_cells()
    verdicts = {}
    failure = None
    with (root/'chat-observations.jsonl').open('x') as stream:
        for index, cell in enumerate(cells, 1):
            try:
                answer = client._request(cell['body'], protocol='chat')
                rows = ledger.snapshot()
                prepaid.prefix_summary(root, rows, cells)
                if len(rows) != index:
                    raise RuntimeError('Chat cell lacks its unique settled request')
                served = prepaid.verify_stream(root, rows[-1])
                if served['choices'][0]['message']['content'] != answer.text:
                    raise ValueError('Saved Chat output differs from its receipted response')
            except (ValueError, RuntimeError, OSError) as exc:
                failure = _stop('chat', cell['id'], exc)
                break
            verdict = matrix.chat_verdict(answer.text)
            stream.write(json.dumps({'id': cell['id'], 'verdict': asdict(verdict)}) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
            verdicts[cell['id']] = verdict
            if index % 12 == 0:
                print(f'Chat Completions verified {index}/48', flush=True)
    rows = ledger.snapshot()
    try:
        billed = receipt_summary(root, rows, prepaid.CAMPAIGN.name)
    except ValueError as exc:
        failure = failure or _stop('chat-accounting', None, exc)
        # The verified prefix still provides an exact lower bound.
        billed = receipt_summary(root, rows[:len(verdicts)], prepaid.CAMPAIGN.name)
        billed['unreconciled_attempts'] = len(rows) - len(verdicts)
    billed['verified_verdicts'] = len(verdicts)
    return verdicts, billed, failure


def _catalog_json(url, key):
    request = Request(url, headers={'Authorization': 'Bearer ' + key})
    with open_without_redirect(request, timeout=30) as reply:
        raw = reply.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('Decision model inventory exceeded the response size limit')
    value = json.loads(raw)
    if not isinstance(value, dict) or 'data' not in value:
        raise ValueError('Invalid Decisions model inventory envelope')
    return value['data'], sha256(raw).hexdigest()


def decisions_inventory(key):
    """Check authoritative account-filtered inventory without making an inference call."""
    rows, digest = _catalog_json(USER_CATALOG, key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Invalid key-filtered Decisions model inventory')
    evidence = {'account_inventory_sha256': digest, 'routes': {}}
    for arm, route in matrix.ROUTES.items():
        matches = [row for row in rows if row.get('id') == route.model]
        if len(matches) != 1 or '/api/alpha/decisions' not in matches[0].get('endpoints', []):
            raise ValueError(f'Key-filtered Decisions inventory did not admit {route.model}')
        model, route_sha = _catalog_json(f'https://openrouter.ai/api/v1/models/{route.model}/endpoints', key)
        if not isinstance(model, dict) or not isinstance(model.get('endpoints'), list):
            raise ValueError(f'Invalid Decisions endpoint inventory for {route.model}')
        endpoints = [row for row in model['endpoints']
                     if isinstance(row, dict) and row.get('tag') == route.endpoint]
        if (model.get('id') != route.model or len(endpoints) != 1
                or endpoints[0].get('name') != f'{route.served_provider} | {route.served_model}'
                or endpoints[0].get('provider_name') != route.served_provider
                or endpoints[0].get('is_byok') is True):
            raise ValueError(f'Decisions route evidence differs for {route.model}/{route.endpoint}')
        evidence['routes'][arm] = {'model': route.model, 'provider': route.endpoint, 'catalog_sha256': route_sha}
    return evidence


def _decision_call(admission, marker, cell):
    request = Request(cell.url, data=cell.body, method='POST',
                      headers={'Authorization': 'Bearer ' + marker, 'Content-Type': 'application/json'})
    with decision_dispatch(admission, cell) as dispatch:
        started = time.monotonic()
        try:
            with open_without_redirect(request, timeout=TIMEOUT) as reply:
                read_direct_response(reply, started, timeout_s=TIMEOUT)
        except HTTPError as exc:
            with exc:
                read_direct_response(exc, started, timeout_s=TIMEOUT)
            raise
        if dispatch.failure or dispatch.verdict is None:
            raise ValueError(dispatch.failure or 'Decisions response lacks an attached billing receipt')


def _decisions(root, key, lock_fd):
    prepaid.check_stops(root)
    _write_new(root/'decisions-inventory.json', matrix.wire_bytes(decisions_inventory(key)))
    rows = ChatJournal(root/'receipts.jsonl').snapshot()
    prepaid.prefix_summary(root, rows, chat_cells())
    if len(rows) != 48:
        raise ValueError('Decisions requires 48 verified Chat Completions receipts')
    admission = DecisionsAdmission(SEED, reviewed_manifest_sha256=MANIFEST, lock_fd=lock_fd, key=key,
                                   completed_chat=binding(root/'receipts.jsonl'),
                                   chat_generations=[row['upstream_request_id'] for row in rows])
    failure = None
    with direct_environment(key):
        _, marker = client_settings(PROJECT)
        for index, cell in enumerate(admission.cells, 1):
            try:
                _decision_call(admission, marker, cell)
            except (ValueError, RuntimeError, OSError) as exc:
                failure = _stop('decisions', cell.id, exc)
                break
            if index % 12 == 0:
                print(f'Decisions verified {index}/48', flush=True)
    billing = admission.audit()
    return billing.pop('verdicts'), billing, failure


def run():
    check_checkout()
    plan = frozen_plan()
    root = prepaid.CAMPAIGN/NAME
    if (PROFILE_NAME != prepaid.PROFILE or selected() is not None or any(os.environ.get(name) for name in (
            'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_R2_LIVE_SCOPE',
            'PACBENCH_JUDGE_OUTPUT_RECOVERY', prepaid.JOURNAL))):
        raise ValueError('Fresh standalone Luna Bedrock Global judge environment required')
    with prepaid.account_lock() as lock_fd:
        prepaid.check_stops(root)
        if root != root.resolve() or root.exists():
            raise RuntimeError('Calibration directory already exists; preserve its evidence')
        key = os.environ['OPENROUTER_API_KEY']
        prepaid.require_checkpoint_key(key)
        proof = binding(prepaid.CHECKPOINT/'operator-confirmation.json')
        cap.confirm_setting(read(proof))
        catalog, catalog_proof = fetch_catalog(profile=prepaid.PROFILE)
        if catalog['max_completion_tokens'] < 32768:
            raise ValueError('Bedrock Global does not support the frozen output setting')
        root.mkdir(mode=0o700)
        _write_new(root/'reviewed-manifest.json', matrix.wire_bytes(plan.manifest))
        settings = {prepaid.JOURNAL: str(root/'receipts.jsonl'),
                    prepaid.COHORT_STOP: str(prepaid.CAMPAIGN/'ACCOUNT_STOP'),
                    'PACBENCH_STOP_FILE': str(root/'STOP'), 'PACBENCH_DISPATCH_TRACKING': prepaid.CONDITION,
                    'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1',
                    'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768'}
        scope = dict(root=str(root), admission=SCOPE, provider=NAME, run_id=prepaid.CAMPAIGN.name,
                     reviewed_commit=REVISION, manifest_sha256=MANIFEST, projects={NAME: {'judge': PROJECT}},
                     controller=prepaid.process_identity(), prepaid=prepaid.verify_prepaid(root, key, proof),
                     auto_recharge_evidence=proof, execution_lock_fd=lock_fd, chat_catalog=catalog_proof)
        path = root/'workflow-scope.json'
        _write_new(path, matrix.wire_bytes(scope))
        settings[cap.ENV] = json.dumps(binding(path))
        decisions_billing = None
        started = time.monotonic()
        with run_environment(settings):
            with direct_environment(key):
                _, marker = client_settings(PROJECT)
                observations, chat_billing, failure = _chat(root, marker)
            _write_new(root/'chat-result.json', matrix.wire_bytes({
                'billing': chat_billing, 'stop': failure, 'comparison': matrix.paired_errors(SEED, observations.items()),
            }))
            if failure is None:
                try:
                    more, decisions_billing, failure = _decisions(root, key, lock_fd)
                except (ValueError, RuntimeError, OSError) as exc:
                    failure = _stop('decisions-preflight', None, exc)
                else:
                    observations.update(more)
        report = matrix.paired_errors(SEED, observations.items())
        result = dict(report, state='complete' if failure is None else 'stopped',
                      stop=failure, chat_billing=chat_billing, decisions_billing=decisions_billing,
                      wall_seconds=round(time.monotonic() - started, 1))
        _write_new(root/'result.json', matrix.wire_bytes(result))
        return result


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    result = run()
    print(json.dumps({key: result[key] for key in ('state', 'stop', 'chat_billing', 'decisions_billing',
                                                     'wall_seconds', 'manifest_sha256')}), flush=True)
    return 0 if result['state'] == 'complete' else 2


if __name__ == '__main__':
    from .calibration_live import main as entry
    raise SystemExit(entry())
