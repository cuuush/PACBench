"""Freeze one authored corpus and run it through native memory providers."""
import argparse
import os
import sys
from functools import partial
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pacbench.corpus.prepare as prepare

from .providers import ACTIVE_PROVIDERS

REPO = Path(__file__).resolve().parents[1]


def corpus_manifest(source):
    manifest = source / 'week.json' if source.is_dir() else source
    if not source.exists() and not source.is_absolute() and len(source.parts) == 1:
        dataset = REPO / 'datasets' / source.name / 'week.json'
        if dataset.is_file():
            return dataset
    return manifest


def use_installed_runtime(argv, parser):
    runtime = REPO / 'vendor/venv-hermes-isolated'
    if Path(sys.prefix).resolve() == runtime.resolve():
        return
    python = runtime / 'bin/python'
    if not os.access(python, os.X_OK):
        parser.error('Install the native provider runtime first with ./scripts/setup.')
    try:
        os.execv(python, [str(python), '-m', 'pacbench', *argv])
    except OSError as exc:
        parser.error(f'Cannot start the installed native provider runtime: {exc.strerror}. Re-run ./scripts/setup.')


def plan_run(manifest, output, providers):
    from pacbench.corpus.audit import open_prepared
    from pacbench.evidence_store import save
    from pacbench.workflows.source_plan import (
        daily_releases,
        validate_evaluation_clocks,
    )

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


def run_from_args(args, parser, *, command_line=None):
    from .corpus.audit import PreparedCorpusError
    from .gateway_client import cli_inference_route

    manifest = corpus_manifest(args.corpus)
    if args.output is None:
        label = manifest.parent.resolve().name if manifest.name == 'week.json' else manifest.stem
        args.output = REPO / 'artifacts/runs' / f'{label or "corpus"}-{uuid4().hex}'
    if os.path.lexists(args.output):
        parser.error(f'output already exists: {args.output}')
    if not manifest.is_file():
        parser.error(f'frozen corpus manifest not found at {manifest}')
    providers = args.providers or list(ACTIVE_PROVIDERS)
    if len(providers) != len(set(providers)):
        parser.error('select each provider only once')
    if args.plan:
        try:
            return plan_run(manifest, args.output, providers)
        except (ValueError, FileExistsError) as exc:
            parser.error(str(exc))

    from pacbench.workflows.native_run import run

    before_start = partial(use_installed_runtime, command_line, parser) if command_line is not None else None
    with cli_inference_route(parser, args.output, args.budget_usd, before_start=before_start) as inference_route:
        try:
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



# Import each command only after selection. Offline commands do not initialize
# a provider, a gateway or a model client.
COMMANDS = {
    ('specs',): ('corpus.prepare', 'specs_main', 'inspect authored workflow specs'),
    ('workflow',): ('workflows.conformance', 'workflow_main', 'plan or exercise a recording workflow'),
    ('controls',): ('judging.paired', 'controls_main', 'render or score saved blind controls'),
    ('costs',): ('usage', 'phase_cost_main', 'summarize costs from retained receipts'),
    ('audit', 'r2'): ('corpus.r2', 'audit_r2_main', 'verify the frozen Week r2 schedule and authored scope'),
    ('audit', 'day'): ('judging.overlays', 'day_review_main', 'inspect an archived Day preparation offline'),
    ('paired',): ('judging.paired', 'paired_main', 'plan or interpret a sealed question-level comparison'),
    ('batch',): ('judging.batch', 'main', 'export and compare retained Week answers offline'),
    ('export',): ('workflows.answer_export', 'main', 'export answers from saved run evidence'),
    ('regrade',): ('judging.overlays', 'main', 'inspect and recover a saved grading overlay'),
    ('fixture',): ('providers.memcal.fixture', 'main', 'exercise a synthetic MemCal connector fixture'),
    ('resume',): ('providers.openviking', 'resume_main', 'resume a verified pre-recall OpenViking run'),
    ('audit', 'prepared'): ('corpus.audit', 'main', 'verify a frozen preparation'),
    ('audit', 'native'): ('native_audit', 'main', 'verify evidence from a native run'),
    ('gateway', 'serve'): ('gateway.server', 'serve_main', 'serve a configured Muse gateway'),
    ('gateway', 'reconcile'): ('gateway.ledger', 'reconcile_main', 'reconcile an existing Muse ledger'),
}


def main(argv=None):
    command_line = argv is None
    argv = list(sys.argv[1:] if command_line else argv)
    parser = argparse.ArgumentParser(prog='python -m pacbench', description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generator = commands.add_parser('generate', help='generate, review and freeze an authored JSON spec')
    prepare.add_arguments(generator)
    native = commands.add_parser('run', help='evaluate a frozen corpus with native providers')
    native.add_argument('corpus', type=Path, help='dataset name, prepared directory or its week.json manifest')
    native.add_argument('--output', type=Path, help='new destination for run evidence (default: under artifacts/runs)')
    native.add_argument('--providers', nargs='+', choices=ACTIVE_PROVIDERS, help='subset of the active providers')
    mode = native.add_mutually_exclusive_group()
    mode.add_argument('--plan', action='store_true', help='verify and summarize the corpus without starting providers or models')
    mode.add_argument('--budget-usd', metavar='AMOUNT',
                      help='start a fresh OpenRouter run with this maximum spend in USD')
    groups = {}
    for words, (module, handler, description) in COMMANDS.items():
        command = words[0]
        parent = commands
        if len(words) == 2:
            if command not in groups:
                group = commands.add_parser(command, help=f'{command} operations')
                groups[command] = group.add_subparsers(dest='operation', required=True)
            parent = groups[command]
        parent.add_parser(words[-1], help=description, add_help=False)
        if tuple(argv[:len(words)]) == words:
            return getattr(import_module(f'.{module}', __package__), handler)(argv[len(words):])
    args = parser.parse_args(argv)
    if args.command == 'generate':
        return prepare.generate_from_args(args, generator)
    return run_from_args(args, native, command_line=argv if command_line else None)


if __name__ == '__main__':
    raise SystemExit(main())
