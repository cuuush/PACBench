"""Freeze one authored corpus and run it through native memory providers."""
import argparse
import json
import os
from pathlib import Path
import sys

import pacbench.corpus.prepare as prepare
from .providers import ACTIVE_PROVIDERS


def plan_run(manifest, output, providers):
    from .document_ingestion import daily_releases, validate_evaluation_clocks
    from .followups import save
    from pacbench.corpus.audit import open_prepared

    with open_prepared(manifest, portable=True) as corpus:
        messages, spec, corpus_hash = corpus.messages, corpus.resolved, corpus.corpus_hash
        questions = [{'id': row['id'], 'at': row['at'], 'criteria': len(item.claims)}
                     for row, item, _ in corpus.questions]
        chronological = bool(corpus.evaluations)
    zone = spec.get('timezone') or 'America/New_York'
    schedule = [{'id': day, 'at': at, 'messages': len(batch)} for day, at, batch in daily_releases(messages, zone)]
    validate_evaluation_clocks(schedule, providers, [row['at'] for row in questions], zone, chronological=chronological)
    report = {'state': 'planned', 'corpus_hash': corpus_hash, 'selected_providers': providers,
              'message_count': len(messages), 'ingestion_days': len(schedule), 'ingestion_schedule': schedule,
              'questions': questions, 'planned_questions': len(questions),
              'planned_criteria': sum(row['criteria'] for row in questions)}
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'summary.json', report)
    print(f"Planned {len(providers)} providers, {len(schedule)} source days, {len(questions)} questions and "
          f"{report['planned_criteria']} criteria per provider.")
    print(output / 'summary.json')
    return 0


def run_from_args(args, parser):
    from .corpus.audit import PreparedCorpusError
    from .native_pilot import run

    if os.path.lexists(args.output):
        parser.error(f'output already exists: {args.output}')
    manifest = args.corpus / 'week.json' if args.corpus.is_dir() else args.corpus
    if not manifest.is_file():
        parser.error(f'frozen corpus manifest not found at {manifest}')
    providers = args.providers or list(ACTIVE_PROVIDERS)
    if len(providers) != len(set(providers)):
        parser.error('select each provider only once')
    try:
        if args.plan:
            return plan_run(manifest, args.output, providers)
        from .gateway_client import public_inference_route

        try:
            inference_route = public_inference_route()
        except ValueError as exc:
            parser.error(str(exc))
        report = run(manifest, args.output, max_messages=None, providers=providers,
                     inference_route=inference_route, report_blocked=True)
    except PreparedCorpusError as exc:
        parser.error(str(exc))
    results = {row['provider']: row for row in report['results']}
    unstarted = []
    for provider in providers:
        row = results.get(provider)
        if row is None:
            unstarted.append(provider)
            continue
        capability = row['capability']
        reason = row.get('reason') or capability.get('execution_blocker') or capability.get('reason') or ''
        print(provider, row['state'], reason)
    if unstarted:
        fallback = ('the run stopped before this provider started' if len(unstarted) == 1
                    else 'the run stopped before these providers started')
        reason = report.get('reason') or report.get('interruption') or fallback
        print(', '.join(unstarted), 'not run', reason)
    print(args.output / 'summary.json')
    return int(report['state'] != 'diagnostic' or len(results) != len(providers))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in {'generate', 'run', '-h', '--help'}:
        return prepare.main(argv)  # Original bare-spec invocation.

    parser = argparse.ArgumentParser(prog='python -m pacbench', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generator = commands.add_parser('generate', help='generate, review and freeze an authored JSON spec')
    prepare.add_arguments(generator)
    native = commands.add_parser('run', help='evaluate a frozen corpus with native providers')
    native.add_argument('corpus', type=Path, help='prepared directory or its week.json manifest')
    native.add_argument('--output', type=Path, required=True, help='new destination for run evidence')
    native.add_argument('--providers', nargs='+', choices=ACTIVE_PROVIDERS, help='subset of the active providers')
    native.add_argument('--plan', action='store_true', help='verify and summarize the corpus without starting providers or models')
    args = parser.parse_args(argv)
    return prepare.generate_from_args(args, generator) if args.command == 'generate' else run_from_args(args, native)


if __name__ == '__main__':
    raise SystemExit(main())
