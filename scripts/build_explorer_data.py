#!/usr/bin/env python3
"""Build the simulation explorer from saved native and semantic experiments.

Every run uses its own verified frozen bundle. Encrypted reasoning stays in
private run artifacts and is redacted from the browser data.
"""

import datetime
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pacbench.frozen import bundle_hash  # noqa: E402
from pacbench.frozen import messages_for  # noqa: E402
from pacbench.harness import _recover_tool_results  # noqa: E402
from pacbench.semantic_experiment import tool_behavior  # noqa: E402

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


def exposure_packet(turn, messages, row=None):
    """Map delivered texts only when the saved prompt exactly verifies the mapping."""
    at = turn.get('at', '')
    if row is not None:
        ids = row.get('source_ids', [])
        by_id = {m['source_id']: m for m in messages}
        packet = [by_id[i] for i in ids if i in by_id]
        complete = len(packet) == len(ids) and len(set(ids)) == len(ids)
    else:
        packet = sorted([m for m in messages if m.get('collected_at', '')[:10] == at[:10]],
                        key=lambda m: (m['collected_at'], m['source_id']))
        ids = [m['source_id'] for m in packet]
        complete = bool(packet)
    public = 'Here are my recent messages. Catch up on them for me.\n\n' + '\n'.join(
        f"[{m['written_at']}] {m['channel']} / {m['thread']} / {m['sender']}: {m['text']}" for m in packet)
    expected = f"[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]\n\n{public}"
    verified = complete and turn.get('prompt') == expected
    return {'source_ids': ids, 'packet_verified': verified,
            'packet': packet if verified else [],
            'packet_problem': '' if verified else 'Saved handoff does not verify the expected message mapping. Inspect its exact prompt.'}


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
                combined = 'claim_ids' in attempt
                selected = attempt.get('claim_ids', [attempt.get('claim_id')])
                ids = [c['id'] for c in (checkpoint_contract or {}).get('claims', [])]
                folder = 'combined' if combined else f"fact-{ids.index(selected[0]) + 1}"
                follow = directory / 'followups' / folder
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
        schedule = summary.get('exposure_schedule')
        if schedule:
            paths = [(row['id'], arm / row['file'], row) for row in schedule]
        else:
            paths = [(p.stem, p, None) for p in sorted(arm.glob('exposure-*.json'))]
        for label, path, row in paths:
            turn = read_json(path)
            if turn:
                exposures.append({'label': label, 'turn': turn, 'delivery': 'native', 'usage': read_json(Path(str(path) + '.usage.json')),
                                  **exposure_packet(turn, messages, row)})
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
    for run_dir in sorted((ROOT / "runs").glob("semantic-*"), reverse=True):
        if not run_dir.is_dir():
            continue
        spec = read_json(run_dir / "spec.json")
        if not spec:
            continue
        bundle = read_json(run_dir / "bundle.json")
        provenance = read_json(run_dir / "bundle-provenance.json")
        if bundle is not None:
            actual_hash = bundle_hash(bundle)
            if not provenance or provenance.get("hash") != actual_hash:
                raise SystemExit(f"ABORT semantic bundle mismatch: {run_dir}")
        else:
            actual_hash = None
        rerun_provenance = read_json(run_dir / "rerun-provenance.json")
        contract = read_json(run_dir / "contract.json")
        if rerun_provenance and (rerun_provenance.get("reused_bundle_hash") != actual_hash or
                                 rerun_provenance.get("reused_contract_hash") !=
                                 (contract or {}).get("contract_hash")):
            raise SystemExit(f"ABORT semantic rerun provenance mismatch: {run_dir}")
        life = (bundle or {}).get("lives", {}).get(spec.get("id"), {})
        messages = life.get("messages", life.get("observations", []))
        arms = {}
        for provider in spec.get("providers", ["memcal", "hindsight"]):
            if spec.get("prepare_only") is True:
                continue
            arm = run_dir / provider
            archive_sources = {}
            archive_db = arm / "provider-home" / "memcal.db"
            if provider == "memcal" and archive_db.exists():
                with sqlite3.connect(f"file:{archive_db}?mode=ro", uri=True) as db:
                    archive_sources = {str(row[0]): row[1] for row in
                        db.execute("SELECT id, external_id FROM archive")}
            arms[provider] = {name: read_json(arm / (name + ".json")) for name in
                              ("ingestion", "maintenance", "retrieval", "preflight",
                               "turn", "judgment", "grade", "status")}
            exchanges = []
            for turn_dir in sorted((arm / "turns").glob("*")):
                if turn_dir.is_dir():
                    exchange = {name: read_json(turn_dir / (name + ".json"))
                                for name in ("turn", "judgment", "grade", "status")}
                    if exchange["turn"]:
                        trace = SimpleNamespace(
                            session_id=exchange["turn"].get("session_id"),
                            tool_results=exchange["turn"].get("tool_results", []))
                        if not all(result.get("capture") == "hermes_session_db"
                                   for result in trace.tool_results):
                            _recover_tool_results(trace, arm / "hermes-home" / "state.db")
                    exchange["day"] = turn_dir.name
                    exchange["contract"] = read_json(run_dir / "contracts" /
                                                     (turn_dir.name + ".json"))
                    exchanges.append(exchange)
            if not exchanges and arms[provider]["turn"]:
                exchanges = [{name: arms[provider][name]
                              for name in ("turn", "judgment", "grade")}]
            arms[provider]["exchanges"] = exchanges
            arms[provider]["label"] = provider.title()
            arms[provider]["provider"] = provider
            daily = sorted(arm.glob("ingestion-*.json"))
            records = [read_json(p) for p in daily] if daily else []
            if not records and arms[provider]["ingestion"]:
                records = arms[provider]["ingestion"].get("daily_batches") or [arms[provider]["ingestion"]]
            arms[provider]["exposures"] = [{"label": r.get("day", str(i + 1)),
                "at": r.get("at"), "delivery": "adapter", "record": r,
                "packet": r.get("packet") or [m for m in messages if m["source_id"] in r.get("source_ids", [])],
                "source_ids": r.get("source_ids", []), "packet_verified": None}
                for i, r in enumerate(records) if r]
            # Older semantic runs predate the separate capability/behavior
            # fields. Derive only what the saved trace actually demonstrates.
            old_status = arms[provider]["status"]
            if old_status is not None and arms[provider]["turn"]:
                turn = arms[provider]["turn"]
                derived = tool_behavior(provider, SimpleNamespace(
                    tool_calls=turn.get("tool_calls", []),
                    tool_results=turn.get("tool_results", []),
                    policy_blocks=turn.get("policy_blocks", 0)))
                derived["basis"] = "derived from saved turn"
                recorded = old_status.get("behavior")
                if recorded and any(recorded.get(k) != derived.get(k) for k in
                                    ("provider_tool_attempts", "provider_tool_calls",
                                     "blocked_provider_calls", "retrieval_called")):
                    old_status["behavior_disagreement"] = recorded
                old_status["behavior"] = derived
            if old_status is not None and "capability" not in old_status and arms[provider]["preflight"]:
                pf = arms[provider]["preflight"]
                available = (bool(pf.get("plugin_installed")) if provider == "memcal"
                             else all(pf.get(k) for k in
                                      ("sdk", "connected", "allowed", "recall_reached_bank")))
                old_status["capability"] = {"available": available,
                                            "basis": "derived from saved preflight"}
            arms[provider]["archive_sources"] = archive_sources
        model_calls = []
        for path in sorted((run_dir / "model-calls").glob("*.json")):
            model_calls.append({"file": path.name,
                                "record": model_record_for_dashboard(read_json(path))})
        verifications = []
        for path in sorted((run_dir / "scene-verifications").glob("*.json")):
            verifications.append({"file": path.name, "record": read_json(path)})
        calibrations = []
        for report_path in sorted(run_dir.glob("calibration*/report.json")):
            folder = report_path.parent
            calibrations.append({
                "name": folder.name,
                "report": read_json(report_path),
                "cases": {path.stem: read_json(path) for path in folder.glob("*.json")
                          if path.name != "report.json"},
            })
        dashboard_runs.append({"kind": "semantic", "id": run_dir.name,
                               "spec": spec, "summary": read_json(run_dir / "summary.json"),
                               "resolved_spec": read_json(run_dir / "resolved-spec.json"),
                               "experiment_status": read_json(run_dir / "experiment-status.json"),
                               "audit": read_json(run_dir / "run-audit.json"),
                               "bundle": bundle, "bundle_hash": actual_hash,
                               "provenance": provenance, "messages": messages,
                               "rerun_provenance": rerun_provenance,
                               "scenes": read_json(run_dir / "scenes.json"),
                               "contract": contract,
                               "daily_facts": {p.stem: read_json(p) for p in
                                               sorted((run_dir / "daily-facts").glob("*.json"))
                                               if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.stem)},
                               "verifications": verifications, "model_calls": model_calls,
                               "calibrations": calibrations,
                               "arms": arms})
    OUT.mkdir(parents=True, exist_ok=True)
    indexed = {r['id'] for r in dashboard_runs}
    for run_dir in (ROOT / 'runs').iterdir():
        if run_dir.is_dir() and run_dir.name not in indexed:
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
