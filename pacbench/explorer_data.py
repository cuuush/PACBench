"""Read browser-visible evidence directly from runs/, without a snapshot or cache."""
from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import quote

from .frozen import bundle_hash, messages_for
from .model_calls import public_record


def read_json(path: Path):
    from .evidence_store import read_json as read_evidence
    return read_evidence(path) if path.exists() else None


def native_run(run_dir: Path, runs_dir: Path, *, include_preflight=True):
    summary = read_json(run_dir / 'summary.json') or {}
    if summary.get('mode') != 'native-hermes' and summary.get('state') != 'prepared':
        return None
    bundle = read_json(run_dir / 'bundle.json')
    if not bundle:
        return None
    actual_hash = bundle_hash(bundle)
    if bundle.get('bundle_hash') != actual_hash or summary.get('bundle_hash', actual_hash) != actual_hash:
        raise ValueError(f'native bundle mismatch: {run_dir.name}')
    resolved = read_json(run_dir / 'resolved-spec.json') or {}
    source = (run_dir / summary.get('source', '.')).resolve()
    if not source.is_relative_to(runs_dir.resolve()):
        raise ValueError('preparation source must stay inside runs/')
    spec = read_json(source / 'spec.json') or resolved
    plan = read_json(run_dir / 'evaluation-plan.json')
    evaluations = [{**row, 'contract': read_json(run_dir / 'evaluations' / row['id'] / 'contract.json')}
                   for row in (plan or {}).get('checks', [])]
    contract = read_json(run_dir / 'contract.json')
    messages = messages_for(bundle, next(iter(bundle['lives'])))
    source_by_id = {m['source_id']:m for m in messages}
    arms, excluded_arms = {}, {}
    candidates=[(arm,None) for arm in sorted(run_dir.glob('repeat-*/*'))]
    for excluded in summary.get('other_attempts', []):
        arm=(run_dir/excluded['evidence']).resolve()
        if not arm.is_relative_to(run_dir.resolve()):raise ValueError('attempt evidence must stay inside run')
        candidates.append((arm,excluded))
    for arm, excluded in candidates:
        if not arm.is_dir():
            continue
        repetition = arm.parent.name
        provider = arm.name
        model_usage=read_json(arm/'model-usage.json')
        settled_usage=read_json(arm/'model-usage-after-settlement.json')
        exchanges = []
        def append_exchange(directory, checkpoint_contract, check_id, turn_file):
            turn = read_json(directory / turn_file)
            if not turn:
                return
            recovery = read_json(directory / 'followups/recovery.json')
            behavior = read_json(directory / 'behavior.json')
            from urllib.parse import urlencode
            def record_url(path):
                relative = path.relative_to(runs_dir)
                return '/record?' + urlencode({'run': relative.parts[0], 'file': Path(*relative.parts[1:]).as_posix()})
            exchanges.append({'turn_record_url': record_url(directory / turn_file), 'judgment_record_url': record_url(directory / 'judgment.json'), 'turn': turn, 'judgment': read_json(directory / 'judgment.json'),
                'grade': read_json(directory / 'grade.json'), 'contract': checkpoint_contract,
                'judge_review': read_json(run_dir / 'judge-scope-review' / f'{provider}-{check_id}.json'),
                'check_id': check_id, 'kind': 'initial', 'usage': read_json(directory / 'usage.json'), 'status': read_json(directory / 'status.json'),
                'retention': recovery, 'metrics': (recovery or behavior or {}).get('initial_metrics'),
                'session': (read_json(directory / 'status.json') or {}).get('session'),
                'source_state': read_json(directory / 'source-state.json')})
            for attempt in (recovery or {}).get('attempts', []):
                selected = attempt['claim_ids']
                follow = directory / 'followups' / 'combined'
                follow_turn = read_json(follow / 'turn.json')
                if not follow_turn:
                    continue
                subset = {**(checkpoint_contract or {}), 'claims': [c for c in (checkpoint_contract or {}).get('claims', []) if c['id'] in selected]}
                exchanges.append({'turn_record_url': record_url(follow / 'turn.json'), 'judgment_record_url': record_url(follow / 'judgment.json'), 'turn': follow_turn, 'judgment': read_json(follow / 'judgment.json'),
                    'grade': read_json(follow / 'grade.json'), 'contract': subset, 'check_id': check_id,
                    'kind': 'followup', 'question_authoring_reused': attempt.get('question_authoring_reused'), 'metrics': attempt.get('metrics'), 'retention': recovery,
                    'usage': read_json(follow / 'usage.json'), 'question_plan': read_json(follow / 'question-plan.json')})
        if evaluations:
            for row in evaluations:
                append_exchange(arm / 'turns' / row['id'], row['contract'], row['id'], 'turn.json')
        else:
            append_exchange(arm, contract, 'recall', 'recall.json')
        exposures = []
        if summary.get('ingestion_mode')=='documents' and excluded is None:
            for row in summary.get('ingestion_schedule',[]):
                record=read_json(arm/row['file'])
                if not record:continue
                packet=[source_by_id[source_id] for source_id in row['source_ids']]
                document=(run_dir/row['source_file']).read_text()
                exposures.append({'label':row['id'],'at':record['at'],'delivery':record['delivery'],
                    'record':record,'usage':record.get('usage'),'packet':packet,
                    'document':document,
                    'packet_verified':record['source_ids']==row['source_ids'] and
                       hashlib.sha256(document.encode()).hexdigest()==record['document_sha256']})
        from .usage import refresh_window
        for item in exchanges+exposures:
            item['usage_after_settlement']=refresh_window(item.get('usage'),settled_usage or model_usage)
        preflight = read_json(arm / 'preflight.json')
        status = read_json(arm / 'status.json') or {'state': 'running', 'capability': preflight}
        operations = [op for ex in exchanges for op in (ex.get('metrics') or {}).get('operations', [])]
        status['behavior'] = {**status.get('behavior', {}),
            'retrieval_called': any(op.get('category') in ('memory_detail', 'memory_search', 'archive_search', 'source_evidence', 'source_messages') for op in operations)}
        collection=excluded_arms if excluded is not None else arms
        key=excluded['evidence'] if excluded is not None else f'{provider}-{repetition}'
        collection[key] = {'provider': provider,
            'label': (f'{provider.title()} · excluded attempt · '+Path(excluded['original_root']).name
                      if excluded is not None else f'{provider.title()} · {repetition.replace("-", " ")}'),
            'exclusion':excluded,
            'configuration_review':read_json(arm/'configuration-review.json'),
            'preflight': preflight, 'status': status, 'exchanges': exchanges, 'exposures': exposures,
            'tool_policy': read_json(arm / 'tool-policy.json'),
            'model_usage': model_usage,
            'model_usage_after_settlement': settled_usage,
            'turn': exchanges[0]['turn'] if exchanges else None}
        collection[key]['evaluation'] = read_json(arm / 'evaluation.json')
        if collection[key]['evaluation']:
            for item in collection[key]['evaluation'].get('checks', []):
                receipt = read_json(arm / 'turns' / item['id'] / 'write-readiness.json')
                if receipt:
                    observed = receipt.get('observed') or {}
                    snapshots = observed.get('snapshots') or []
                    states = snapshots[-1] if snapshots and isinstance(snapshots[-1], list) else []
                    item['write_readiness'] = {'state': receipt.get('state'), 'dependencies': receipt.get('dependencies', []),
                        'accepted_ids': (receipt.get('inputs') or {}).get('accepted_ids', []),
                        'latency_s': receipt.get('latency_s'), 'ready': observed.get('ready'),
                        'reason': observed.get('reason'), 'native_states': states,
                        'record_url': '/record?' + __import__('urllib.parse', fromlist=['urlencode']).urlencode({
                            'run':(arm / 'turns' / item['id'] / 'write-readiness.json').relative_to(runs_dir).parts[0],
                            'file':Path(*(arm / 'turns' / item['id'] / 'write-readiness.json').relative_to(runs_dir).parts[1:]).as_posix()})}

    records = {}
    components=summary.get('component_runs',[])
    folders=[source,run_dir,run_dir/'judge-scope-review']
    for component in components:
        folder=(run_dir/component['public_root']).resolve()
        if not folder.is_relative_to(run_dir.resolve()):raise ValueError('component records must stay inside run')
        folders.extend((folder,folder/'judge-scope-review'))
    for folder in folders:
        for path in sorted((folder / 'model-calls').glob('*.json')):
            if path.is_symlink() or (folder/'model-calls').is_symlink():continue
            relative=(str(path.resolve().relative_to(run_dir.resolve()))
                      if path.resolve().is_relative_to(run_dir.resolve()) else 'preparation/model-calls/'+path.name)
            aliases=[str(path)]
            for component in components:
                public=(run_dir/component['public_root']).resolve()
                if path.resolve().is_relative_to(public):
                    aliases.append(str(Path(component['original_root'])/path.resolve().relative_to(public)))
            records[relative] = {'file':relative,'aliases':aliases,'record':public_record(read_json(path))}
    captured_times = []
    for arm in arms.values():
        try:
            captured = datetime.datetime.fromisoformat((arm.get('model_usage') or {})['at'])
            if captured.tzinfo is not None:
                captured_times.append(captured.timestamp())
        except (KeyError, TypeError, ValueError):
            pass
    return {'kind': 'native' if summary.get('mode') else 'prepared', 'id': run_dir.name,
        'spec': spec, 'resolved_spec': resolved, 'summary': summary,
        'audit': read_json(run_dir / 'native-audit.json') or read_json(run_dir / 'run-audit.json'),
        'messages': messages, 'bundle': bundle,
        'bundle_hash': actual_hash, 'contract': contract, 'evaluation_plan': plan,
        'evaluations': evaluations, 'condition': read_json(run_dir / 'condition.json'),
        'cost_review': read_json(run_dir / 'cost-comparison.json'),
        'development_review': read_json(run_dir / 'development-review.json'),
        'failure_review': read_json(run_dir / 'failure-review.json'),
        'preflight':(native_run(run_dir/'preflight',runs_dir,include_preflight=False)
                     if include_preflight and (run_dir/'preflight/summary.json').exists() else None),
        'scenes': read_json(source / 'scenes.json'), 'arms': arms, 'excluded_arms':excluded_arms,
        'verifications': [{'file': p.name, 'record': read_json(p)} for p in sorted((source / 'scene-verifications').glob('*.json'))],
        'model_calls': list(records.values()),
        'sort_time': max(captured_times, default=(run_dir / 'summary.json').stat().st_mtime)}


def run_index(root: Path) -> dict:
    """List saved runs without loading corpora, transcripts, or model requests."""
    runs=[];errors=[];directory=root/'runs'
    for path in directory.iterdir() if directory.is_dir() else []:
        if not path.is_dir() or path.is_symlink():continue
        try:
            summary=read_json(path/'summary.json') or {}
            if summary.get('mode')!='native-hermes' and summary.get('state')!='prepared':continue
            spec=read_json(path/'resolved-spec.json') or {}
            times=[]
            for arm in path.glob('repeat-*/*'):
                for name in ('model-usage-after-settlement.json','model-usage.json'):
                    usage=read_json(arm/name) or {}
                    if usage.get('at'):
                        stamp=datetime.datetime.fromisoformat(usage['at'])
                        if stamp.tzinfo:times.append(stamp.timestamp())
                        break
            runs.append({'id':path.name,'spec':{'id':spec.get('id',path.name)},
                         'summary':{'state':summary.get('state')},
                         'sort_time':max(times,default=(path/'summary.json').stat().st_mtime),
                         'detail_url':'/run?run='+quote(path.name)})
        except (OSError,ValueError,TypeError) as exc:errors.append({'run':path.name,'error':str(exc)})
    runs.sort(key=lambda item:item['sort_time'],reverse=True)
    return {'runs':runs,'errors':errors,'generated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}


def load_runs(root: Path, *, include_model_records=True, run_id=None) -> dict:
    runs_dir = root / 'runs'
    runs = []
    errors = []
    for run_dir in (runs_dir.iterdir() if runs_dir.is_dir() else []):
        if run_id is not None and run_dir.name!=run_id:continue
        if not run_dir.is_dir() or run_dir.is_symlink():
            continue
        try:
            run = native_run(run_dir, runs_dir)
            if run:
                if not include_model_records:
                    from urllib.parse import urlencode
                    def index_records(item,bundle_file='bundle.json'):
                        # The source and contracts already exist once at run
                        # level. Keep references in provider views, not copies.
                        item['source_documents']={}
                        batch_counts={b['id']:len(b['source_ids']) for b in item['summary'].get('ingestion_schedule',[])}
                        for contract in [item.get('contract'),*[e.get('contract') for e in item.get('evaluations',[])]]:
                            if contract:contract.pop('source_aliases',None)
                        for arm in [*item['arms'].values(),*item.get('excluded_arms',{}).values()]:
                            arm.pop('turn',None)
                            if arm.get('status'):arm['status'].pop('checks',None)
                            if arm.get('evaluation'):
                                arm['evaluation']['checks']=[{k:v for k,v in c.items() if k in
                                    ('id','at','session','state','verdict','reason','cause','error_type','contract_hash','write_readiness')}
                                    for c in arm['evaluation'].get('checks',[])]
                            for exposure in arm.get('exposures',[]):
                                packet=exposure.pop('packet',[])
                                exposure['source_ids']=[m['source_id'] for m in packet]
                                document=exposure.pop('document',None)
                                if document is not None:item['source_documents'][exposure['label']]=document
                            for ex in arm.get('exchanges',[]):
                                judgment = ex.get('judgment')
                                if judgment:
                                    ex['judgment'] = {k: judgment[k] for k in ('question', 'model_call') if k in judgment}
                                ex.pop('question_plan', None)
                                turn = ex.get('turn') or {}
                                turn['prefetch_context_present'] = bool(turn.pop('prefetch_context', None))
                                for result in turn.get('tool_results', []):
                                    text = result.get('text')
                                    if isinstance(text, str) and len(text) > 500:
                                        result['text'] = text[:500]
                                        result['preview_clipped'] = True
                                turn.pop('stderr_full', None)
                                turn.pop('stderr_tail', None)
                                turn.pop('stdout_tail', None)
                                for trace in turn.get('provider_traces', []):
                                    trace.pop('record', None)
                                if ex.get('contract'):ex['contract'].pop('source_aliases',None)
                                if ex.get('source_state'):
                                    state=ex['source_state'];ids=state.pop('source_ids',None)
                                    if ids is not None:state['source_count']=len(ids)
                                    elif state.get('reference_version')==2:
                                        state['source_count']=sum(batch_counts[id] for id in state['ingested_batches'])
                                if ex.get('metrics'):
                                    for operation in ex['metrics'].get('operations', []): operation.pop('result', None)
                                    ex['metrics'].pop('provider_internal_operations', None)
                                if ex.get('retention'):
                                    ex['retention'].pop('initial_metrics',None)
                                    ex['retention'].pop('attempts',None)
                                if ex.get('status'):ex['status'].pop('recovery',None)
                        item.pop('bundle',None)
                        item['bundle_url']='/bundle?'+urlencode({'run':run['id'],'file':bundle_file})
                        for call in item.get('model_calls', []):
                            record=call['record'] or {}
                            location=Path(call['aliases'][0]).resolve().relative_to(runs_dir.resolve())
                            call['record']={**{k:record[k] for k in
                                ('model','status','usage') if k in record},
                                'lazy_url':'/model-call?'+urlencode({
                                    'run':location.parts[0],
                                    'file':Path(*location.parts[1:]).as_posix()})}
                        for review in item.get('verifications', []):
                            record=review['record']
                            if record.get('model_call'):
                                # The exact checker prompt is available from
                                # its indexed model call, without another copy
                                # in every scene's initial response payload.
                                review['record']={k:v for k,v in record.items() if k!='judge_prompt'}
                                review['record']['review_index']=True
                        if item.get('preflight'):
                            index_records(item['preflight'],'preflight/bundle.json')
                    index_records(run)
                runs.append(run)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, StopIteration) as exc:
            # A partial or malformed run must not hide the other saved results.
            errors.append({'run': run_dir.name, 'error': str(exc)})
    runs.sort(key=lambda run: run['sort_time'], reverse=True)
    return {'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
            'runs': runs, 'errors': errors}
