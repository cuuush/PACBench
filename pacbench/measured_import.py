"""Supervised import-only trials with bounded cancellation and per-call metrics."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal
import hashlib, json, os, signal, subprocess, sys, threading, time, uuid
from pathlib import Path

from .import_lifecycle import OWNER, scan, cleanup, identity, alive
from .import_timings import union_seconds

PROVIDERS = ['memcal','mem0','hindsight','supermemory','openviking','honcho','reme','cognee','memos']
REPO = Path(__file__).resolve().parents[1]


def now(): return datetime.now(timezone.utc).isoformat()
def stamp(value): return datetime.fromisoformat(value).timestamp()


def read_events(path):
    events=[];errors=[]
    if path.exists():
        for index,line in enumerate(path.read_text().splitlines()):
            try: events.append(json.loads(line))
            except ValueError: errors.append({'line':index+1,'state':'malformed-or-interrupted-event'})
    return events,errors


def write(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as stream:
        os.chmod(temp, 0o600); json.dump(value, stream, indent=2); stream.write('\n')
    temp.replace(path)


def observations(component, calls):
    component = Path(component); provider = component.name
    arm = component / 'repeat-1' / provider
    project = 'pacbench-' + hashlib.sha256(str(arm.resolve()).encode()).hexdigest()[:12] + '-' + provider + '-memory'
    own = [c for c in calls if c.get('project') == project]
    events,event_errors = read_events(component / 'import-events.jsonl')
    starts = {e['phase_id']: e for e in events if e['event'] == 'phase-start'}
    ends = {e['phase_id']: e for e in events if e['event'] == 'phase-end'}
    phases = [{**start, 'end_utc': ends.get(key, {}).get('utc'),
               'duration_s': ends.get(key, {}).get('duration_s'),
               'state': ends.get(key, {}).get('state', 'interrupted-no-terminal-event')}
              for key, start in starts.items()]
    requests = []; repeated = {}
    for c in own:
        matches = [p for p in phases if stamp(p['utc']) <= stamp(c['started']) and
                   (not p['end_utc'] or stamp(c['started']) <= stamp(p['end_utc']))]
        phase = matches[-1] if matches else None
        row = {'id': c['id'], 'started': c['started'], 'finished': c.get('finished'),
               'duration_s': stamp(c['finished'])-stamp(c['started']) if c.get('finished') else None,
               'state': c['state'], 'http_status': c.get('http_status'),
               'model': c.get('served_model', c.get('model')), 'provider': c.get('served_provider'),
               'generation_id': c.get('upstream_request_id'), 'finish_reason': c.get('finish_reason'),
               'input_tokens': c.get('input_tokens'), 'output_tokens': c.get('output_tokens'),
               'usage_detail': c.get('usage_detail'), 'response_validation': c.get('response_validation'),
               'native_request_parameters': c.get('native_request_parameters'),
               'reserved_bound_usd':c.get('reserved'),
               'reservation_policy':c.get('reservation_policy','independent-input-output-v1'),
               'reported_cost_usd': c['accounted'] if c['state'] == 'settled' else None,
               'unresolved_bound_usd': c['accounted'] if c['state'] != 'settled' else None,
               'request_sha256': c.get('request_sha256'),
               'phase_id': phase['phase_id'] if phase else None,
               'phase': phase['phase'] if phase else 'unattributed-native-background',
               'batch': phase.get('batch') if phase else None}
        repeated.setdefault(c.get('request_sha256'), []).append(c['id']); requests.append(row)
    for phase in phases:
        left = stamp(phase['utc']); right = stamp(phase['end_utc']) if phase['end_utc'] else None
        intervals = [(max(left,stamp(c['started'])),min(right,stamp(c['finished']))) for c in own
            if right is not None and c.get('finished') and stamp(c['started']) < right and stamp(c['finished']) > left]
        span = union_seconds(intervals) if right is not None else None
        phase['observed_api_span_union_s'] = span
        phase['non_api_or_unresolved_s'] = max(0,phase['duration_s']-span) if span is not None else None
        admitted=[r for r in requests if r['phase_id']==phase['phase_id']]
        phase['requests_admitted']=len(admitted)
        phase['settled_reported_cost_usd']=str(sum(Decimal(r['reported_cost_usd']) for r in admitted if r['reported_cost_usd'] is not None))
        phase['unresolved_bound_usd']=str(sum(Decimal(r['unresolved_bound_usd']) for r in admitted if r['unresolved_bound_usd'] is not None))
    ready = next((e for e in events if e['event'] == 'native-all-batches-ready'), None)
    return {'provider': provider, 'request_count_observed': len(requests), 'requests': requests,'event_parse_errors':event_errors,
            'phases': phases, 'native_ready_observation': ready,
            'settled_reported_cost_usd': str(sum(Decimal(c['accounted']) for c in own if c['state']=='settled')),
            'unresolved_bound_usd': str(sum(Decimal(c['accounted']) for c in own if c['state']!='settled')),
            'input_tokens_observed': sum(c.get('input_tokens') or 0 for c in own),
            'output_tokens_observed': sum(c.get('output_tokens') or 0 for c in own),
            'requests_missing_token_counts': sum(c.get('input_tokens') is None or c.get('output_tokens') is None for c in own),
            'identical_request_groups': [v for k,v in repeated.items() if k and len(v)>1],
            'timing_scope': 'Real-clock phases; API duration includes network, provider queue and model time. Residual includes local compute, polling, unresolved API intervals and harness overhead; not individually identifiable. Identical bodies are reported without assuming retries or loops. Local compute and local embeddings are unpriced.'}


def native_concurrency_environment(provider):
    condition=os.environ.get('PACBENCH_LUNA_CONCURRENCY_CONDITION')
    if not condition:return {}
    from .openrouter_route import PROFILE_NAME
    if condition!='luna-budget-serial-v1' or PROFILE_NAME!='luna-bedrock-global':
        raise ValueError('Unknown or unauthorized native concurrency condition')
    fields={'hindsight':{'HINDSIGHT_API_LLM_MAX_CONCURRENT':'1'},
            'memcal':{'MEMCAL_MAX_PARALLEL':'1'}}
    if provider not in fields:raise ValueError('Serial condition is defined only for Hindsight and Memcal')
    return fields[provider]


def supervise(command, component, *, stop, stop_file, timeout_s, grace_s=5, container_cleanup=True, cancel_file=None):
    component = Path(component).resolve(); owner = uuid.uuid4().hex
    env = {**os.environ, OWNER: owner, 'PACBENCH_IMPORT_ROOT': str(component),
           'PACBENCH_STOP_FILE': str(stop_file), 'TMPDIR': '/var/tmp',
           'PACBENCH_MEMORY_API_INPUT_TRACE_ROOT':str(component/'repeat-1'/component.name/'memory-api-inputs'),
           'DERIVER_MODEL_CONFIG__THINKING_EFFORT': 'none'}
    native_limits=native_concurrency_environment(component.name)
    env.update(native_limits)
    if native_limits:write(component.with_name(component.name+'.condition.json'),{
        'condition':'luna-budget-serial-v1','native_environment':native_limits,
        'scope':'Explicit budget-limited native concurrency; inference settings and workload unchanged otherwise. Compare separately from native defaults.'})
    if cancel_file is not None:env['PACBENCH_DIRECT_STOP_ON_429']=str(cancel_file)
    started_utc = now(); started = time.monotonic(); known = []; reason = None
    ownership_path=component.with_name(component.name+'.ownership.private.json')
    log = component.with_name(component.name + '.execution.private.log')
    with log.open('xb') as stream:
        log.chmod(0o600)
        child = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                                 stderr=stream, start_new_session=True, cwd=REPO)
        row = identity(child.pid)
        if row: known.append({**row, 'ownership': ['controller-created-child']})
        write(ownership_path,{'state':'running','root':str(component),'owner':owner,'processes':known,'started':started_utc})
        last_inventory=time.monotonic()
        try:
            while child.poll() is None:
                rows, _ = scan(component, owner, known)
                known = list({(r['pid'],r['start_ticks']):r for r in [*known,*rows]}.values())
                if time.monotonic()-last_inventory>=2:
                    write(ownership_path,{'state':'running','root':str(component),'owner':owner,'processes':known,'started':started_utc})
                    last_inventory=time.monotonic()
                if stop.is_set() or stop_file.exists() or cancel_file is not None and cancel_file.exists():
                    reason = 'cancelled'; break
                if time.monotonic()-started >= timeout_s: reason = 'timeout'; break
                time.sleep(.25)
            if reason:
                stop_file.touch(exist_ok=True)
                deadline = time.monotonic()+grace_s
                while child.poll() is None and time.monotonic()<deadline:
                    rows, _ = scan(component, owner, known); known += rows
                    time.sleep(.1)
        except BaseException:
            reason = 'controller-exception'; stop_file.touch(exist_ok=True)
            raise
        finally:
            cleaned = cleanup(component, owner, known, grace_s=2, container_cleanup=container_cleanup)
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: cleaned['direct_child_wait'] = 'still-running'
            write(ownership_path,{'state':'terminal','root':str(component),'owner':owner,'processes':known,
                'started':started_utc,'finished':now(),'cleanup':cleaned,'stop_reason':reason})
    finished = now()
    parse_errors=[]
    try: summary = json.loads((component/'summary.json').read_text()) if (component/'summary.json').exists() else {}
    except (OSError,ValueError): summary={};parse_errors.append('summary-unreadable-or-interrupted')
    events,event_errors=read_events(component/'import-events.jsonl');parse_errors+=event_errors
    ready = next((e for e in events if e['event']=='native-all-batches-ready'), None)
    first_batch=next((e for e in events if e.get('event')=='phase-start' and e.get('phase')=='native-batch-import-through-readiness'),None)
    complete=(not reason and child.returncode==0 and summary.get('state')=='processing-complete'
        and summary.get('mode')=='native-import-validation' and summary.get('benchmark_questions_sent')==0
        and summary.get('judge_calls')==0 and not parse_errors and ready
        and cleaned['state']=='verified-inventory-stopped')
    return {'provider': component.name, 'started': started_utc, 'finished': finished,
            'launch_to_cleanup_wall_s': time.monotonic()-started,
            'launch_to_native_ready_s': ready['monotonic_s']-started if ready else None,
            'first_batch_to_native_ready_s': ready['monotonic_s']-first_batch['monotonic_s'] if ready and first_batch else None,
            'returncode': child.returncode, 'stop_reason': reason,
            'state': 'processing-complete' if complete else reason or 'incomplete',
            'parse_errors':parse_errors,
            'cleanup': cleaned, 'benchmark_questions_sent': summary.get('benchmark_questions_sent'),
            'judge_calls': summary.get('judge_calls'), 'stores_preserved': True,
            'condition': 'Fresh stores, concurrent functional imports; includes initialization and native background processing. Readiness excludes subsequent shutdown. Not a controlled hardware latency ranking.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--probe-source-ids',nargs=4,help='Isolated four-message conversation probe from the frozen fixture')
    parser.add_argument('--providers',nargs='+',choices=PROVIDERS,default=PROVIDERS)
    parser.add_argument('--concurrency',type=int,choices=range(1,10),default=9)
    parser.add_argument('--timeout-s',type=float,default=3600)
    parser.add_argument('--detach',action='store_true')
    args=parser.parse_args()
    if args.probe_source_ids and len(args.providers)!=1:parser.error('Conversation probe requires one provider')
    if args.timeout_s<=0 or len(args.providers)!=len(set(args.providers)): parser.error('Positive timeout and unique providers required')
    if args.detach:
        from .durable_job import launch
        print(json.dumps(launch([sys.executable,'-m','pacbench.measured_import',*[a for a in sys.argv[1:] if a!='--detach']],args.output)));return
    root=args.output.resolve()
    if root.exists(): parser.error('Preserve existing results; use a new output directory')
    subprocess.run([sys.executable,str(REPO/'scripts/run_reviewed_week_cohort.py'),'--preflight-only',
        '--output',str(root)],cwd=REPO,check=True)
    root.mkdir(); stop=threading.Event(); stop_file=root/'STOP'
    def request_stop(*unused): stop.set();stop_file.touch(exist_ok=True)
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,request_stop)
    from .openrouter_route import MODEL,ENDPOINT,PROFILE_NAME
    report={'model':MODEL,'endpoint':ENDPOINT,'route_profile':PROFILE_NAME,'native_concurrency_condition':os.environ.get('PACBENCH_LUNA_CONCURRENCY_CONDITION'),'reservation_policy':os.environ.get('PACBENCH_RESERVATION_POLICY','independent-input-output-v1'),'version':'measured-native-import-v1','state':'running','started':now(),
            'execution_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
            'concurrency':args.concurrency,'provider_timeout_s':args.timeout_s,
            'benchmark_questions_sent':0,'judge_calls':0,'probe_source_ids':args.probe_source_ids,'results':[]}
    write(root/'measured-cohort.json',report)
    def worker(provider):
        if stop.is_set() or stop_file.exists(): return {'provider':provider,'state':'not-started-cancelled'}
        component=root/provider
        command=[sys.executable,'-m','pacbench.openrouter_run','--import-only','--prepared',
                 str(REPO/'artifacts/pizza-week-hybrid-stream-v7-20261113/week.json'),
                 '--providers',provider,'--output',str(component),'--spend-limit-usd','20','--max-messages','150']
        if args.probe_source_ids:command+=['--probe-source-ids',*args.probe_source_ids]
        if provider=='honcho': command+=['--honcho-clock','simulated']
        result=supervise(command,component,stop=stop,stop_file=root/(provider+'.STOP'),timeout_s=args.timeout_s,cancel_file=stop_file)
        write(root/(provider+'.lifecycle.json'),result)
        text=(root/(provider+'.execution.private.log')).read_text(errors='replace')
        if any(s in text for s in ('Task authentication blocked','upstream_auth_blocked','Task spending cap reached',
                'Reported task liability exceeded','Direct OpenRouter authentication blocked')):
            stop.set();stop_file.touch(exist_ok=True)
        return result
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures={pool.submit(worker,p):p for p in args.providers}
        while futures:
            for future in as_completed(list(futures)):
                provider=futures.pop(future)
                try: row=future.result()
                except BaseException as exc: row={'provider':provider,'state':'controller-failed','error_class':type(exc).__name__}
                report['results'].append(row);write(root/'measured-cohort.json',report)
                print(json.dumps({k:row.get(k) for k in ('provider','state','launch_to_native_ready_s','stop_reason')}),flush=True)
    from .direct_accounting import live_budget
    calls=live_budget().snapshot()
    for provider in args.providers:
        component=root/provider
        if component.exists():
            try:metrics=observations(component,calls)
            except Exception as exc:metrics={'state':'metrics-incomplete','error_class':type(exc).__name__}
            write(root/(provider+'.metrics.json'),metrics)
    report.update(state='finished',finished=now(),all_processing_complete=all(r['state']=='processing-complete' for r in report['results']))
    write(root/'measured-cohort.json',report)


if __name__=='__main__': main()
