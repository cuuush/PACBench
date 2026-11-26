"""Inspect or exercise a frozen workflow without providers, model calls or scores."""
import argparse
from collections import Counter
import json
from pathlib import Path

from .workflows.conformance import RecordingAccount
from pacbench.campaigns.pizza_week import compile_pizza_week
from .workflows.spec import compile_prepared
from .workflows.runner import Condition, run_compiled_workflow
from .workflows.tracks import PROVIDERS, eligibility


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan','conform','preflight'))
    parser.add_argument('--state', choices=('continuing-account','isolated-control'), default='continuing-account')
    parser.add_argument('--track', choices=('nightly-document','first-party-connector'), required=True)
    parser.add_argument('--seven-daily-documents', action='store_true',
                        help='exercise the frozen one-document-per-creation-day layout offline')
    parser.add_argument('--prepared', type=Path, help='frozen preparation directory or week.json (default: Pizza Week)')
    parser.add_argument('--provider', choices=tuple(PROVIDERS))
    parser.add_argument('--output', type=Path, help='new private evidence directory for conformance')
    parser.add_argument('--site', type=Path, help='explicit authorized synthetic MemCal site for preflight')
    args = parser.parse_args(argv)
    if args.site and args.action != 'preflight':
        parser.error('--site is only used by preflight')
    if (args.action == 'conform') != (args.output is not None):
        parser.error('conform requires a new --output; plan writes only to stdout')
    if args.prepared and (args.action == 'preflight' or args.seven_daily_documents):
        parser.error('--prepared supports offline plan and conformance with its declared source layout')
    if args.seven_daily_documents and (
            args.action == 'preflight' or (args.track, args.state) != ('nightly-document', 'continuing-account')):
        parser.error('seven daily files require the continuing document plan or conformance')
    try:
        plan = (compile_prepared(args.prepared, args.state) if args.prepared
                else compile_pizza_week(args.state, seven_daily_documents=args.seven_daily_documents))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.error(str(error))
    docs, turns, frozen = plan.documents, plan.turns, plan.source_basis
    if args.action == 'preflight':
        if (args.provider, args.track, args.state) != ('memcal', 'first-party-connector', 'continuing-account'):
            parser.error('preflight requires the continuing-account MemCal first-party connector')
        from pacbench.providers.memcal.contract import source_preflight, verify_contract
        result = source_preflight(verify_contract(list(docs), list(turns), frozen), args.site)
        print(json.dumps(result, indent=2))
        return 0 if result['state'] == 'source-access-verified' else 2
    result = {'spec':plan.spec['version'],'spec_sha256':plan.spec_sha256,'judge':plan.spec['judge'],'source_basis':frozen,
              'track':args.track,'state':args.state,'nightly_files':len(docs),
              'cutoffs':sorted({d.cutoff for d in docs}),
              'turns_by_measure':dict(Counter(t.measure for t in turns)),
              'criteria_by_measure':dict(sum_claims(turns)),
              'prompt_caching':'unmeasured','providers':{}}
    if plan.layout is not None:
        result.update(source_layout=plan.layout['source_layout'],
                      document_index_sha256=plan.layout['normalized_index_sha256'])
    if plan.offline_only:
        result['source_documents'] = result.pop('nightly_files')
        result['workflow_format'] = plan.spec['format']
    output_created = False
    performed_work = False
    for provider in ([args.provider] if args.provider else PROVIDERS):
        row = eligibility(provider,args.track,plan.channels)
        result['providers'][provider] = row
        covered = tuple(c for c in plan.channels if c not in row['missing_channels'])
        if args.action == 'plan':
            continue
        if not covered:
            row.update(result='conformance-unavailable', recorded_files=0, offline_recorded_turns=0,
                       reason=f'No documented {args.track} route for the generic offline conformance workflow. '
                              + row['boundary'])
            continue
        if not output_created:
            args.output.mkdir(parents=True,mode=0o700,exist_ok=False)
            output_created = True
        condition = Condition(provider,args.track,args.state,'recording frontend','recording sentinel',
                              'in-process','offline recording fake',PROVIDERS[provider].reviewed_version,
                              'no Hermes process','synthetic event clock',(),covered)
        account = RecordingAccount(condition, plan.documents_for(covered))
        try:
            scores = run_compiled_workflow(plan, condition, account, args.output/provider)
            row.update(offline_recorded_turns=sum(r['answered'] for r in scores.values()), recorded_files=len(account.files))
            if row['recorded_files'] or row['offline_recorded_turns']:
                row['result'] = 'offline-conformance' if covered == plan.channels else 'channel-only import conformance'
                performed_work = True
            else:
                row.update(result='conformance-unavailable', reason='Conformance recorded no source files or turns.')
        finally:
            account.close()
    print(json.dumps(result,indent=2))
    return 2 if args.action == 'conform' and not performed_work else 0


def sum_claims(turns):
    counts = Counter()
    for turn in turns:
        counts[turn.measure] += len(turn.claims)
    return counts


if __name__ == '__main__':
    raise SystemExit(main())
