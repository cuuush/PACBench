"""Consume durable verified turns without opening an answer account or replaying its work."""
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import json
import os
from itertools import islice
from pathlib import Path

from .canary_capability import digest
from .evidence_store import read_json
from .semantic_grade import (
    CONTEXT_JUDGE_PROTOCOL, RETRY_GUIDANCE, UnusableJudgeResponse, audit_native_judge_attempts,
    grade_native_semantic, grade_semantic, judge_prompt,
)
from .model_calls import ModelOutput
from .workflow_runner import UnverifiedJudgment, grade_turn, judge_options


# Admission receives the actual post-routing wire body. A claimed turn's prompt
# is available only inside the judge's call stack, including its documented retry.
_dispatch = ContextVar('verified_judge_dispatch', default=None)


_recovery = ContextVar('verified_judge_recovery', default=None)


@contextmanager
def judge_only_owner(progress, scope):
    """Bind a judge-only worker to this run; document turns can be consumed as they verify."""
    from .canary_capability import binding
    from .live_dispatch import process_identity

    root = Path(scope['root'])
    bound = binding(root/'workflow-scope.json')
    from .workflow_document_progress import PROTOCOL, PROVIDERS
    identity = progress.binding('identity') or {}
    document = (identity.get('protocol') == PROTOCOL and scope['provider'] in PROVIDERS
                and identity.get('condition', {}).get('provider') == scope['provider']
                and identity.get('account') == str(root) and identity.get('spec_sha256') == scope['spec_sha256'])
    if ((not document and scope['provider'] != 'memcal' and scope.get('admission') != 'pizza-day-native-document-v1')
            or progress.directory != root/'workflow'
            or progress.binding('workflow_scope') != bound):
        raise ValueError('judge recovery requires the original qualified run scope')
    original = scope['controller']
    current = process_identity()
    if set(original) != set(current) or type(original['pid']) is not int or original['pid'] <= 1:
        raise ValueError('missing original process identity')
    # PID namespaces cannot establish another machine's process ownership.
    if original['boot_id'] != current['boot_id'] or original['uid'] != current['uid']:
        raise ValueError('original controller unavailable on this host')
    try:
        owner = process_identity(original['pid'])
    except FileNotFoundError:
        owner = None
    if owner == original and (not document or owner == current):
        raise ValueError('original workflow controller is still running')
    path = progress.directory/'judge-recovery.lock'
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = _recovery.set((progress, bound, current))
        try:
            yield
        finally:
            _recovery.reset(token)


def _check(progress, directory, limit):
    if type(limit) is not int or limit < 1:
        raise ValueError('grade batch limit must be a positive integer')
    directory = Path(directory).absolute()
    if (directory != directory.resolve() or directory != progress.directory
            or progress.binding('evidence_kind') != 'native'):
        raise ValueError('grading requires this run’s verified native checkpoints')
    return directory


def retained_grade(turn, surface, directory, *, audit_path=None):
    """Verify the actual frozen rubric, answer, timing, request/response and Luna route offline."""
    path = audit_path or directory/'judgments'/turn.id/'judgment.json'
    if path != path.resolve():
        raise UnverifiedJudgment('judgment is outside its run directory')
    options = judge_options(turn, surface)
    try:
        audit_native_judge_attempts(path, question=turn.prompt, answer=surface['answer'], claims=turn.claims,
                                    required_profile='luna-bedrock-global', model_call_dir=directory/'model-calls',
                                    **options)
        record = read_json(path)
        if record['checkpoint'] != turn.id:
            raise ValueError('judgment belongs to another checkpoint')
        return grade_semantic(turn.id, turn.prompt, surface['answer'], turn.claims,
                              lambda _: record['response'], **options)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise UnverifiedJudgment(str(exc)) from exc


def _saved_response(progress, turn, surface, directory):
    """Rebuild a missing final judgment from a returned response, without a transport."""
    import tempfile
    from .canary_capability import read

    def cached(prompt):
        proof = progress.binding('judge-response/'+turn.id+'/'+digest(prompt))
        if not proof:
            # ModelClient writes its canonical run-scoped response before it
            # returns. A crash at that boundary can leave this sole witness.
            from .openrouter_route import CHAT_COMPLETIONS_URL
            key = 'judge-intent/'+turn.id+'/'+digest(prompt)
            if progress.active_grade(turn.id) is None or progress.binding(key) != progress.active_grade(turn.id):
                raise UnverifiedJudgment('no claimed request for this prompt')
            request = {'model':'openai/gpt-6-luna', 'temperature':0.0, 'messages':[{'role':'user','content':prompt}],
                       'response_format':{'type':'json_object'}, 'stream':True, 'stream_options':{'include_usage':True}}
            path = directory/'model-calls'/(digest({'url':CHAT_COMPLETIONS_URL,'body':request})+'.json')
            if not path.is_file() or path != path.resolve():
                raise UnverifiedJudgment('no stored response to the original direct request')
            call = read_json(path)
            if (call.get('request') != request or not call.get('judge_transport',{}).get('id')
                    or call['judge_transport'].get('checkpoint_effect') != progress.active_grade(turn.id)
                    or call['judge_transport']['id'] != (call.get('accounting_receipt') or {}).get('id')):
                raise UnverifiedJudgment('unbound original direct response')
            return ModelOutput(call['text'], call['model'], call.get('usage') or {}, str(path))
        path = Path(proof['path'])
        if path != path.resolve() or path.parent != directory/'model-calls':
            raise UnverifiedJudgment('foreign retained judge response')
        try:
            call = read(proof)
            return ModelOutput(call['text'], call['model'], call.get('usage') or {}, str(path))
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise UnverifiedJudgment('retained model response changed') from exc

    opts = judge_options(turn, surface)
    with tempfile.TemporaryDirectory(prefix='pacbench-audit-judge-') as audit_dir:
        output = Path(audit_dir)/'judgment.json'
        try:
            grade = grade_native_semantic(turn.id, turn.prompt, surface['answer'], turn.claims, cached,
                                         record_path=output, **opts)
            audit_native_judge_attempts(output, question=turn.prompt, answer=surface['answer'], claims=turn.claims,
                                        required_profile='luna-bedrock-global', model_call_dir=directory/'model-calls',
                                        **opts)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise UnverifiedJudgment('returned response or transport is unverified') from exc
        # Save the bound attempts before changing the claim to graded. A later
        # exporter can replay the audit even if the final file never appeared.
        attempts = [read_json(p) for p in sorted((output.parent/'judgment-attempts').glob('attempt-*.json'))]
        progress.bind('recovered-judge-attempts/'+turn.id, attempts)
    return grade


def reconcile_claimed(progress, directory, *, limit, after=None):
    """Audit a page of interrupted claims. Missing receipts leave the original claim in doubt."""
    directory = _check(progress, directory, limit)
    rows = progress.claimed_grades(page_size=limit, after=after)
    result = {'examined': 0, 'recovered': 0, 'uncertain': [], 'last_turn_id': None}
    for turn, surface in islice(rows, limit):
        result['examined'] += 1
        result['last_turn_id'] = turn.id
        try:
            if (directory/'judgments'/turn.id/'judgment.json').exists():
                grade = retained_grade(turn, surface, directory)
            else:
                grade = _saved_response(progress, turn, surface, directory)
        except UnverifiedJudgment:
            result['uncertain'].append(turn.id)
            continue
        progress.finish_grade(turn.id, [vars(c) for c in grade.slots])
        result['recovered'] += 1
    return result


def grade_verified(progress, judge, directory, journal, *, limit, unsent_claim=None):
    """Claim and score up to limit turns. Only the progress store determines eligibility.

    A terminal refusal or crash leaves a durable claim. Reconciliation can finish
    that claim only from a saved and independently audited response.
    """
    directory = _check(progress, directory, limit)
    processed = 0
    while processed < limit:
        item = (progress.get_claimed_grade(unsent_claim) if unsent_claim else progress.claim_grade())
        if item is None:
            break
        turn, surface = item
        journal.write('judge-started', question=turn.id, measure=turn.measure)
        def observed(prompt):
            prompt_hash = digest(prompt)
            progress.bind('judge-intent/'+turn.id+'/'+prompt_hash, progress.active_grade(turn.id))
            journal.write('judge-request', question=turn.id, prompt_sha256=prompt_hash)
            token = _dispatch.set((progress, turn.id, prompt_hash))
            try:
                result = judge(prompt)
                if isinstance(result, ModelOutput):
                    from .canary_capability import binding
                    path = Path(result.record_path)
                    if path != path.resolve() or path.parent != directory/'model-calls':
                        raise UnverifiedJudgment('foreign judge model record')
                    progress.bind('judge-response/'+turn.id+'/'+prompt_hash, binding(path))
                return result
            finally:
                _dispatch.reset(token)
        try:
            # An existing directory is evidence of a prior attempt. Never enter
            # the network path to fill a missing response or reinterpret a refusal.
            if (directory/'judgments'/turn.id).exists():
                grade = retained_grade(turn, surface, directory)
            else:
                grade = grade_turn(turn, surface, observed, directory/'judgments', require_receipt=True)
        except (UnusableJudgeResponse, UnverifiedJudgment) as exc:
            failure = type(exc).__name__
            progress.finish_grade(turn.id, None, failure=failure)
            journal.write('ungraded', question=turn.id, measure=turn.measure, reason=failure)
        else:
            criteria = [vars(c) for c in grade.slots]
            progress.finish_grade(turn.id, criteria)
            journal.write('grade', question=turn.id, measure=turn.measure,
                          judge=CONTEXT_JUDGE_PROTOCOL, criteria=criteria)
        processed += 1
    return processed


def active_claim(scope, project, effect):
    """Recheck the exact durable claim and its current judge owner immediately before dispatch."""
    from .canary_capability import binding
    from .live_dispatch import process_identity

    claim = _dispatch.get()
    if claim is None or not effect:
        return False
    progress, question, _ = claim
    root = Path(scope['root'])
    bound = binding(root/'workflow-scope.json')
    owner = process_identity()
    return ((scope['controller'] == owner or _recovery.get() == (progress, bound, owner))
            and project == scope['projects'][scope['provider']]['judge']
            and progress.directory == root/'workflow' and progress.binding('evidence_kind') == 'native'
            and progress.binding('workflow_scope') == bound and progress.active_grade(question) == effect)


def claimed_request(scope, project, body):
    """Identify a current verified-turn judge request from its original scope.

    The caller must first validate its normal scope, native deployment, project
    and Luna route. A recovery owner can submit this judge role only.
    """
    claim = _dispatch.get()
    if claim is None:
        return None
    progress, question, prompt_hash = claim
    effect = progress.active_grade(question)
    captured = progress.get_claimed_grade(question)
    if captured is None or not active_claim(scope, project, effect):
        raise ValueError('judge does not own the bound verified checkpoint')
    turn, surface = captured
    expected = judge_prompt(turn.prompt, surface['answer'], turn.claims, **judge_options(turn, surface))
    messages = body.get('messages')
    if (not isinstance(messages, list) or len(messages) != 1 or messages[0].get('role') != 'user'
            or messages[0].get('content') not in (expected, expected + RETRY_GUIDANCE)
            or digest(messages[0]['content']) != prompt_hash or body.get('tools')):
        raise ValueError('judge wire request differs from the claimed prompt')
    return {'kind': 'judge', 'question_id': question, 'provider': scope['provider'], 'checkpoint_effect': effect}


def paid_batch(progress, directory, limit, *, resume_unsent_day=False):
    """Grade an interrupted run in place without opening its account or issuing a user turn."""
    # Route modules read this once at import. Reject mixed/legacy admission modes.
    os.environ.setdefault('PACBENCH_OPENROUTER_PROFILE', 'luna-bedrock-global')
    from . import execution_capability, workflow_capability as cap
    from .canary_capability import binding
    from .gateway_client import client_settings
    from .live_dispatch import CONDITION
    from .model_calls import ModelClient
    from .observational_receipts import credit_snapshot
    from .openrouter_direct import direct_environment
    from .openrouter_route import PROFILE_NAME, MODEL, CHAT_COMPLETIONS_URL
    from .workflow_runner import Journal

    root = directory.parent
    bound = binding(root/'workflow-scope.json')
    if (PROFILE_NAME != cap.PROFILE or progress.binding('workflow_scope') != bound
            or execution_capability.selected() is not None or os.environ.get('PACBENCH_R2_LIVE_SCOPE')
            or os.environ.get('PACBENCH_DIRECT_UPSTREAM_KEY') or os.environ.get('PACBENCH_JUDGE_OUTPUT_RECOVERY')
            or not (root/'receipts.jsonl').is_file() or (root/'receipts.jsonl').is_symlink()):
        raise ValueError('judge recovery requires the original receipt journal and a fresh Luna process')
    os.environ.update({cap.ENV: json.dumps(bound), 'PACBENCH_RECEIPT_JOURNAL': str(root/'receipts.jsonl'),
        'PACBENCH_DISPATCH_TRACKING': CONDITION, 'PACBENCH_ROLE_ROUTES': 'luna-all-roles-v1',
        'PACBENCH_LUNA_COMPATIBILITY': 'temperature-omitted-default-v1', 'PACBENCH_EFFECTIVE_OUTPUT_TOKENS':'32768',
        'PACBENCH_STOP_FILE': str(root/'STOP'), 'PACBENCH_OBSERVATIONAL_COHORT_STOP': str(root.parent/'ACCOUNT_STOP')})
    scope = cap.load()
    # Verify the original billing setting and receipt. Check the current prepaid
    # balance again before the first possible send. The transport retains its
    # normal refusal/admission/unknown-receipt policy for every request.
    with judge_only_owner(progress, scope):
        budget = cap.budget()
        unsent = None
        if resume_unsent_day:
            from .pizza_day_honcho import unsent_judge
            unsent = unsent_judge(progress, scope, budget)
            if limit != 1:
                raise ValueError('only the single verified Pizza Day question can be recovered')
        if not unsent and not progress.grade_states().get('ready'):
            return 0
        if (root/'STOP').exists() or (root.parent/'ACCOUNT_STOP').exists():
            raise RuntimeError('original run is stopped')
        offline_generations = (budget.has_offline_generations() if hasattr(budget, 'has_offline_generations')
                               else any((r.get('upstream_request_id') or '').startswith('offline-native-')
                                        for r in budget.snapshot()))
        if offline_generations:
            raise ValueError('loopback fixture answers cannot receive live judgments')
        credit_snapshot()
        from uuid import uuid4
        journal_dir = directory/'judge-recovery'/uuid4().hex
        with direct_environment(os.environ['OPENROUTER_API_KEY']), Journal(journal_dir) as journal:
            project = scope['projects'][scope['provider']]['judge']
            _, key = client_settings(project)
            judge = ModelClient(key=key, model=MODEL, record_dir=directory/'model-calls',
                                url=CHAT_COMPLETIONS_URL, json_mode=True, max_attempts=1)
            return grade_verified(progress, judge, directory, journal, limit=limit, unsent_claim=unsent)


def main(argv=None):
    import argparse
    from .workflow_progress import WorkflowProgress

    parser = argparse.ArgumentParser(description='Inspect incremental grading or reconcile saved Luna receipts offline.')
    parser.add_argument('directory', type=Path, help='existing workflow directory containing run.json')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--grade', action='store_true', help='use the approved Luna judge for previously unsent verified turns')
    actions.add_argument('--reconcile', action='store_true', help='commit only grades proved by existing judge receipts')
    parser.add_argument('--limit', type=int, default=32)
    parser.add_argument('--after', help='resume inspection after this claimed turn ID')
    args = parser.parse_args(argv)
    directory = args.directory.absolute()
    meta = read_json(directory/'run.json')
    from .workflow_stream import fingerprint
    identity = meta.get('identity', {})
    from .workflow_document_progress import PROTOCOL
    if (identity.get('protocol') not in ('memcal-incremental-v1', PROTOCOL) or meta.get('evidence_kind') != 'native'
            or identity.get('evidence_kind') != 'native' or fingerprint(identity) != meta['run_key']):
        raise ValueError('expected the original incremental native run')
    with WorkflowProgress(directory, meta['run_key']) as progress:
        _check(progress, directory, args.limit)
        if progress.binding('identity') != identity or not progress.binding('plan_registered'):
            raise ValueError('workflow metadata differs from its registered plan')
        report = {}
        if args.reconcile:
            report['reconciliation'] = reconcile_claimed(progress, directory, limit=args.limit, after=args.after)
        if args.grade:
            report['newly_processed'] = paid_batch(progress, directory, args.limit)
        from .workflow_metrics import coverage
        report['by_measure'] = progress.summary()
        report['coverage'] = coverage(report['by_measure'], complete=progress.complete)
        report['grade_states'] = progress.grade_states()
        report['complete'] = progress.complete
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
