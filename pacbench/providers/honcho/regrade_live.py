"""Historical Luna v4 judging for the sealed 31-chat Honcho Week only."""
import argparse
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path

from ... import cohort_regrade as regrade, cohort_regrade_live as transport
from ...canary_capability import binding, read, sha
from ...cohort_regrade_journal import _require_complete_arms
from ...live_dispatch import process_identity
from ...observational_receipts import replay_attempts
from ...semantic_grade import Claim, grade_semantic
from pacbench.workflows.recording import receipt_summary

prepaid = transport.prepaid
SCOPE = 'pizza-week-honcho-authored-chats-luna-v4-v1'
PROJECT = 'pacbench-honcho-authored-chats-luna-v4-judge'
REVISION = 'luna-context-v4-v1'
SOURCE = 'honcho-seven-daily-authored-chats-week-v2-live-verification/honcho'
INPUT_SHA = 'b8d334e70199c9be0cb3c563a0ba320d798873e020bb57e54f3b8b6d56ca26e5'
RESULT_SHA = 'afe7bdfef891f6b779d2eabd2c0e0c404c727898a30985600ca8a84ccc7960ef'
PLAN_ID = 'da94f275fec9206f464027b75720623837e3566e8763b81038d23f27d3d2db64'
WIRE_SHA = '644db2f83ed518dfd3b6c424d968b5267d22949c122f73b248bb325e4726e8e4'
REQUESTS = 47
CRITERIA = 192


def _verify_wire(wire):
    if (wire['plan_id'] != PLAN_ID or wire['input_manifest_sha256'] != INPUT_SHA
            or len(wire['cells']) != REQUESTS or regrade.digest(wire['cells']) != WIRE_SHA):
        raise ValueError('Honcho v4 requests differ from the frozen 47-cell plan')


def frozen_plan():
    source = prepaid.CAMPAIGN/SOURCE
    manifest = source/'workflow-scope.json'
    if sha(manifest) != INPUT_SHA or sha(source/'result.json') != RESULT_SHA:
        raise ValueError('Honcho source scope or original sealed result differs')
    plan = regrade.snapshot(manifest, REVISION)
    _require_complete_arms(plan)
    row = plan['coverage']['honcho']
    if (tuple(plan['coverage']) != ('honcho',) or row['observed_native_sessions'] != 31
            or row['binding']['single_native_session'] is not False
            or (row['completed_turns'], row['judge_requests_planned'], row['expected_criteria']) != (49, REQUESTS, CRITERIA)
            or sum(len(item['saved']['turn']['claims']) for item in plan['items']) != CRITERIA):
        raise ValueError('Honcho v4 requires the complete authored chats and original criteria only')
    wire = transport._wire_plan(plan)
    _verify_wire(wire)
    return plan, wire


def validate(scope, bound):
    from pacbench.workflows.capability import confirm_setting

    root = prepaid.CAMPAIGN/SCOPE
    proof = scope['auto_recharge_evidence']
    if (scope.get('admission') != SCOPE or scope.get('root') != str(root) or root != root.resolve()
            or scope.get('provider') != SCOPE or scope.get('run_id') != prepaid.CAMPAIGN.name
            or scope.get('controller') != process_identity() or Path(bound['path']) != root/'workflow-scope.json'
            or (scope.get('revision'), scope.get('plan_id'), scope.get('input_manifest_sha256')) != (REVISION, PLAN_ID, INPUT_SHA)
            or scope.get('projects') != {SCOPE: {'judge': PROJECT}}
            or os.environ.get(transport.JOURNAL) != str(root/'receipts.jsonl')
            or os.environ.get(transport.COHORT_STOP) != str(prepaid.CAMPAIGN/'ACCOUNT_STOP')
            or os.environ.get('PACBENCH_STOP_FILE') != str(root/'STOP')
            or os.environ.get('PACBENCH_DISPATCH_TRACKING') != transport.CONDITION
            or Path(proof['path']) != prepaid.CHECKPOINT/'operator-confirmation.json'
            or Path(scope['wire_plan']['path']) != root/'request-manifest.json'):
        raise ValueError('Unbound authored Honcho v4 judge scope')
    prepaid.check_lock(scope['execution_lock_fd'])
    prepaid.check_stops(root)
    confirm_setting(read(proof))
    prepaid.require_checkpoint_key(os.environ.get('PACBENCH_DIRECT_UPSTREAM_KEY') or os.environ.get('OPENROUTER_API_KEY', ''))
    _verify_wire(transport._wire_binding(scope))


def admit(scope, project, body):
    from pacbench.workflows.capability import ENV

    active = os.environ.get(transport.ACTIVE_CELL)
    expected = next((cell for cell in transport._wire_binding(scope)['cells'] if cell['id'] == active), None)
    if project != PROJECT or expected is None or transport._wire_sha(body) != expected['request_sha256']:
        raise ValueError('Request differs from the active frozen Honcho v4 cell')
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                slot=dict(kind='judge', provider=SCOPE, question_id=active), projects=[PROJECT])


def _check_report(root, plan, wire, report):
    """Independently verify the saved score from the authenticated original streams."""
    with (root/'receipts.jsonl').open() as stream:
        rows = list(replay_attempts(stream).values())
    with (root/'observations.jsonl').open() as stream:
        observations = [json.loads(line) for line in stream]
    observed_wire = read(binding(root/'request-manifest.json'))
    if observed_wire != wire or len(rows) != REQUESTS or len(observations) != REQUESTS:
        raise ValueError('Retained Honcho v4 judging is incomplete or has changed')
    seen, correct, graded = set(), 0, 0
    for item, cell, row, observation in zip(plan['items'], wire['cells'], rows, observations, strict=True):
        response = observation['response']
        if (observation['plan_id'] != plan['plan_id'] or observation['cell'] != cell['id']
                or observation['input_sha256'] != cell['input_sha256'] or observation['prompt_sha256'] != cell['prompt_sha256']
                or observation['attempt_id'] != row['id'] or observation['generation_id'] != row['upstream_request_id']
                or observation['billing_receipt_sha256'] != row['billing_receipt']['sha256']
                or observation['wire_response_sha256'] != row['direct_body_evidence']['sha256']
                or observation['response_sha256'] != sha256(response.encode()).hexdigest()):
            raise ValueError('Honcho v4 observation differs from the frozen input or response evidence')
        cost = transport._verified_response(root, row, cell, response, seen)
        if cost != Decimal(observation['actual_usd']):
            raise ValueError('Honcho v4 receipt cost differs from the observation')
        turn = item['saved']['turn']
        claims = tuple(Claim(c['id'], c['description'], tuple(c['evidence'])) for c in turn['claims'])
        grade = grade_semantic(turn['id'], turn['prompt'], item['saved']['surface']['answer'], claims,
                               lambda _: response, **item['options'])
        correct += grade.correct
        graded += len(grade.slots)
    arm = report['providers']['honcho']
    if (graded != CRITERIA or report['plan_id'] != PLAN_ID or report['revision'] != REVISION
            or report.get('condition') != SCOPE or report['receipt_verified'] is not True
            or report['billing'] != receipt_summary(root, rows, prepaid.CAMPAIGN.name)
            or (arm['regrade_correct'], arm['regraded_criteria'], arm['expected_criteria']) != (correct, graded, CRITERIA)):
        raise ValueError('Honcho v4 report differs from the independently replayed judgments')
    return report


def run():
    from pacbench.workflows import capability as capability
    from ...execution_capability import selected
    from ...openrouter_direct import direct_environment
    from ...openrouter_route import PROFILE_NAME
    from ...openrouter_run import fetch_catalog
    from .. import run_environment

    plan, wire = frozen_plan()
    root = prepaid.CAMPAIGN/SCOPE
    if root.exists():
        saved = root/'report.json'
        if not saved.exists():
            raise RuntimeError('Existing Honcho v4 attempt is incomplete. Preserve it without replay.')
        return _check_report(root, plan, wire, read(binding(saved)))
    if PROFILE_NAME != transport.PROFILE or selected() is not None or any(os.environ.get(var) for var in (
            transport.JOURNAL, 'PACBENCH_DIRECT', 'PACBENCH_DIRECT_UPSTREAM_KEY', 'PACBENCH_JUDGE_OUTPUT_RECOVERY',
            capability.ENV, transport.ACTIVE_CELL)):
        raise ValueError('Honcho v4 requires a fresh approved Luna execution context')
    with prepaid.account_lock() as lock:
        prepaid.check_stops(root)
        if root.exists():
            raise RuntimeError('A durable Honcho v4 attempt already exists')
        key = os.environ['OPENROUTER_API_KEY']
        prepaid.require_checkpoint_key(key)
        catalog, _ = fetch_catalog(profile=transport.PROFILE)
        if catalog['max_completion_tokens'] < 32768:
            raise ValueError('Pinned route lacks the historical judge output allowance')
        root.mkdir(mode=0o700)
        transport._write_new(root/'request-manifest.json', wire)
        proof = binding(prepaid.CHECKPOINT/'operator-confirmation.json')
        scope = dict(admission=SCOPE, provider=SCOPE, run_id=prepaid.CAMPAIGN.name, root=str(root),
                     controller=process_identity(), execution_lock_fd=lock, plan_id=PLAN_ID, revision=REVISION,
                     input_manifest_sha256=INPUT_SHA, projects={SCOPE: {'judge': PROJECT}},
                     wire_plan=binding(root/'request-manifest.json'), auto_recharge_evidence=proof,
                     prepaid=prepaid.verify_prepaid(root, key, proof))
        transport._write_new(root/'workflow-scope.json', scope)
        settings = {capability.ENV: json.dumps(binding(root/'workflow-scope.json')),
                    transport.JOURNAL: str(root/'receipts.jsonl'),
                    transport.COHORT_STOP: str(prepaid.CAMPAIGN/'ACCOUNT_STOP'),
                    'PACBENCH_STOP_FILE': str(root/'STOP'), 'PACBENCH_DISPATCH_TRACKING': transport.CONDITION,
                    'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1',
                    'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1',
                    'PACBENCH_EFFECTIVE_OUTPUT_TOKENS': '32768'}
        overlay = regrade.Overlay(root/'overlay', plan)
        try:
            with run_environment(settings), direct_environment(key):
                billing = transport._grade(root, scope, overlay, plan, wire, stop_on_invalid=True)
            frozen_plan()
            report = overlay.report()
            report.update(condition=SCOPE, billing=billing, receipt_verified=True,
                          request_manifest_sha256=sha(root/'request-manifest.json'),
                          observations_sha256=sha(root/'observations.jsonl'))
            _check_report(root, plan, wire, report)
            regrade.write_report(root/'report.json', report)
        except (OSError, ValueError, RuntimeError) as error:
            report = overlay.report()
            report.update(condition=SCOPE, state='incomplete', failure_type=type(error).__name__)
            regrade.write_report(root/'partial-report.json', report)
            raise
        finally:
            overlay.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true', help='Run the single prepaid judge batch, or verify its saved result')
    args = parser.parse_args()
    os.umask(0o077)
    if args.execute:
        report = run()
        row = report['providers']['honcho']
        summary = dict(condition=SCOPE, correct=row['regrade_correct'], graded=row['regraded_criteria'],
                       denominator=CRITERIA, actual_usd=report['billing']['complete_cost_usd'])
    else:
        plan, _ = frozen_plan()
        summary = dict(condition=SCOPE, plan_id=plan['plan_id'], requests=REQUESTS, criteria=CRITERIA,
                       wire_sha256=WIRE_SHA, judged=False)
    print(json.dumps(summary, sort_keys=True))


if __name__ == '__main__':
    main()
