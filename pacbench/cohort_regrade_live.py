"""Run the verified Pizza Week overlay through the existing prepaid judge transport."""
import argparse
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path

from . import cohort_regrade as regrade
from pacbench.judging import development_live as prepaid
from .canary_capability import binding, read, sha
from .chat_stream import parse_chat_stream
from .cohort_regrade_journal import _require_complete_arms
from .live_dispatch import CONDITION, process_identity
from .observational_receipts import COHORT_STOP, ENV as JOURNAL, RunReceiptJournal
from .openrouter_route import CHAT_COMPLETIONS_URL, PROFILES, with_route
from .workflows.recording import receipt_summary

SCOPE = 'pizza-week-luna-explicit-overlay-v1'
PROJECT = 'pacbench-pizza-week-explicit-overlay-judge'
REVISION = 'luna-explicit-criteria-v1'
PROFILE = 'luna-bedrock-global'
MODEL = PROFILES[PROFILE]['model']
ARMS = ('honcho', 'supermemory', 'memcal', 'mem0', 'hindsight', 'openviking', 'reme', 'cognee', 'memos')
MANIFEST_SHA = 'a98d3d8ea0c5348e44faf159ff22a4e467d58201e77a3802cbb5453a16aa506b'
INVENTORY_SHA = '57311a1db8743cb2d99063bee233e5e7929846853b10b1bc88ea6f381fd4158f'
ACTIVE_CELL = 'PACBENCH_COHORT_REGRADE_ACTIVE_CELL'


def _verify_inventory(manifest):
    from .cohort_regrade_retained import read_bytes

    registry = prepaid.CAMPAIGN.parent/'pizza-week-nine-cohort-inventory-v1.json'
    raw, digest = read_bytes(registry)
    entries, manifest_sha = regrade.discover(manifest)
    if digest != INVENTORY_SHA or manifest_sha != MANIFEST_SHA:
        raise ValueError('cohort manifests differ from the reviewed retained inputs')
    inventory = json.loads(raw)
    by_arm = {row['arm']: row for row in inventory['cohorts']}
    if tuple(entry.arm for entry in entries) != ARMS or set(by_arm) != set(ARMS):
        raise ValueError('unexpected cohort coverage or first-grade order')
    root = prepaid.CAMPAIGN.parent
    if inventory['root'] != str(root):
        raise ValueError('retained inventory belongs to a different runner')
    for entry in entries:
        item = by_arm[entry.arm]
        folder = root/item['root']
        files = {**item['archive_files'], 'result.json': item['result_sha256'],
                 'workflow-scope.json': item['scope_sha256']}
        if (entry.path != folder/'workflow-scope.json' or entry.sha256 != item['scope_sha256']
                or any(read_bytes(folder/name)[1] != digest for name, digest in files.items())):
            raise ValueError(f'retained artifact changed for {entry.arm}')


def wire_body(item):
    return with_route({
        'model': MODEL, 'messages': [{'role': 'user', 'content': item['prompt']}],
        'response_format': {'type': 'json_object'}, 'stream': True,
        'stream_options': {'include_usage': True}, 'max_completion_tokens': 32768,
    }, profile=PROFILE)


def _wire_sha(body):
    # Match the existing dispatch journal's request fingerprint.
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _wire_plan(plan):
    return {'plan_id': plan['plan_id'], 'input_manifest_sha256': plan['manifest_sha256'],
            'cells': [{'id': item['id'], 'request_sha256': _wire_sha(wire_body(item)),
                       'input_sha256': item['input_sha256'], 'prompt_sha256': item['prompt_sha256']}
                      for item in plan['items']]}


def _wire_binding(scope):
    return read(scope['wire_plan'])


def validate(scope, bound):
    from .workflows.capability import confirm_setting

    root = prepaid.CAMPAIGN/SCOPE
    if (scope.get('admission') != SCOPE or scope.get('root') != str(root) or root != root.resolve()
            or scope.get('provider') != SCOPE or scope.get('run_id') != prepaid.CAMPAIGN.name
            or scope.get('controller') != process_identity()
            or Path(bound['path']) != root/'workflow-scope.json'
            or scope.get('revision') != REVISION or scope.get('input_manifest_sha256') != MANIFEST_SHA
            or scope.get('projects') != {SCOPE: {'judge': PROJECT}}
            or os.environ.get(JOURNAL) != str(root/'receipts.jsonl')
            or os.environ.get(COHORT_STOP) != str(prepaid.CAMPAIGN/'ACCOUNT_STOP')
            or os.environ.get('PACBENCH_STOP_FILE') != str(root/'STOP')
            or os.environ.get('PACBENCH_DISPATCH_TRACKING') != CONDITION):
        raise ValueError('unbound Pizza Week saved-answer judge scope')
    prepaid.check_lock(scope.get('execution_lock_fd'))
    prepaid.check_stops(root)
    proof = scope['auto_recharge_evidence']
    if Path(proof['path']) != prepaid.CHECKPOINT/'operator-confirmation.json':
        raise ValueError('independent account checkpoint required for saved-answer judging')
    confirm_setting(read(proof))
    prepaid.require_checkpoint_key(os.environ.get('PACBENCH_DIRECT_UPSTREAM_KEY') or os.environ.get('OPENROUTER_API_KEY', ''))
    if Path(scope['wire_plan']['path']) != root/'request-manifest.json':
        raise ValueError('foreign saved-answer request manifest')
    wire = _wire_binding(scope)
    if (wire['plan_id'] != scope['plan_id'] or wire['input_manifest_sha256'] != MANIFEST_SHA
            or len(wire['cells']) != len(ARMS)*47 or len({c['id'] for c in wire['cells']}) != len(wire['cells'])):
        raise ValueError('changed frozen plan or repeated saved-answer cells')


def admit(scope, project, body):
    from .workflows.capability import ENV

    active = os.environ.get(ACTIVE_CELL)
    expected = next((row for row in _wire_binding(scope)['cells'] if row['id'] == active), None)
    if project != PROJECT or expected is None or _wire_sha(body) != expected['request_sha256']:
        raise ValueError('request differs from the exact active saved-answer cell')
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                slot=dict(kind='judge', provider=SCOPE, question_id=active), projects=[PROJECT])


def _matches(row, cell):
    return (row['workflow_slot']['question_id'] == cell['id']
            and row['request_sha256'] == cell['request_sha256'] and row['route_profile'] == PROFILE
            and row['dispatch_context']['attempt_number'] == 1 and not row.get('retry_of'))


class OverlayJournal(RunReceiptJournal):
    def prepare(self, project, body, **kwargs):
        from .workflows.capability import admission

        # Refuse changes to the caller's body even when transport would normalize them.
        admission(project, PROFILE, body)
        return super().prepare(project, body, **kwargs)

    def check_prepared(self, rows, row):
        cells = _wire_binding(read(row['workflow_capability']))['cells']
        if (len(rows) >= len(cells) or not _matches(row, cells[len(rows)])
                or any(not _matches(previous, cell) or previous['state'] != 'settled'
                       or previous['http_status'] != 200
                       for previous, cell in zip(rows.values(), cells))):
            raise RuntimeError('only the next unused saved-answer cell can dispatch after settled predecessors')

    def mark_dispatch_possible(self, identifier, **kwargs):
        from .workflows.capability import load

        load()
        super().mark_dispatch_possible(identifier, **kwargs)

    def retry_delay(self, identifier):
        return None


def _verified_response(root, row, cell, text, seen):
    generation = row.get('upstream_request_id')
    if (not _matches(row, cell) or row.get('state') != 'settled' or row.get('http_status') != 200
            or not generation or generation in seen):
        raise RuntimeError('missing, duplicate or ambiguous settled judge receipt')
    billing = receipt_summary(root, [row], prepaid.CAMPAIGN.name)
    if billing['generations'] != 1 or billing['missing_attempt_ids']:
        raise RuntimeError('saved-answer cell has no unique settled judge receipt')
    prepaid.verify_stream(root, row)
    response, _ = parse_chat_stream((root/row['direct_body_evidence']['relative_path']).read_bytes(), MODEL)
    if (response['choices'][0]['message']['content'] != text
            or Decimal(str(response['usage']['cost'])) != Decimal(billing['complete_cost_usd'])):
        raise RuntimeError('saved-answer response differs from its settled wire body')
    seen.add(generation)
    return Decimal(billing['complete_cost_usd'])


def _write_new(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def _grade(root, scope, overlay, plan, wire, *, stop_on_invalid=False):
    from .gateway_client import client_settings
    from .model_calls import ModelClient
    from .workflows.capability import budget
    from pacbench.providers import run_environment

    journal = budget()
    project = scope['projects'][scope['provider']]['judge']
    _, marker = client_settings(project)
    total, seen = Decimal(0), set()
    with (root/'observations.jsonl').open('x') as stream:
        for index, (item, cell) in enumerate(zip(plan['items'], wire['cells'], strict=True), 1):
            prepaid.check_stops(root)
            # Scope ModelClient's automatic record reuse to one exact paid cell.
            directory = root/'model-calls'/item['id']
            if directory.exists():
                raise ValueError('saved-answer cell already has model output evidence')
            client = ModelClient(key=marker, model=MODEL, record_dir=directory, url=CHAT_COMPLETIONS_URL,
                                 max_attempts=1, timeout_s=180)
            with run_environment({ACTIVE_CELL: cell['id']}):
                answer = client._request(wire_body(item), protocol='chat')
            rows = journal.snapshot()
            if len(rows) != index:
                raise RuntimeError('saved-answer cell did not produce exactly one new metered attempt')
            row = rows[-1]
            cost = _verified_response(root, row, cell, answer.text, seen)
            total += cost
            record = {'plan_id': plan['plan_id'], 'cell': cell['id'], 'input_sha256': cell['input_sha256'],
                      'prompt_sha256': cell['prompt_sha256'], 'attempt_id': row['id'], 'response': answer.text,
                      'response_sha256': hashlib.sha256(answer.text.encode()).hexdigest(),
                      'generation_id': row['upstream_request_id'], 'actual_usd': str(cost),
                      'billing_receipt_sha256': row['billing_receipt']['sha256'],
                      'wire_response_sha256': row['direct_body_evidence']['sha256']}
            stream.write(json.dumps(record, sort_keys=True)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
            overlay.import_response(record)
            report = overlay.report() if stop_on_invalid or index == 1 or index % 47 == 0 else None
            if index == 1 or index % 47 == 0:
                arm = report['providers'][item['arm']]
                print(json.dumps({'settled_cells': index, 'arm': item['arm'], 'graded_criteria': arm['regraded_criteria'],
                                  'correct': arm['regrade_correct'], 'actual_usd': str(total)}), flush=True)
                _write_new(root/f'checkpoint-{index:03}.json', report)
            if (stop_on_invalid or index == 1) and report['states'][cell['id']] != 'parsed-offline':
                raise RuntimeError('settled judge response did not parse as frozen criteria')
        billing = receipt_summary(root, rows, scope['run_id'])
        if (billing['generations'] != len(wire['cells']) or billing['missing_attempt_ids']
                or Decimal(billing['complete_cost_usd']) != total):
            raise RuntimeError('the full judge overlay does not reconcile to its settled receipts')
    return billing


def run(manifest, expected_plan):
    from pacbench.workflows import capability as capability
    from .execution_capability import selected
    from .openrouter_direct import direct_environment
    from .openrouter_route import PROFILE_NAME
    from .openrouter_run import fetch_catalog
    from pacbench.providers import run_environment

    root = prepaid.CAMPAIGN/SCOPE
    if PROFILE_NAME != PROFILE or selected() is not None or any(os.environ.get(var) for var in (
            JOURNAL, 'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_JUDGE_OUTPUT_RECOVERY', ACTIVE_CELL)):
        raise ValueError('fresh already-authorized prepaid Luna context required')
    _verify_inventory(manifest)
    plan = regrade.snapshot(manifest, REVISION)
    _require_complete_arms(plan)
    if (plan['plan_id'] != expected_plan or plan['manifest_sha256'] != MANIFEST_SHA
            or tuple(plan['coverage']) != ARMS or any(
                (row['completed_turns'], row['judge_requests_planned'], row['expected_criteria']) != (49, 47, 192)
                for row in plan['coverage'].values())):
        raise ValueError('local regrade plan differs from its verified nine-arm input')
    wire = _wire_plan(plan)
    with prepaid.account_lock() as lock:
        prepaid.check_stops(root)
        if root.exists():
            raise RuntimeError('this retained-answer scope already has a durable run; preserve it without replay')
        key = os.environ['OPENROUTER_API_KEY']
        prepaid.require_checkpoint_key(key)
        catalog, _ = fetch_catalog(profile=PROFILE)
        if catalog['max_completion_tokens'] < 32768:
            raise ValueError('pinned route no longer supports the established judge output setting')
        root.mkdir(mode=0o700)
        _write_new(root/'request-manifest.json', wire)
        proof = binding(prepaid.CHECKPOINT/'operator-confirmation.json')
        scope = dict(admission=SCOPE, provider=SCOPE, run_id=prepaid.CAMPAIGN.name, root=str(root),
                     controller=process_identity(), execution_lock_fd=lock, plan_id=plan['plan_id'],
                     revision=REVISION, input_manifest_sha256=MANIFEST_SHA, projects={SCOPE: {'judge': PROJECT}},
                     wire_plan=binding(root/'request-manifest.json'), auto_recharge_evidence=proof,
                     prepaid=prepaid.verify_prepaid(root, key, proof))
        _write_new(root/'workflow-scope.json', scope)
        settings = {capability.ENV: json.dumps(binding(root/'workflow-scope.json')),
                    JOURNAL: str(root/'receipts.jsonl'), COHORT_STOP: str(prepaid.CAMPAIGN/'ACCOUNT_STOP'),
                    'PACBENCH_STOP_FILE': str(root/'STOP'), 'PACBENCH_DISPATCH_TRACKING': CONDITION,
                    'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1',
                    'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768'}
        overlay = regrade.Overlay(root/'overlay', plan)
        try:
            with run_environment(settings), direct_environment(key):
                billing = _grade(root, scope, overlay, plan, wire)
            _verify_inventory(manifest)
            result = overlay.report()
            result.update(billing=billing, provenance='receipt-verified Luna Chat Completions overlay',
                          request_manifest_sha256=sha(root/'request-manifest.json'),
                          observations_sha256=sha(root/'observations.jsonl'))
            result['receipt_verified'] = True
            regrade.write_report(root/'report.json', result)
        finally:
            overlay.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--expected-plan', required=True)
    args = parser.parse_args()
    report = run(args.manifest, args.expected_plan)
    print(json.dumps({'plan_id': report['plan_id'], 'settled_usd': report['billing']['complete_cost_usd'],
                      'arms': {name: [row['regrade_correct'], row['regraded_criteria']]
                               for name, row in report['providers'].items()}}))


if __name__ == '__main__':
    main()
