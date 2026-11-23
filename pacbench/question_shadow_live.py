"""Single-use paid admission for the frozen, inspected-answer development regression."""
import argparse
from decimal import Decimal
import json
import os
from pathlib import Path

from . import judge_development_live as prepaid, workflow_capability as capability
from .canary_capability import binding, read
from .execution_capability import selected
from .gateway_client import client_settings
from .model_calls import ModelClient
from .openrouter_direct import direct_environment
from .openrouter_route import CHAT_COMPLETIONS_URL, MODEL, PROFILE_NAME
from .openrouter_run import fetch_catalog
from .question_shadow_development import REVIEWED, compare, fingerprint, render, save, wire
from .workflow_memcal_live import run_environment

SCOPE = 'pizza-week-question-contract-development-shadow-v1'
PROJECT = 'pacbench-week-question-contract-shadow-judge'
MANIFEST = '7cb8395727307ea7f65c7f4a5c0a70e6dbf9e39ce11ea449b56c4a3072271695'
REQUESTS = 'b6ed09faa5ec80bc7eba601417233b70bf9681e4781f718b1a0066b859897b1f'


def frozen_cells():
    path = prepaid.CAMPAIGN / SCOPE / 'requests.json'
    if path.resolve() != path:
        raise ValueError('Foreign request manifest path')
    cells = json.loads(path.read_bytes())
    if fingerprint(cells) != REQUESTS:
        raise ValueError('Shadow request manifest differs')
    return cells


def validate(scope, bound):
    prepaid.validate_standalone(scope, bound, admission=SCOPE, name=SCOPE, project=PROJECT,
                               revision=REVIEWED, manifest_sha=MANIFEST)


def admit(scope, project, body):
    return prepaid.admit_frozen(scope, project, body, expected_project=PROJECT, cells=frozen_cells())


class ShadowJournal(prepaid.JudgeJournal):
    project = PROJECT

    def frozen_cells(self):
        return frozen_cells()

    def check_wire(self, method, data):
        if method != 'POST' or not any(wire(cell['body']) == data for cell in frozen_cells()):
            raise ValueError('Request bytes differ from the frozen development pair')


def run(exports):
    manifest, cells, cases = render(exports)
    if fingerprint(manifest) != MANIFEST or fingerprint(cells) != REQUESTS:
        raise ValueError('Inputs, reference, settings or schedule differ from the frozen regression')
    root = prepaid.CAMPAIGN / SCOPE
    if (PROFILE_NAME != prepaid.PROFILE or selected() is not None or any(os.environ.get(k) for k in (
            'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_R2_LIVE_SCOPE',
            'PACBENCH_JUDGE_OUTPUT_RECOVERY', capability.ENV, prepaid.JOURNAL))):
        raise ValueError('Fresh standalone Luna context required')
    with prepaid.account_lock() as lock:
        prepaid.check_stops(root)
        if root.resolve() != root or root.exists():
            raise RuntimeError('Shadow scope already exists or has a foreign path; preserve without replay')
        key = os.environ['OPENROUTER_API_KEY']
        prepaid.require_checkpoint_key(key)
        proof = binding(prepaid.CHECKPOINT / 'operator-confirmation.json')
        capability.confirm_setting(read(proof))
        catalog, catalog_proof = fetch_catalog(profile=prepaid.PROFILE)
        if catalog['max_completion_tokens'] < 32768:
            raise ValueError('Pinned route no longer supports the frozen output setting')
        root.mkdir(mode=0o700)
        save(root / 'manifest.json', manifest)
        save(root / 'requests.json', cells)
        scope = dict(root=str(root), admission=SCOPE, provider=SCOPE, run_id=prepaid.CAMPAIGN.name,
                     reviewed_commit=REVIEWED, manifest_sha256=MANIFEST, projects={SCOPE: {'judge': PROJECT}},
                     controller=prepaid.process_identity(), prepaid=prepaid.verify_prepaid(root, key, proof),
                     auto_recharge_evidence=proof, execution_lock_fd=lock, chat_catalog=catalog_proof)
        save(root / 'workflow-scope.json', scope)
        settings = {capability.ENV: json.dumps(binding(root / 'workflow-scope.json')),
                    prepaid.JOURNAL: str(root / 'receipts.jsonl'),
                    prepaid.COHORT_STOP: str(prepaid.CAMPAIGN / 'ACCOUNT_STOP'),
                    'PACBENCH_STOP_FILE': str(root / 'STOP'), 'PACBENCH_DISPATCH_TRACKING': prepaid.CONDITION,
                    'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1',
                    'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768'}
        replies = {}
        with run_environment(settings), direct_environment(key):
            journal = capability.budget(project=PROJECT)
            _, marker = client_settings(PROJECT)
            client = ModelClient(key=marker, model=MODEL, record_dir=root/'model-calls', url=CHAT_COMPLETIONS_URL,
                                 max_attempts=1, timeout_s=180)
            for index, cell in enumerate(cells, 1):
                prepaid.check_stops(root)
                answer = client._request(cell['body'], protocol='chat')
                rows = journal.snapshot()
                if len(rows) != index:
                    raise RuntimeError('Missing unique attempt for the current shadow cell')
                billing = prepaid.prefix_summary(root, rows, cells)
                served = prepaid.verify_stream(root, rows[-1])
                if Decimal(str(served['usage']['cost'])) != Decimal(str(rows[-1]['settled_charge_usd'])):
                    raise RuntimeError('Stream cost differs from the settled attempt')
                choice = served['choices'][0]
                if choice['message']['content'] != answer.text:
                    raise RuntimeError('Decoded output differs from the settled wire response')
                reply = dict(request_sha256=fingerprint(cell['body']), text=answer.text,
                             finish_reason=choice['finish_reason'])
                save(root / f'observation-{index:02}.json', dict(cell=cell['id'], reply=reply,
                     attempt_id=rows[-1]['id'], receipt_sha256=rows[-1]['billing_receipt']['sha256']))
                replies[cell['id']] = reply
        result = dict(manifest_sha256=MANIFEST, requests_sha256=REQUESTS, billing=billing,
                      results=compare(cases, replies))
        save(root / 'result.json', result)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('exports', type=Path)
    args = parser.parse_args(argv)
    result = run(args.exports)
    print(json.dumps({'manifest_sha256': MANIFEST, 'billing': result['billing']}))
    return 0


if __name__ == '__main__':
    from .question_shadow_live import main as entry
    raise SystemExit(entry())
