"""Inspect or exercise the current Pizza Week workflow without providers, model calls or scores."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .workflow_conformance import RecordingAccount
from .workflow_runner import Condition, run_current_week
from .workflow_spec import SPEC, compile_week
from .workflow_tracks import BOTH, PROVIDERS, eligibility


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan','conform','preflight'))
    parser.add_argument('--state', choices=('continuing-account','isolated-control'), default='continuing-account')
    parser.add_argument('--track', choices=('nightly-document','first-party-connector'), required=True)
    parser.add_argument('--seven-daily-documents', action='store_true',
                        help='exercise the frozen one-document-per-creation-day layout offline')
    parser.add_argument('--provider', choices=tuple(PROVIDERS))
    parser.add_argument('--output', type=Path, help='new private evidence directory for conformance')
    parser.add_argument('--site', type=Path, help='explicit authorized synthetic MemCal site for preflight')
    args = parser.parse_args(argv)
    if args.site and args.action != 'preflight':
        parser.error('--site is only used by preflight')
    if (args.action == 'conform') != (args.output is not None):
        parser.error('conform requires a new --output; plan writes only to stdout')
    daily_manifest = None
    if args.seven_daily_documents:
        if args.action == 'preflight' or (args.track, args.state) != ('nightly-document', 'continuing-account'):
            parser.error('seven daily files require the continuing document plan or conformance')
        from .workflow_daily_documents import compile_daily_week

        spec, docs, turns, frozen, daily_manifest = compile_daily_week()
    else:
        spec, docs, turns, frozen = compile_week(args.state)
    if args.action == 'preflight':
        if (args.provider, args.track, args.state) != ('memcal', 'first-party-connector', 'continuing-account'):
            parser.error('preflight requires the continuing-account MemCal first-party connector')
        from .workflow_memcal_contract import source_preflight, verify_contract
        result = source_preflight(verify_contract(docs, turns, frozen), args.site)
        print(json.dumps(result, indent=2))
        return 0 if result['state'] == 'source-access-verified' else 2
    fingerprint = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    result = {'spec':spec['version'],'spec_sha256':fingerprint,'judge':spec['judge'],'source_basis':frozen,
              'track':args.track,'state':args.state,'nightly_files':len(docs),
              'cutoffs':sorted({d.cutoff for d in docs}),
              'turns_by_measure':dict(Counter(t.measure for t in turns)),
              'criteria_by_measure':dict(sum_claims(turns)),
              'prompt_caching':'unmeasured','providers':{}}
    if daily_manifest is not None:
        result.update(source_layout=daily_manifest['source_layout'],
                      document_index_sha256=daily_manifest['normalized_index_sha256'])
    output_created = False
    performed_work = False
    for provider in ([args.provider] if args.provider else PROVIDERS):
        row = eligibility(provider,args.track)
        result['providers'][provider] = row
        covered = tuple(c for c in BOTH if c not in row['missing_channels'])
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
        scoped = docs if daily_manifest is not None else [d for d in docs if d.channel in covered]
        account = RecordingAccount(condition,scoped)
        try:
            scores = run_current_week(condition, account, args.output/provider,
                                      seven_daily_documents=args.seven_daily_documents)
            row.update(offline_recorded_turns=sum(r['answered'] for r in scores.values()), recorded_files=len(account.files))
            if row['recorded_files'] or row['offline_recorded_turns']:
                row['result'] = 'offline-conformance' if covered == BOTH else 'channel-only import conformance'
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
