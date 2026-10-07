#!/usr/bin/env python3
"""Build the simulation explorer from current native trials.

Every run uses its own verified frozen bundle. Encrypted reasoning stays in
private run artifacts and is redacted from the browser data.
"""

import datetime
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pacbench.frozen import bundle_hash  # noqa: E402
from pacbench.frozen import messages_for  # noqa: E402

OUT = ROOT / "explorer" / "data"


def model_record_for_dashboard(value):
    if isinstance(value, dict):
        return {key: ("<encrypted reasoning retained in private run artifact>"
                      if key == "encrypted_content" and item else
                      model_record_for_dashboard(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [model_record_for_dashboard(item) for item in value]
    return value


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def native_run(run_dir):
    summary = read_json(run_dir / 'summary.json') or {}
    if summary.get('mode') != 'native-hermes' and summary.get('state') != 'prepared':
        return None
    bundle = read_json(run_dir / 'bundle.json')
    if not bundle:
        return None
    actual_hash = bundle_hash(bundle)
    if bundle.get('bundle_hash') != actual_hash or summary.get('bundle_hash', actual_hash) != actual_hash:
        raise SystemExit(f'ABORT native bundle mismatch: {run_dir}')
    resolved = read_json(run_dir / 'resolved-spec.json') or {}
    source = Path(summary.get('source', run_dir)).resolve()
    if not source.is_relative_to((ROOT / 'runs').resolve()):
        source = run_dir
    spec = read_json(source / 'spec.json') or resolved
    plan = read_json(run_dir / 'evaluation-plan.json')
    evaluations = [{**row, 'contract': read_json(run_dir / 'evaluations' / row['id'] / 'contract.json')}
                   for row in (plan or {}).get('checks', [])]
    contract = read_json(run_dir / 'contract.json')
    messages = messages_for(bundle, next(iter(bundle['lives'])))
    arms = {}
    for arm in sorted(run_dir.glob('repeat-*/*')):
        if not arm.is_dir() or not (arm / 'hermes-home').exists():
            continue
        repetition = arm.parent.name
        provider = arm.name
        exchanges = []
        def append_exchange(directory, checkpoint_contract, check_id, turn_file):
            turn = read_json(directory / turn_file)
            if not turn:
                return
            recovery = read_json(directory / 'followups/recovery.json')
            exchanges.append({'turn': turn, 'judgment': read_json(directory / 'judgment.json'),
                'grade': read_json(directory / 'grade.json'), 'contract': checkpoint_contract,
                'check_id': check_id, 'kind': 'initial', 'usage': read_json(directory / 'usage.json'), 'status': read_json(directory / 'status.json'),
                'retention': recovery, 'metrics': (recovery or {}).get('initial_metrics')})
            for attempt in (recovery or {}).get('attempts', []):
                selected = attempt['claim_ids']
                follow = directory / 'followups' / 'combined'
                follow_turn = read_json(follow / 'turn.json')
                if not follow_turn:
                    continue
                subset = {**(checkpoint_contract or {}), 'claims': [c for c in (checkpoint_contract or {}).get('claims', []) if c['id'] in selected]}
                exchanges.append({'turn': follow_turn, 'judgment': read_json(follow / 'judgment.json'),
                    'grade': read_json(follow / 'grade.json'), 'contract': subset, 'check_id': check_id,
                    'kind': 'followup', 'question_authoring_reused': attempt.get('question_authoring_reused'), 'metrics': attempt.get('metrics'), 'retention': recovery,
                    'usage': read_json(follow / 'usage.json'), 'question_plan': read_json(follow / 'question-plan.json')})
        if evaluations:
            for row in evaluations:
                append_exchange(arm / 'turns' / row['id'], row['contract'], row['id'], 'turn.json')
        else:
            append_exchange(arm, contract, 'recall', 'recall.json')
        exposures = []
        if summary.get('ingestion_mode')=='documents':
            for row in summary.get('ingestion_schedule',[]):
                record=read_json(arm/row['file'])
                if not record:continue
                packet=[m for m in messages if m['source_id'] in row['source_ids']]
                document=(run_dir/row['source_file']).read_text()
                exposures.append({'label':row['id'],'at':record['at'],'delivery':record['delivery'],
                    'record':record,'usage':record.get('usage'),'packet':packet,
                    'document':document,
                    'packet_verified':record['source_ids']==row['source_ids'] and
                       hashlib.sha256(document.encode()).hexdigest()==record['document_sha256']})
        preflight = read_json(arm / 'preflight.json')
        status = read_json(arm / 'status.json') or {'state': 'running', 'capability': preflight}
        operations = [op for ex in exchanges for op in (ex.get('metrics') or {}).get('operations', [])]
        status['behavior'] = {**status.get('behavior', {}),
            'retrieval_called': any(op.get('category') in ('memory_detail', 'memory_search', 'archive_search', 'source_evidence', 'source_messages') for op in operations)}
        arms[f'{provider}-{repetition}'] = {'provider': provider,
            'label': f'{provider.title()} · {repetition.replace("-", " ")}',
            'preflight': preflight, 'status': status, 'exchanges': exchanges, 'exposures': exposures,
            'model_usage': read_json(arm / 'model-usage.json'),
            'model_usage_after_settlement': read_json(arm / 'model-usage-after-settlement.json'),
            'turn': exchanges[0]['turn'] if exchanges else None}
    records = {}
    for folder in (source, run_dir):
        for path in sorted((folder / 'model-calls').glob('*.json')):
            records[path.name] = {'file': path.name, 'record': model_record_for_dashboard(read_json(path))}
    return {'kind': 'native' if summary.get('mode') else 'prepared', 'id': run_dir.name,
        'spec': spec, 'resolved_spec': resolved, 'summary': summary,
        'audit': read_json(run_dir / 'native-audit.json') or read_json(run_dir / 'run-audit.json'),
        'messages': messages, 'bundle': bundle,
        'bundle_hash': actual_hash, 'contract': contract, 'evaluation_plan': plan,
        'evaluations': evaluations, 'condition': read_json(run_dir / 'condition.json'),
        'scenes': read_json(source / 'scenes.json'), 'arms': arms,
        'verifications': [{'file': p.name, 'record': read_json(p)} for p in sorted((source / 'scene-verifications').glob('*.json'))],
        'model_calls': list(records.values()), 'sort_time': (run_dir / 'summary.json').stat().st_mtime}


def main() -> None:
    dashboard_runs = []
    OUT.mkdir(parents=True, exist_ok=True)
    for run_dir in (ROOT / 'runs').iterdir():
        if run_dir.is_dir():
            native = native_run(run_dir)
            if native:
                dashboard_runs.append(native)
    dashboard_runs.sort(key=lambda r: r.get('sort_time') or (ROOT / 'runs' / r['id']).stat().st_mtime, reverse=True)
    (OUT / "dashboard.json").write_text(json.dumps({
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "runs": dashboard_runs}, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT / 'dashboard.json'} ({len(dashboard_runs)} runs)")


if __name__ == "__main__":
    main()
