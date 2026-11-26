"""Offline authoring audit of the historical Pizza Week r2 contract.

Run python -m pacbench.week_spec_audit for the per-turn report. The refund
example also exports --emit cases and --emit key for judge_blind_controls.
"""
import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from enum import StrEnum
import json

from pacbench.corpus.sources import bundle_hash
from pacbench.campaigns.pizza_week import checked_file
from pacbench.corpus.prepare import read_spec
from .spec_first_pilot import EXCEPTION, FIXTURES, audit_scope, authored_questions, scope_pair
from pacbench.providers.memcal.contract import member_digest, verify_contract
from pacbench.campaigns.pizza_week import ROOT, compile_week
from .workflows.spec import instant

PRIMARY = 'continuing-account'
CONTROL = 'isolated-control'
AUDIT_REVISION = 'pizza-week-authoring-audit-v1'
REGISTRY = FIXTURES / 'pizza_week_authoring_audit_v1.json'
DATASET = ROOT / 'datasets/pizza-week/r2'
WORKFLOW_MANIFEST = 'c15fd1b408738d448d3724ec668de93e1f0051e5a8c47c61b15e145d86f74496'


def compare_questions(questions, workflow, turns, control_turns, registry, rsvp):
    """Compare original authored rows, before the r2 loader substitutes frozen facts."""
    if registry['revision'] != AUDIT_REVISION:
        raise ValueError('unknown authoring audit revision')
    exceptions = dict(registry['fact_list_exceptions'])
    if rsvp['question_id'] in exceptions:
        raise ValueError('duplicate RSVP exception')
    exceptions[rsvp['question_id']] = rsvp
    prose = dict(registry['expect_contracts'])
    authored = {q['id']: q for q in questions}
    controls = {t.id: t for t in control_turns}
    primary = {t.id: t for t in turns}
    extra = {q['id']: q for q in workflow['additional_turns']}
    isolated_only = set(workflow['isolated_controls'])
    expected_primary = (authored.keys() - isolated_only) | extra.keys()
    if (len(authored) != len(questions) or len(primary) != len(turns) or len(controls) != len(control_turns)
            or len(extra) != len(workflow['additional_turns']) or extra.keys() & authored.keys()
            or authored.keys() != controls.keys() or not isolated_only <= controls.keys()
            or primary.keys() != expected_primary):
        raise ValueError('turn inventory differs between authored, primary and isolated schedules')

    rows = []
    for t in [*turns, *(t for t in control_turns if t.id in isolated_only)]:
        track = CONTROL if t.id in isolated_only else PRIMARY
        row = dict(id=t.id, track=track, at=t.at, measure=t.measure, scored_claims=len(t.claims))
        if t.id in extra:
            declaration = extra[t.id]
            row.update(basis='workflow', after=declaration['after'])
            if 'source_claims' in declaration:
                row['source_claims'] = declaration['source_claims']
            if 'user_claim' in declaration:
                row['source_turn'] = declaration['user_claim']['source_turn']
        else:
            a, s = scope_pair(authored[t.id], t, workflow['dataset'], track)
            if a['facts']:
                exception = exceptions.pop(t.id, None)
                result = audit_scope(a, s, exception)
                row.update(basis='explicit-facts', authored_facts=len(a['facts']), differences=result['differences'])
                if exception:
                    row['exception'] = exception
            else:
                entry = prose.pop(t.id, None)
                if (not a.get('expect') or not entry or not entry.get('reason', '').strip()
                        or bundle_hash({'authored': a, 'scored': s}) != entry['scope_sha256']):
                    raise ValueError(f'unregistered prose expectation or changed r2 contract: {t.id}')
                row.update(basis='prose-expectation', historical_scope=entry['reason'])
        rows.append(row)
    if exceptions or prose:
        raise ValueError(f'unused scope entries: {sorted(exceptions.keys() | prose.keys())}')
    return rows


class RefundStatus(StrEnum):
    ISSUED = 'issued'
    PENDING = 'pending'


@dataclass(frozen=True)
class ReportedRefund:
    booking: str
    cancellation_day: str
    reported_status: RefundStatus
    source: str


# Reviewed sidecar for one public r2 claim. The speaker reports issuance.
# Funds arriving in the user's account would be a different claim.
REFUND = ReportedRefund('pottery', 'Wednesday October 7', RefundStatus.ISSUED,
                        'message-2bdf115e8eb602900aba9851')


def refund_controls(turn, documents):
    fact = REFUND
    if turn.id != 'refund' or len(turn.claims) != 1:
        raise ValueError('refund example needs the historical single-claim question')
    claim, = turn.claims
    statement = f'The {fact.booking} refund is reported as {fact.reported_status}.'
    if (claim.id, claim.description, claim.evidence) != ('fact-1', statement, (fact.source,)):
        raise ValueError('typed refund differs from the frozen claim')
    body = f'Your refund for the {fact.booking} class cancelled {fact.cancellation_day} has been {fact.reported_status}.'
    records = [(d.cutoff, text) for d in documents for source, text in zip(d.source_ids, d.bodies, strict=True)
               if source == fact.source]
    if len(records) != 1 or instant(records[0][0]) > instant(turn.at) or records[0][1] != body:
        raise ValueError('typed refund needs its exact cited report available at the cutoff')
    cases, key = [], {}
    phrasing = {RefundStatus.ISSUED: ' They confirm they have issued your refund.',
                None: '', RefundStatus.PENDING: ' They say your refund is still pending and has yet to be issued.'}
    other_status, = set(RefundStatus) - {fact.reported_status}
    for status, verdict in ((fact.reported_status, 'correct'), (None, 'missed'), (other_status, 'misrepresented')):
        answer = f'The {fact.booking} class was cancelled.' + phrasing[status]
        ident = bundle_hash({'question_id': turn.id, 'answer': answer})[:16]
        cases.append(dict(id=ident, at=turn.at, question=turn.prompt, answer=answer,
                          claims=[(claim.id, claim.description, claim.evidence)]))
        key[ident] = [verdict]
    return sorted(cases, key=lambda c: c['id']), {'verdicts': key}


def audit_week():
    archive = json.loads(checked_file(DATASET, 'workflow-v2.json', WORKFLOW_MANIFEST))
    pinned_workflow = json.loads(checked_file(ROOT, archive['spec'], archive['spec_sha256']))
    workflow, docs, turns, source = compile_week(PRIMARY)
    if workflow != pinned_workflow:
        raise ValueError('compiled workflow declarations differ from the archived spec')
    _, _, isolated, _ = compile_week(CONTROL)
    rows = compare_questions(authored_questions(), workflow, turns, isolated, read_spec(REGISTRY), read_spec(EXCEPTION))
    # The archive serialization binds IDs, wording, evidence order, clocks, sessions
    # and parentage even where authored and compiled prose change together.
    frozen = verify_contract(docs, turns, source)
    if frozen['contract_sha256'] != archive['schedules'][PRIMARY]['sha256']:
        raise ValueError('primary schedule differs from the frozen r2 contract')
    if member_digest([asdict(t) for t in isolated]) != archive['schedules'][CONTROL]['sha256']:
        raise ValueError('isolated-control schedule differs from the frozen r2 contract')
    primary_rows = [r for r in rows if r['track'] == PRIMARY]
    primary_by_basis = Counter(r['basis'] for r in primary_rows)
    summary = dict(authored_fact_lists=sum(r['basis'] == 'explicit-facts' for r in rows),
                   primary_turns_by_basis=dict(primary_by_basis),
                   primary_claims_by_basis={k: sum(r['scored_claims'] for r in primary_rows if r['basis'] == k)
                                            for k in primary_by_basis},
                   isolated_only=[r['id'] for r in rows if r['track'] == CONTROL],
                   scope_exceptions=[r['id'] for r in rows if 'exception' in r])
    refund = next(t for t in turns if t.id == 'refund')
    cases, key = refund_controls(refund, docs)
    report = dict(revision=AUDIT_REVISION, dataset=workflow['dataset'], summary=summary, frozen_primary=frozen, turns=rows,
                  comparison='Text comparisons check wording and order. Semantic entailment requires evidence.',
                  typed_example=dict(turn=refund.id, claim=refund.claims[0].id, as_of=refund.at,
                                     reported_status=REFUND.reported_status, control_count=len(cases)))
    return report, cases, key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--emit', choices=('report', 'cases', 'key'), default='report')
    args = parser.parse_args()
    report, cases, key = audit_week()
    print(json.dumps({'report': report, 'cases': cases, 'key': key}[args.emit], indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
