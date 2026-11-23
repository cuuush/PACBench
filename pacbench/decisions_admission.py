"""Exact request and receipt admission for the guarded Decisions transport."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import os
from pathlib import Path
import re
from uuid import uuid4

from . import judge_development_live as prepaid
from .canary_capability import binding, read, sha
from .decisions_calibration import DECISIONS_URL, ROUTES, decisions_verdict, render_plan, strict_json, wire_bytes
from .direct_accounting import now
from .observational_receipts import RunReceiptJournal, rejection_402, stop_cohort
from .response_receipts import receipt
from .workflow_capability import confirm_setting


NAME = 'pizza-week-decisions-development-v2'
_ACTIVE = ContextVar('calibration_decisions_dispatch', default=None)


def validate_response(cell, attempt_id, *, status, body, complete, observed_generation=None):
    """Bind a complete Decisions response to a pinned route and exact billed usage."""
    if type(status) is not int or status != 200 or complete is not True or not isinstance(body, bytes):
        raise ValueError('Complete Decisions HTTP 200 response required')
    value = strict_json(body.decode('utf-8'))
    if not isinstance(value, dict) or 'error' in value:
        raise ValueError('Invalid Decisions response envelope')
    route = ROUTES[cell.arm]
    if (value.get('model'), value.get('provider')) != (route.served_model, route.served_provider):
        raise ValueError('Wrong served Decisions model or provider')
    generation = value.get('id')
    if not isinstance(generation, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', generation):
        raise ValueError('Missing or invalid Decisions generation receipt')
    if observed_generation is not None and observed_generation != generation:
        raise ValueError('Decisions response and header generation IDs differ')
    usage = value.get('usage')
    if not isinstance(usage, dict) or ('is_byok' in usage and usage['is_byok'] is not False):
        raise ValueError('Prepaid Decisions usage required')
    cost = usage.get('cost')
    if type(cost) not in (int, Decimal) or not Decimal(cost).is_finite() or cost < 0:
        raise ValueError('Missing or invalid Decisions billed cost')
    tokens = {target: usage.get(source) for source, target in (
        ('input_tokens', 'prompt_tokens'), ('output_tokens', 'completion_tokens'),
    )}
    if any(type(count) is not int or count < 0 for count in tokens.values()):
        raise ValueError('Missing or invalid Decisions token usage')
    event = {'id': generation, 'model': route.served_model, 'provider': route.served_provider,
             'usage': {**tokens, 'cost': cost}}
    item = receipt(attempt_id, event, status=status, body_sha256=sha256(body).hexdigest(), complete=True)
    return {**item, 'endpoint': route.endpoint, 'url': cell.url, 'reported_byok': usage.get('is_byok'),
            'request_sha256': cell.digest}, decisions_verdict(value)


def _write_new(path, body):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


class DecisionsAdmission:
    """Own one ordered Decisions subplan in a single-use prepaid run directory.

    The reviewed manifest comes from the run's authorization, separately from
    the case inputs.
    """

    def __init__(self, seed, *, reviewed_manifest_sha256, lock_fd, key,
                 completed_chat=None, chat_generations=()):
        plan = render_plan(seed)
        if plan.digest != reviewed_manifest_sha256:
            raise ValueError('Unreviewed Decisions calibration manifest')
        self.cells = tuple(cell for cell in plan.cells if cell.arm in ROUTES)
        self.root = prepaid.CAMPAIGN/NAME
        if self.root != self.root.resolve() or self.root.exists():
            raise RuntimeError('Decisions calibration requires its unused run directory')
        self.lock_fd = lock_fd
        self.completed_chat = completed_chat
        self.chat_generations = frozenset(chat_generations)
        prepaid.check_lock(self.lock_fd)
        self.auto_recharge_evidence = binding(prepaid.CHECKPOINT/'operator-confirmation.json')
        self._check_admission()
        prepaid.require_checkpoint_key(key)
        self.root.mkdir(mode=0o700)
        self.responses = self.root/'response-bodies'
        self.responses.mkdir(mode=0o700)
        scope = dict(protocol=plan.manifest['protocol'], manifest_sha256=reviewed_manifest_sha256,
                     root=str(self.root), prepaid=prepaid.verify_prepaid(self.root, key, self.auto_recharge_evidence),
                     auto_recharge_evidence=self.auto_recharge_evidence, completed_chat=completed_chat)
        _write_new(self.root/'reviewed-manifest.json', wire_bytes(plan.manifest))
        path = self.root/'decisions-scope.json'
        _write_new(path, wire_bytes(scope))
        self.scope = binding(path)
        self._journal = RunReceiptJournal(self.root/'receipts.jsonl')
        _write_new(self._journal.path, b'')
        self._journal_identity = self._journal.path.stat()
        self._attempts = 0

    def _check_admission(self):
        prepaid.check_lock(self.lock_fd)
        prepaid.check_stops(self.root)
        confirm_setting(read(self.auto_recharge_evidence))

    def _identity(self, cell):
        route = ROUTES[cell.arm]
        return dict(cell=cell.id, request_sha256=cell.digest, url=cell.url, model=route.model,
                    endpoint=route.endpoint, scope_sha256=self.scope['sha256'])

    def _check_journal(self):
        path = self._journal.path
        if path != path.resolve() or not path.is_file() or not os.path.samestat(path.stat(), self._journal_identity):
            raise ValueError('Missing or replaced Decisions receipt journal')

    def _verified_prefix(self, rows):
        scope = read(self.scope)
        if self.completed_chat:
            baseline = Path(self.completed_chat['path'])
            if baseline != baseline.resolve() or sha(baseline) != self.completed_chat['sha256']:
                raise ValueError('Completed Chat Completions journal changed')
        manifest = self.root/'reviewed-manifest.json'
        balance_sha = scope['prepaid']['receipt_sha256']
        balance = self.root/'prepaid-verification'/(balance_sha + '.private.json')
        for path, expected in ((manifest, scope['manifest_sha256']), (balance, balance_sha)):
            if path != path.resolve() or not path.is_file() or sha(path) != expected:
                raise ValueError('Decisions manifest or prepaid evidence changed')
        if not self._attempts <= len(rows) <= len(self.cells):
            raise ValueError('Missing or excess Decisions attempts')
        verdicts, generations, unknown, costs = {}, set(), [], []
        for index, (cell, row) in enumerate(zip(self.cells, rows.values()), 1):
            if any(row.get(field) != expected for field, expected in self._identity(cell).items()):
                raise ValueError('Decisions attempt does not match the reviewed request order')
            if row['dispatch_execution'] != 'dispatch_possible' or row['attempt_number'] != 1:
                raise ValueError('Unverified Decisions dispatch')
            if row['state'] != 'settled':
                if row['state'] not in ('receipt_pending', 'unresolved') or index != len(rows):
                    raise ValueError('Invalid Decisions receipt prefix')
                unknown.append(row['id'])
                continue
            path = self.responses/(row['id'] + '.json')
            if path != path.resolve() or path.parent != self.responses:
                raise ValueError('Foreign Decisions response evidence')
            try:
                item, verdict = validate_response(
                    cell, row['id'], status=row['http_status'], body=path.read_bytes(),
                    complete=row['body_complete'], observed_generation=row['observed_generation'],
                )
            except (ValueError, OSError, KeyError, TypeError) as exc:
                raise ValueError('Unverified saved Decisions receipt') from exc
            generation = item['generation_id']
            cost = item['usage']['cost']
            if (generation in generations or generation in self.chat_generations
                    or item != row.get('billing_receipt')
                    or cost != row.get('settled_charge_usd') or asdict(verdict) != row.get('verdict')):
                raise ValueError('Duplicate or changed saved Decisions receipt')
            verdicts[cell.id] = verdict
            generations.add(generation)
            costs.append(Decimal(cost))
        return verdicts, generations, unknown, costs

    def prepare(self, *, cell_id, method, url, body):
        """Durably consume the next cell immediately before a potential send."""
        self._check_admission()
        self._check_journal()
        with self._journal.journal() as (rows, append):
            _, _, unknown, _ = self._verified_prefix(rows)
            if unknown:
                raise RuntimeError('Unsettled Decisions request; further dispatch refused')
            if len(rows) == len(self.cells):
                raise RuntimeError('Decisions calibration has no unused requests')
            cell = self.cells[len(rows)]
            if type(body) is not bytes or (cell_id, method, url, body) != (cell.id, 'POST', DECISIONS_URL, cell.body):
                raise ValueError('Only the next exact reviewed Decisions request is admitted')
            self._check_admission()
            attempt = str(uuid4())
            append('prepared', attempt, dict(
                self._identity(cell), id=attempt, attempt_number=1, state='receipt_pending',
                dispatch_execution='dispatch_possible', started=now(), finished=None,
            ))
            self._attempts = len(rows) + 1
            return attempt

    def settle(self, attempt, *, status, body, complete, observed_generation=None):
        """Append one terminal response to its pending attempt, even after a stop."""
        prepaid.check_lock(self.lock_fd)
        self._check_journal()
        with self._journal.journal() as (rows, append):
            _, generations, unknown, _ = self._verified_prefix(rows)
            if unknown != [attempt] or rows[attempt]['state'] != 'receipt_pending':
                raise ValueError('No matching pending Decisions attempt')
            cell = self.cells[len(rows) - 1]
            try:
                item, verdict = validate_response(
                    cell, attempt, status=status, body=body, complete=complete,
                    observed_generation=observed_generation,
                )
                if item['generation_id'] in generations or item['generation_id'] in self.chat_generations:
                    raise ValueError('Duplicate Decisions generation ID')
            except ValueError:
                append('unresolved', attempt, dict(
                    state='unresolved', finished=now(),
                    http_status=status if type(status) is int else None,
                    body_sha256=sha256(body).hexdigest() if isinstance(body, bytes) else None,
                    body_complete=complete is True,
                ))
                raise
            _write_new(self.responses/(attempt + '.json'), body)
            append('settled', attempt, dict(
                state='settled', finished=now(), http_status=status, body_complete=True,
                observed_generation=observed_generation, billing_receipt=item,
                settled_charge_usd=item['usage']['cost'], verdict=asdict(verdict),
            ))
        return verdict

    def audit(self):
        """Return normalized cells and exact-validated costs without replaying a request."""
        self._check_journal()
        with self._journal.journal() as (rows, _):
            verdicts, generations, unknown, costs = self._verified_prefix(rows)
        return dict(verdicts=verdicts, attempted=len(rows), generations=len(generations),
                    remaining=len(self.cells) - len(rows), unknown=unknown,
                    known_cost_usd=str(sum(costs, Decimal(0))) if costs else None)


class _Dispatch:
    """One runner-owned request through the existing before-send and capture hooks."""

    def __init__(self, admission, cell):
        self.admission = admission
        self.cell = cell
        self.attempt = None
        self.sent = False
        self.verdict = None
        self.failure = None

    def mark_dispatch_possible(self, ident):
        self.admission._check_admission()
        if _ACTIVE.get() is not self or self.attempt != ident or self.sent:
            raise RuntimeError('Decisions dispatch does not own an unused request')
        self.sent = True

    def observe_response(self, ident, status, event=None, *, headers=None):
        # The complete response and any observed generation header are
        # reconciled together by settle().
        if ident != self.attempt or not self.sent:
            raise RuntimeError('Decision response has no owned dispatch')

    def settle(self, ident, status, body=b'', *, headers=None, body_complete=False):
        if ident != self.attempt or not self.sent:
            raise RuntimeError('Decision receipt has no owned dispatch')
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        length = headers.get('content-length')
        if length is not None and (not length.isascii() or not length.isdecimal() or int(length) != len(body)):
            body_complete = False
        try:
            self.verdict = self.admission.settle(
                ident, status=status, body=body, complete=body_complete,
                observed_generation=headers.get('x-generation-id'),
            )
        except ValueError as exc:
            self.failure = str(exc)
        if status in (401, 403):
            stop_cohort('openrouter_access_refused', ident)
        if status == 402 and rejection_402(body, headers)[0] == 'insufficient_credits':
            stop_cohort('insufficient_credits', ident)


@contextmanager
def decision_dispatch(admission, cell):
    if _ACTIVE.get() is not None:
        raise RuntimeError('A Decisions cell is already active')
    dispatch = _Dispatch(admission, cell)
    token = _ACTIVE.set(dispatch)
    try:
        yield dispatch
    finally:
        _ACTIVE.reset(token)


def admit_decisions(project, method, url, body):
    from . import execution_capability, judge_calibration_live as runner, workflow_capability

    dispatch = _ACTIVE.get()
    if (dispatch is None or dispatch.attempt or project != runner.PROJECT
            or execution_capability.selected() is not workflow_capability
            or os.environ.get('PACBENCH_R2_LIVE_SCOPE')):
        raise RuntimeError('Decisions requires the active frozen development scope')
    scope = workflow_capability.load()
    admission = dispatch.admission
    if (scope['admission'] != runner.SCOPE or scope['execution_lock_fd'] != admission.lock_fd
            or not admission.completed_chat
            or admission.completed_chat['path'] != str(prepaid.CAMPAIGN/runner.NAME/'receipts.jsonl')
            or len(admission.chat_generations) != 48):
        raise RuntimeError('Decisions requires the verified Chat Completions cohort')
    dispatch.attempt = admission.prepare(cell_id=dispatch.cell.id, method=method, url=url, body=body)
    return dispatch.attempt, dispatch
