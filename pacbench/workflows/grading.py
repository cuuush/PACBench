"""Consume durable verified turns without opening an answer account or replaying its work."""
import fcntl
import os
from contextlib import contextmanager
from contextvars import ContextVar
from itertools import islice
from pathlib import Path

from pacbench.evidence_store import binding_digest as digest
from pacbench.judging.grade import (
    CONTEXT_JUDGE_PROTOCOL,
    RETRY_GUIDANCE,
    UnusableJudgeResponse,
    audit_native_judge_attempts,
    grade_native_semantic,
    grade_semantic,
    judge_prompt,
)

from ..evidence_store import read_json
from ..model_calls import ModelOutput
from .runner import UnverifiedJudgment, grade_turn, judge_options

# Admission receives the actual post-routing wire body. A claimed turn's prompt
# is available only inside the judge's call stack, including its documented retry.
_dispatch = ContextVar('verified_judge_dispatch', default=None)


_recovery = ContextVar('verified_judge_recovery', default=None)


@contextmanager
def judge_only_owner(progress, scope):
    """Bind a judge-only worker to this run; document turns can be consumed as they verify."""
    from pacbench.evidence_store import file_binding as binding
    from pacbench.workflows.progress import process_identity

    root = Path(scope['root'])
    bound = binding(root/'workflow-scope.json')
    from pacbench.workflows.progress import DOCUMENT_PROGRESS_PROTOCOL as PROTOCOL
    from pacbench.workflows.progress import DOCUMENT_PROVIDERS as PROVIDERS
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

    from pacbench.judging.retained_scope import read_canary_binding as read

    def cached(prompt):
        proof = progress.binding('judge-response/'+turn.id+'/'+digest(prompt))
        if not proof:
            # ModelClient writes its canonical run-scoped response before it
            # returns. A crash at that boundary can leave this sole witness.
            from ..openrouter_route import CHAT_COMPLETIONS_URL
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
                    from pacbench.evidence_store import file_binding as binding
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
    from pacbench.evidence_store import file_binding as binding
    from pacbench.workflows.progress import process_identity

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
