"""Offline authoring pilot. Run with python -m pacbench.spec_first_pilot.

Use --emit cases or --emit key for the two separate judge_blind_controls inputs.

The synthetic sources are timestamped form entries. A decline requires an
explicit reply for that actor, event and role. Only the required field compiles
to a scored claim. Control labels come from edits to typed values.
"""
import argparse
from dataclasses import asdict, dataclass
from enum import StrEnum
import json

from .frozen import bundle_hash
from .prepare import read_spec, resolve_spec
from .semantic_contract import ContractClaim
from .workflow_spec import ROOT, compile_week, instant

FIXTURES = ROOT / 'tests/fixtures'
SPEC = FIXTURES / 'spec_first_role_reply_v1.json'
EXCEPTION = FIXTURES / 'spec_first_rsvp_scope_v1.json'


class Reply(StrEnum):
    ACCEPTED = 'accepted'
    DECLINED = 'declined'


@dataclass(frozen=True)
class RoleReply:
    role: str
    reply: Reply
    evidence: str


@dataclass(frozen=True)
class Availability:
    available: bool
    evidence: str


@dataclass(frozen=True)
class RoleQuestion:
    id: str
    actor: str
    event: str
    as_of: str
    required: RoleReply
    optional: tuple[Availability | RoleReply, ...]

    @property
    def prompt(self):
        return f'Did {self.actor} accept the {self.required.role} shift at {self.event}?'


@dataclass(frozen=True)
class VolunteerForm:
    """One direct reply. Abilities, offers and declines are separate authored fields."""
    id: str
    actor: str
    recipient: str
    event: str
    written_at: str
    received_at: str
    reported_unable: frozenset[str] = frozenset()
    offered: frozenset[str] = frozenset()
    declined: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DeclinedEveryRole:
    """A communicated decision covering an explicitly exhaustive list of jobs."""
    actor: str
    recipient: str
    event: str
    roles: tuple[str, ...]
    as_of: str
    source_id: str

    @property
    def prompt(self):
        return f'What did {self.actor} tell {self.recipient} about helping with {self.event}?'

    @property
    def claim(self):
        statement = (f'{self.actor} told {self.recipient} they declined every job for {self.event} '
                     f'({", ".join(self.roles)}).')
        return ContractClaim('all-roles-declined', statement, (self.source_id,))

    def check_source(self, form: VolunteerForm):
        roles = set(self.roles)
        if not roles or len(roles) != len(self.roles) or any(not role.strip() for role in roles):
            raise ValueError('the question must enumerate distinct named jobs')
        received = instant(form.received_at)
        if instant(form.written_at) > received:
            raise ValueError('a reply cannot arrive before it was written')
        if ((form.id, form.actor, form.recipient, form.event) !=
                (self.source_id, self.actor, self.recipient, self.event) or received > instant(self.as_of)):
            return 'insufficient'
        if not (form.reported_unable | form.offered | form.declined) <= roles or form.offered & form.declined:
            raise ValueError('reply contains an unlisted job or conflicting decisions for one job')
        if form.offered:
            return 'contradicted'
        if form.declined == roles:
            return 'supported'
        return 'insufficient'


def _require_record(records, question, scope, value_field, expected, citation):
    """Verify the cited form entry is the current explicit value at the cutoff."""
    cutoff = instant(question.as_of)
    target = dict(scope, actor=question.actor, event=question.event)
    available = []
    for row in records:
        if all(row.get(key) == value for key, value in target.items()):
            at = instant(row['at'])
            if at <= cutoff:
                available.append((at, row))
    if not available:
        raise ValueError('no in-scope evidence at the question cutoff')
    latest = max(at for at, _ in available)
    current = [row for at, row in available if at == latest]
    if len(current) != 1 or current[0]['id'] != citation:
        raise ValueError('citation must identify the unique current entry at the cutoff')
    actual = current[0].get(value_field)
    if type(actual) is not type(expected) or actual != expected:
        raise ValueError(f'cited record does not establish {value_field}')


def _role_reply(role, reply, evidence):
    return RoleReply(role, Reply(reply), evidence)


def compile_role(spec):
    if spec['revision'] != 'role-reply-v1':
        raise ValueError('unknown authoring pilot revision')
    fields = dict(spec['question'])
    required = _role_reply(**fields.pop('required'))
    extra = fields.pop('optional', [])
    if not isinstance(extra, list):
        raise ValueError('optional fields must be a list')
    optional = tuple(Availability(**f) if 'available' in f else _role_reply(**f) for f in extra)
    q = RoleQuestion(**fields, required=required, optional=optional)
    if not all(isinstance(v, str) and v.strip() for v in (q.id, q.actor, q.event)):
        raise ValueError('question, actor and event must be named')
    records = spec['records']
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('duplicate record ID')
    seen = set()
    for field in (q.required, *q.optional):
        if not isinstance(field.evidence, str) or not field.evidence.strip():
            raise ValueError('field needs a named evidence record')
        if isinstance(field, RoleReply):
            if not isinstance(field.role, str) or not field.role.strip():
                raise ValueError('a role reply needs a named role')
            scope = {'kind': 'role-reply', 'role': field.role}
            _require_record(records, q, scope, 'reply', field.reply.value, field.evidence)
        else:
            if type(field.available) is not bool:
                raise ValueError('availability must be a boolean')
            scope = {'kind': 'availability'}
            _require_record(records, q, scope, 'available', field.available, field.evidence)
        slot = frozenset(scope.items())
        if slot in seen:
            raise ValueError('required and optional fields must have distinct scopes')
        seen.add(slot)
    statement = f'{q.actor} {q.required.reply} the {q.required.role} shift at {q.event}.'
    claim = ContractClaim('role-reply', statement, (q.required.evidence,))
    return q, claim


def controls(spec):
    """Return existing judge_blind_controls case format and a separate expected key."""
    q, claim = compile_role(spec)
    opposite = Reply.DECLINED if q.required.reply == Reply.ACCEPTED else Reply.ACCEPTED
    verbs = {Reply.DECLINED: 'passed on', Reply.ACCEPTED: 'signed on for'}
    contexts = [f'The staffing sheet is up for {q.event}.']
    for field in q.optional:
        if isinstance(field, RoleReply):
            contexts.append(f'{q.actor} {field.reply} the {field.role} shift at {q.event}.')
        else:
            availability = 'is available for' if field.available else 'is unavailable for'
            contexts.append(f'{q.actor} {availability} {q.event}.')
    cases, expected = [], {}
    for context in contexts:
        for reply, verdict in ((q.required.reply, 'correct'), (None, 'missed'), (opposite, 'misrepresented')):
            answer = context
            if reply is not None:
                answer += f' {q.actor} {verbs[reply]} the {q.required.role} shift.'
            ident = bundle_hash({'question_id': q.id, 'answer': answer})[:16]
            cases.append(dict(id=ident, at=q.as_of, question=q.prompt, answer=answer,
                              claims=[(claim.id, claim.statement, claim.evidence)]))
            expected[ident] = [verdict]
    return sorted(cases, key=lambda case: case['id']), expected


def authored_questions():
    path = ROOT / 'specs/pizza_week_assistant.json'
    _, _, resolved = resolve_spec(read_spec(path), path.with_suffix(''))
    return resolved['checks']


def scope_pair(authored, turn, dataset, track):
    a = dict(id=authored['id'], at=authored['at'], question=authored['prompt'], facts=authored['facts'])
    if 'expect' in authored:
        a['expect'] = authored['expect']
    s = dict(dataset=dataset, track=track, id=turn.id, at=turn.at, question=turn.prompt,
             claims=[asdict(c) for c in turn.claims])
    return a, s


def historical_rsvp_scopes():
    track = 'continuing-account'
    workflow, _, turns, _ = compile_week(track)
    authored = next(q for q in authored_questions() if q['id'] == 'rsvp-before')
    turn = next(t for t in turns if t.id == authored['id'])
    return scope_pair(authored, turn, workflow['dataset'], track)


def audit_scope(authored, scored, exception=None):
    facts = authored['facts']
    statements = [claim['description'] for claim in scored['claims']]
    differing = [field for field in ('id', 'at', 'question') if authored[field] != scored[field]]
    if facts != statements:
        differing.append('criteria')
    if not differing and exception is None:
        return dict(status='aligned', differences=[])
    # Bind both complete projections, including ordered claims and evidence.
    drift_sha = bundle_hash({'authored': authored, 'scored': scored})
    if (not differing or not exception or not exception.get('reason', '').strip()
            or exception.get('revision') not in ('rsvp-existing-scope-v1', 'pizza-week-existing-scope-v1')
            or exception.get('question_id') != authored['id'] or exception.get('scope_sha256') != drift_sha):
        detail = ', '.join(differing) or 'obsolete exception'
        raise ValueError(f'unrecognized scope drift on {authored["id"]}: {detail} ({drift_sha})')
    return dict(status='existing-scope exception', differences=differing, reason=exception['reason'],
                authored_question=authored['question'], scored_question=scored['question'],
                authored_facts=len(facts), scored_claims=len(statements),
                authored_only=[s for s in facts if s not in statements],
                scored_only=[s for s in statements if s not in facts])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emit', choices=('report', 'cases', 'key'), default='report')
    args = parser.parse_args()
    spec = read_spec(SPEC)
    cases, expected = controls(spec)
    if args.emit == 'report':
        q, claim = compile_role(spec)
        audit = audit_scope(*historical_rsvp_scopes(), read_spec(EXCEPTION))
        result = dict(revision=spec['revision'], question=q.prompt, as_of=q.as_of, claim=asdict(claim),
                      control_count=len(cases), historical_rsvp=audit)
    else:
        result = cases if args.emit == 'cases' else {'verdicts': expected}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
