"""A run-owned Honcho calendar; the harness and spending gateway stay real.

Only native wall clocks change. Queue rows, source timestamps, native batching
settings and monotonic timers are never edited. App processes are paused around
each clock step so a claim cannot start between the final DB check and the step.
This records an observed idle boundary, not proof about arbitrary in-memory work.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
import urllib.request
from email.utils import parsedate_to_datetime

from pacbench.followups import save
from pacbench.provider_clock import LIBRARY, LIBRARY_SHA256, ProviderClock

VERSION = 'honcho-wall-clock-v4'
SUPPORTED = ('honcho-wall-clock-v1','honcho-wall-clock-v2','honcho-wall-clock-v3', VERSION)
START_TOLERANCE_S = 5
CONFIG = 'provider-clock.json'


def create(root: Path, at: str) -> ProviderClock:
    if hashlib.sha256(LIBRARY.read_bytes()).hexdigest() != LIBRARY_SHA256:
        raise RuntimeError('Install the pinned provider clock runtime before native setup')
    clock = ProviderClock(root / 'provider-clock', datetime.fromisoformat(at))
    from pacbench.python_provider_clock import package_metadata, VERSION as PYTHON_CLOCK, PACKAGE_C_SHA256
    metadata = package_metadata()
    save(root / CONFIG, {'version': VERSION, 'initial_at': at,
        'library_sha256': LIBRARY_SHA256, 'clock_directory': str(clock.directory),
        'wall_speed': 1, 'monotonic': 'real', 'gateway_clock': 'real',
        'suspend_during_benchmark_overhead': True,
        'overhead_fallback': 'native-clock-runs; no rewind; scripted checkpoint gates remain enforced',
        'source_timestamps': 'unchanged', 'native_batching': 'unchanged',
        'python_clock':{**metadata,'protocol':PYTHON_CLOCK,'extensions_by_abi':PACKAGE_C_SHA256,
                        'os_clock':'real','tls_clock':'real','shared_offset':'per-read; no polling'},
        'scope': 'PostgreSQL uses libfaketime; Python API/deriver/Hermes use shared-offset time-machine; TLS/OS remain real'})
    return clock


def enabled(root: Path) -> bool:
    return (root / CONFIG).exists()


def load(root: Path):
    config = json.loads((root / CONFIG).read_text())
    if config['version'] != VERSION or config['library_sha256'] != LIBRARY_SHA256:
        raise RuntimeError('Unrecognized native provider clock condition')
    directory = root / 'provider-clock'
    if directory.resolve() != Path(config['clock_directory']).resolve():
        raise RuntimeError('Provider clock belongs to another run')
    return ProviderClock.attach(directory), config


def docker_clock_args(clock):
    # Mount the directory, not the offset inode: atomic replacements must be
    # visible inside PostgreSQL. The canary verified this exact library ABI.
    return ['--user', '999:999',
        '--mount', f'type=bind,src={LIBRARY},dst=/pacbench-clock-lib.so,readonly',
        '--mount', f'type=bind,src={clock.directory},dst=/pacbench-clock,readonly',
        '-e', 'LD_PRELOAD=/pacbench-clock-lib.so',
        '-e', 'FAKETIME_TIMESTAMP_FILE=/pacbench-clock/offset',
        '-e', 'FAKETIME_NO_CACHE=1', '-e', 'FAKETIME_DONT_FAKE_MONOTONIC=1']


def register(root, container, endpoint, processes):
    clock, config = load(root)
    config.update(container=container, endpoint=endpoint,
                  processes={name: proc.pid for name, proc in processes.items()})
    save(root / CONFIG, config)
    # Independent native API evidence, rather than assuming its env was honored.
    with urllib.request.urlopen(endpoint + '/health', timeout=5) as response:
        observed = parsedate_to_datetime(response.headers['Date'])
    state = snapshot(root)
    now = clock.now()
    if abs((observed - now).total_seconds()) > 5 or abs(
            (datetime.fromisoformat(state['db_now']) - now).total_seconds()) > 5:
        raise RuntimeError('Native API and database did not adopt the same provider clock')
    save(root / 'provider-clock-startup.json', {'version': VERSION,
        'real_at': datetime.now(timezone.utc).isoformat(), 'provider_at': now.isoformat(),
        'api_date': observed.isoformat(), 'db_at': state['db_now'],
        'library_sha256': LIBRARY_SHA256, 'monotonic': 'real',
        'python_adoptions':{name:read_adoption(root,proc.pid,role=name) for name,proc in processes.items()}})


def verify_adoption(root, value, *, role):
    _, config = load(root)
    expected = config['python_clock']
    if (value.get('protocol') != expected['protocol'] or value.get('version') != expected['version'] or
            value.get('python_sha256') != expected['python_sha256'] or
            value.get('extension_sha256') != expected['extensions_by_abi'].get(value.get('abi')) or
            value.get('bootstrap_sha256') != expected['bootstrap_sha256'] or
            value.get('clock_file') != str(root.resolve()/'provider-clock/offset') or
            value.get('os_clock') != 'real' or value.get('tls_clock') != 'real' or value.get('role') != role):
        raise RuntimeError('Python provider clock adoption differs from declared runtime')
    return value


def read_adoption(root, pid, *, role):
    path=root/'provider-clock/python-adoptions'/(str(pid)+'.json')
    value=json.loads(path.read_text())
    if value.get('pid') != pid:
        raise RuntimeError('Python provider clock PID differs')
    return verify_adoption(root,value,role=role)


def adoption_files(root):
    return {path.name for path in (root/'provider-clock/python-adoptions').glob('*.json')}


def capture_turn_adoption(home, workspace, before):
    root=home.parent
    rows=[]
    try:
        for path in (root/'provider-clock/python-adoptions').glob('*.json'):
            if path.name in before:continue
            value=json.loads(path.read_text())
            if value.get('role')=='hermes' and value.get('home')==str(home.resolve()) and value.get('cwd')==str(workspace.resolve()):
                rows.append(verify_adoption(root,value,role='hermes'))
        if len(rows) != 1:
            raise RuntimeError('Expected one fresh Hermes Python clock adoption')
        return {'state':'pass','adoption':rows[0]}
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as exc:
        return {'state':'fail','reason':type(exc).__name__}


def python_environment(clock, env):
    child=clock.python_environment(env)
    child['PACBENCH_PYTHON_CLOCK_ROLE']='hermes'
    return child


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def snapshot(root, ids=(), observer='hermes'):
    """Fresh read-only native telemetry. Never infer enrollment from counters."""
    _, config = load(root)
    defaults = json.loads((root / 'native-processing-settings.json').read_text())['deriver']
    target = int(defaults['REPRESENTATION_BATCH_WORK_UNIT_TARGET_TOKENS'])
    age = int(defaults['REPRESENTATION_BATCH_MAX_AGE_SECONDS'])
    if target <= 0 or age <= 0 or defaults['FLUSH_ENABLED']:
        raise RuntimeError('Simulated clock requires unchanged native batching defaults')
    identifiers = ','.join(_literal(i) for i in ids) or 'NULL'
    sql = f"""WITH units AS (
        SELECT work_unit_key FROM queue WHERE NOT processed GROUP BY work_unit_key
      ), stats AS (
        SELECT q.work_unit_key,SUM(m.token_count) AS tokens,MIN(q.created_at) AS oldest
        FROM queue q JOIN messages m ON m.id=q.message_id
        WHERE NOT q.processed AND q.work_unit_key LIKE 'representation:%'
        GROUP BY q.work_unit_key
      ), eligible AS (
        SELECT u.work_unit_key FROM units u LEFT JOIN stats s USING(work_unit_key)
        WHERE NOT EXISTS (SELECT 1 FROM active_queue_sessions a WHERE a.work_unit_key=u.work_unit_key)
        AND (u.work_unit_key NOT LIKE 'representation:%' OR coalesce(s.tokens,0)>={target}
             OR s.oldest<=now()-make_interval(secs=>{age}))
      ) SELECT json_build_object(
        'db_now',clock_timestamp(),
        'claims',(SELECT count(*) FROM active_queue_sessions),
        'eligible',(SELECT count(*) FROM eligible),
        'pending',(SELECT count(*) FROM queue WHERE NOT processed),
        'embeddings',(SELECT count(*) FROM message_embeddings WHERE sync_state='pending'),
        'busy_connections',(SELECT count(*) FROM pg_stat_activity
            WHERE datname=current_database() AND pid<>pg_backend_pid()
            AND backend_type='client backend' AND state<>'idle'),
        'next_eligible_at',(SELECT min(oldest)+make_interval(secs=>{age}) FROM stats),
        'errors',(SELECT coalesce(json_agg(json_build_object('id',q.id,'task_type',q.task_type,
                        'session_name',s.name)), '[]'::json)
                  FROM queue q LEFT JOIN sessions s ON s.id=q.session_id WHERE q.error IS NOT NULL),
        'messages',(SELECT coalesce(json_agg(json_build_object(
            'id',m.public_id,'queue_id',q.id,'processed',q.processed,
            'has_error',q.error IS NOT NULL)), '[]'::json)
            FROM messages m JOIN queue q ON q.message_id=m.id AND q.task_type='representation'
                 AND q.workspace_name=m.workspace_name
            WHERE m.public_id IN ({identifiers})
              AND q.payload->'observers' @> jsonb_build_array({_literal(observer)}::text)))"""
    docker_env = {k: v for k, v in os.environ.items() if k not in {
        'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    proc = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', 'exec',
        config['container'], 'psql', '-U', 'postgres', '-d', 'postgres',
        '-X', '-t', '-A', '-c', sql], env=docker_env, capture_output=True, text=True, timeout=15)
    if proc.returncode:
        raise RuntimeError('Native simulated-clock queue telemetry unavailable')
    return json.loads(proc.stdout)


def _docker(config, *args):
    environment = {k:v for k,v in os.environ.items() if k not in {
        'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    return subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args],
        env=environment,capture_output=True,text=True,timeout=15)


def _process_started(pid):
    try:
        fields = (Path('/proc')/str(pid)/'stat').read_text().split(') ')[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except OSError:
        return None


def watch_overhead(root, marker, owner, started):
    """A separate real-clock process stops native work if its owner is killed."""
    while marker.exists():
        try: abort=json.loads(marker.read_text()).get('abort',False)
        except FileNotFoundError:return
        if abort or _process_started(owner) != started:
            _, config = load(root)
            _docker(config,'unpause',config['container'])
            from pacbench.process_cleanup import cleanup_run_processes
            cleanup_run_processes(root)
            stopped = _docker(config,'stop','--time','5',config['container'])
            save(root/'provider-clock-interrupted.json',{
                'version':VERSION,'state':'interrupted','scored':False,
                'real_at':datetime.now(timezone.utc).isoformat(),
                'database_stopped':stopped.returncode == 0,
                'reason':('Native suspension cleanup failed' if abort else
                          'Benchmark owner exited during runtime suspension')})
            marker.unlink(missing_ok=True)
            return
        time.sleep(.2)


@contextmanager
def benchmark_overhead(root, roles):
    """Exclude judging at an observed idle boundary, with real-clock cleanup.

    Snapshots cannot prove arbitrary in-memory quiescence. Monotonic timers keep
    running while suspended; no active observed native work is paused here.
    """
    clock, config = load(root)
    from pacbench.usage import snapshot as cost_snapshot
    stack = ExitStack(); database_paused = False; frozen = None
    unexcluded_reason = None; running_started = None; running_provider = None
    running_offset = None; running_db = None; pause_failure = None
    real_started = datetime.now(timezone.utc)
    marker = clock.directory / ('overhead-' + uuid.uuid4().hex + '.json')
    save(marker, {'owner':os.getpid(),'started':_process_started(os.getpid())})
    watcher = subprocess.Popen([sys.executable,'-m','provider_adapters.honcho_clock',
        '--watch-overhead',str(root.resolve()),str(marker),str(os.getpid()),_process_started(os.getpid())],
        cwd=Path(__file__).resolve().parents[1], start_new_session=True,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        stack.enter_context(paused_apps(root))
        state = snapshot(root)
        usage = cost_snapshot(roles)
        busy_model = usage.get('state') != 'captured' or any(
            c.get('role') == 'memory_provider' and c.get('finished') is None
            for c in usage.get('calls',[]))
        if busy_model or any(state[k] for k in ('claims','eligible','embeddings','busy_connections')):
            stack.close()  # Native work proceeds concurrently; never suspend it for a judge.
            unexcluded_reason = 'native-work-active'
        else:
            paused = _docker(config,'pause',config['container'])
            if paused.returncode:
                inspected = _docker(config,'inspect','--format','{{json .State}}',config['container'])
                try: inspection=json.loads(inspected.stdout)
                except (ValueError,TypeError):inspection={}
                if ('freezer not supported' not in (paused.stderr or '').lower() or inspected.returncode or
                        inspection.get('Paused') is not False or inspection.get('Running') is not True):
                    raise RuntimeError('Cannot suspend the native database for benchmark overhead')
                # A managed runtime may lack cgroup.freeze. Do not rebase an
                # unpaused database: let all native processes and clocks run.
                stack.close()
                unexcluded_reason = 'container-freezer-unavailable'
                error=paused.stderr.strip()
                pause_failure={'returncode':paused.returncode,'error':error,
                    'error_sha256':hashlib.sha256(error.encode()).hexdigest(),
                    'inspection':{k:inspection[k] for k in ('Running','Paused')}}
            else:
                database_paused = True
                frozen = clock.now()  # Read after the cgroup is actually stopped.
        if unexcluded_reason:
            running_db=snapshot(root)['db_now']
            running_started = datetime.now(timezone.utc)
            running_provider = clock.now()
            running_offset=hashlib.sha256(clock.path.read_bytes()).hexdigest()
        yield
    finally:
        try:
            if database_paused:
                def stopped():
                    inspected = _docker(config,'inspect','--format','{{.State.Paused}}',config['container'])
                    return (inspected.returncode == 0 and inspected.stdout.strip() == 'true' and
                        all((Path('/proc')/str(pid)/'stat').read_text().split(') ')[1][0] in 'Tt'
                            for pid in config['processes'].values()))
                try:
                    clock.resume_suspended(frozen,stopped)
                finally:
                    resumed = _docker(config,'unpause',config['container'])
                    if resumed.returncode:
                        raise RuntimeError('Native database did not resume after benchmark overhead')
                    database_paused = False
                observed = snapshot(root)
                if abs((datetime.fromisoformat(observed['db_now'])-frozen).total_seconds()) > 3:
                    raise RuntimeError('Native database clock changed during excluded benchmark overhead')
                with (root/'provider-clock-steps.jsonl').open('a') as stream:
                    stream.write(json.dumps({'version':VERSION,'kind':'benchmark-overhead',
                        'real_started_at':real_started.isoformat(),
                        'real_at':datetime.now(timezone.utc).isoformat(),
                        'provider_at':frozen.isoformat(),'db_after':observed['db_now'],
                        'pre_step':state,'database_paused':True})+'\n')
            elif running_started is not None:
                record={'version':'native-running-overhead-v1','clock_protocol':VERSION,
                    'reason':unexcluded_reason,'database_paused':False,'native_clock_running':True,
                    'offset_adjustment':False,'real_started_at':running_started.isoformat(),
                    'provider_started_at':running_provider.isoformat(),'db_started_at':running_db,
                    'offset_before_sha256':running_offset,'pause_failure':pause_failure}
                try:
                    observed=snapshot(root)
                    record.update(state='observed',real_finished_at=datetime.now(timezone.utc).isoformat(),
                        provider_finished_at=clock.now().isoformat(),db_finished_at=observed['db_now'],
                        offset_after_sha256=hashlib.sha256(clock.path.read_bytes()).hexdigest())
                except Exception as exc:
                    record.update(state='unverified',error_class=type(exc).__name__)
                with (root/'provider-clock-running-overhead.jsonl').open('a') as stream:
                    stream.write(json.dumps(record)+'\n')
        finally:
            try:
                stack.close()
            finally:
                if database_paused:
                    # Keep the external cleanup process alive even though this
                    # owner is alive: it must retry unpause and stop the arm.
                    save(marker,{'abort':True})
                    watcher.wait(timeout=30)
                else:
                    marker.unlink(missing_ok=True)
                    try: watcher.wait(timeout=3)
                    except subprocess.TimeoutExpired: watcher.terminate(); watcher.wait(timeout=3)


@contextmanager
def paused_apps(root):
    """Pause only verified run-owned process groups; always resume in finally."""
    _, config = load(root)
    stopped = []
    try:
        for name, pid in config['processes'].items():
            proc = Path('/proc') / str(pid)
            expected = 'src.main:app' if name == 'api' else 'src.deriver'
            if (pid == os.getpid() or os.getpgid(pid) != pid or
                    (proc / 'cwd').resolve() != root.resolve() or
                    expected not in (proc / 'cmdline').read_bytes().decode(errors='replace')):
                raise RuntimeError('Refusing to pause a process outside this native run')
            os.killpg(pid, signal.SIGSTOP)
            stopped.append(pid)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all((Path('/proc') / str(pid) / 'stat').read_text().split(') ')[1][0] in 'Tt'
                   for pid in stopped):
                break
            time.sleep(.01)
        else:
            raise RuntimeError('Native app pause did not complete')
        yield
    finally:
        for pid in stopped:
            try: os.killpg(pid, signal.SIGCONT)
            except ProcessLookupError: pass


def step_if_idle(root, target, *, ids=(), observer='hermes', horizon=None):
    clock, _ = load(root)
    if target <= clock.now():
        return False
    with paused_apps(root):
        state = snapshot(root, ids, observer)
        if any(state[key] for key in ('claims', 'eligible', 'embeddings', 'busy_connections')):
            return False
        if ids and set(ids) != {m['id'] for m in state['messages']}:
            return False
        before = clock.now()
        if horizon is not None and target > horizon:
            raise RuntimeError('Native processing would advance beyond the next checkpoint')
        # Retained failures must not disappear silently during simulated cleanup.
        # They do not make unrelated persisted conclusions unavailable.
        native_errors = []
        if state['errors']:
            from provider_adapters.honcho_local import queue_errors
            native_errors = queue_errors(root, include_all=True)
        clock.advance(target)
        after = snapshot(root, ids, observer)
        if abs((datetime.fromisoformat(after['db_now']) - clock.now()).total_seconds()) > 3:
            raise RuntimeError('Database did not observe the provider clock advance')
        path = root / 'provider-clock-steps.jsonl'
        with path.open('a') as stream:
            stream.write(json.dumps({'version': VERSION,
                'real_at': datetime.now(timezone.utc).isoformat(), 'from': before.isoformat(),
                'to': clock.now().isoformat(), 'target': target.isoformat(),
                'horizon': horizon.isoformat() if horizon is not None else None,
                'native_errors_before': native_errors,
                'pre_step': state, 'db_after': after['db_now'],
                'scope': 'apps paused; DB idle and recorded queue/embedding work checked'}) + '\n')
        return True


def before_turn(root, at, env):
    """Called by the real harness; only the Hermes child receives clock env."""
    if not enabled(root): return env
    from pacbench.stop import check_stop
    from pacbench.gateway_client import require_live_cost_boundary
    target = datetime.fromisoformat(at)
    deadline = time.monotonic() + 900
    while True:
        check_stop(env.get('PACBENCH_STOP_FILE'))
        require_live_cost_boundary()
        if env.get('PACBENCH_USAGE_ROLES'):
            from pacbench.usage import snapshot as cost_snapshot, provider_model_health
            health = provider_model_health(cost_snapshot(json.loads(env['PACBENCH_USAGE_ROLES'])))
            if health.get('state') == 'stop':
                raise RuntimeError(health['reason'])
        clock, _ = load(root)
        now = clock.now()
        if (now-target).total_seconds() > START_TOLERANCE_S:
            raise RuntimeError('Provider clock is past the scripted checkpoint; refusing future-time exposure')
        if target <= now:
            return python_environment(clock,env)
        state=snapshot(root)
        next_event=target
        if state['pending'] and state.get('next_eligible_at'):
            due=datetime.fromisoformat(state['next_eligible_at'])+timedelta(milliseconds=10)
            if now < due < target:next_event=due
        if step_if_idle(root,next_event,horizon=target):
            if next_event == target:return python_environment(clock,env)
            continue  # Let actual due work finish before the later question.
        if time.monotonic() >= deadline:
            raise RuntimeError('Native Honcho did not become idle before the next simulated turn')
        time.sleep(.2)


def before_recovery(root, initial_at, env):
    """A natural recovery arrives after the answer, never back at its start."""
    clock, _ = load(root)
    now = clock.now()
    initial = datetime.fromisoformat(initial_at)
    next_at = env.get('PACBENCH_CLOCK_NEXT_CHECKPOINT')
    if (now.astimezone(initial.tzinfo).date() != initial.date() or
            next_at and now >= datetime.fromisoformat(next_at)):
        raise RuntimeError('Recovery no longer fits before the next scripted checkpoint')
    return now.isoformat(), python_environment(clock,env)


def wait_for_batch(root, acknowledgments, roles):
    """Witness every message, let native age eligibility fire, preserve defaults."""
    from pacbench.gateway_client import require_live_cost_boundary
    from pacbench.stop import check_stop
    from pacbench.usage import provider_model_health, snapshot as cost_snapshot
    expected = {m['id'] for ack in acknowledgments for m in ack['messages']}
    sessions={ack['session_id'] for ack in acknowledgments}
    observers = {ack['observer']['peer_id'] for ack in acknowledgments}
    if not expected or len(observers) != 1:
        raise RuntimeError('Cannot establish native source enrollment')
    observer = next(iter(observers))
    evidence = {}; history = []; previous = None
    def receipt(ready, reason, health):
        return {'ready': ready, 'interface': 'native messages + simulated wall clock',
                'result': acknowledgments, 'processing_status': history,
                'message_witnesses': list(evidence.values()), 'clock_protocol': VERSION,
                'source_error_scope':'submitted-source-sessions-v1',
                'model_health': health, 'readiness': reason}
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        try:
            require_live_cost_boundary()
        except RuntimeError as exc:
            return receipt(False, str(exc), {})
        check_stop(os.environ.get('PACBENCH_STOP_FILE'))
        health = provider_model_health(cost_snapshot(roles)) if roles else {}
        if health.get('state') == 'stop':
            return receipt(False, health['reason'], health)
        state = snapshot(root, sorted(expected), observer)
        for row in state['messages']:
            previous_row = evidence.get(str(row['queue_id']))
            if previous_row and previous_row['id'] != row['id']:
                raise RuntimeError('Native source witness changed identity')
            evidence[str(row['queue_id'])] = row
        enrolled = expected == {r['id'] for r in evidence.values()}
        complete = enrolled and all(r['processed'] and not r['has_error'] for r in evidence.values())
        progress = {k: v for k, v in state.items() if k not in ('db_now', 'busy_connections')}
        if progress != previous:
            history.append(state); previous = progress
        source_errors=[e for e in state['errors'] if e.get('session_name') is None or e['session_name'] in sessions]
        if source_errors or any(r['has_error'] for r in evidence.values()):
            from provider_adapters.honcho_local import queue_errors
            history[-1]['native_queue_errors'] = queue_errors(root, include_all=True)
            return receipt(False, 'Native Honcho source processing failed; error witnesses preserved', health)
        # Preserve a completion witness before simulated cleanup can remove it.
        if complete and not any(state[k] for k in ('claims', 'pending', 'embeddings')):
            return receipt(True, 'All submitted messages observed, processed and error-free', health)
        if enrolled and not any(state[k] for k in ('claims', 'eligible', 'embeddings')) and state['next_eligible_at']:
            # The time really changes for DB, API and deriver; no flush flag or
            # queue timestamp is modified. The final check occurs while paused.
            target = datetime.fromisoformat(state['next_eligible_at']) + timedelta(milliseconds=10)
            horizon_text = os.environ.get('PACBENCH_CLOCK_HORIZON')
            horizon = datetime.fromisoformat(horizon_text) if horizon_text else None
            if horizon is not None and target > horizon:
                return receipt(False, 'Native source processing is not due before the scripted checkpoint', health)
            step_if_idle(root, target, ids=sorted(expected), observer=observer, horizon=horizon)
        time.sleep(.2)
    return receipt(False, 'Real processing deadline exceeded; missing witnesses are not success', {})


def audit_evidence(root, declared):
    """Offline/publication validation; it does not need a running native DB."""
    errors = []
    def check(value, message):
        if not value: errors.append(message)
    def instant(value):
        result = datetime.fromisoformat(value)
        if result.tzinfo is None: raise ValueError('clock evidence lacks timezone')
        return result
    try:
        config = json.loads((root/CONFIG).read_text())
        startup = json.loads((root/'provider-clock-startup.json').read_text())
        running_overhead=root/'provider-clock-running-overhead.jsonl'
        if running_overhead.exists():
            check(config.get('overhead_fallback')=='native-clock-runs; no rewind; scripted checkpoint gates remain enforced',
                  'Honcho unexcluded judging overhead condition is undeclared')
            for line in running_overhead.read_text().splitlines():
                record=json.loads(line)
                real_before,real_after=instant(record['real_started_at']),instant(record['real_finished_at'])
                provider_before,provider_after=instant(record['provider_started_at']),instant(record['provider_finished_at'])
                check(record.get('version')=='native-running-overhead-v1' and record.get('clock_protocol')==declared and
                      record.get('reason') in {'native-work-active','container-freezer-unavailable'} and
                      record.get('state')=='observed' and record.get('database_paused') is False and
                      record.get('native_clock_running') is True and record.get('offset_adjustment') is False and
                      len(record.get('offset_before_sha256',''))==64 and
                      record['offset_before_sha256']==record.get('offset_after_sha256') and
                      real_before<=real_after and provider_before<=provider_after and
                      abs(((provider_after-provider_before)-(real_after-real_before)).total_seconds())<=3 and
                      abs((instant(record['db_started_at'])-provider_before).total_seconds())<=3 and
                      abs((instant(record['db_finished_at'])-provider_after).total_seconds())<=3,
                      'Honcho unexcluded judging overhead clock witness is inconsistent')
                if record.get('reason')=='container-freezer-unavailable':
                    failure=record.get('pause_failure') or {}
                    check(failure.get('returncode') and 'freezer not supported' in failure.get('error','').lower() and
                          hashlib.sha256(failure.get('error','').encode()).hexdigest()==failure.get('error_sha256') and
                          failure.get('inspection')=={'Running':True,'Paused':False},
                          'Honcho unsupported-freezer fallback lacks a safe native state witness')
        check(declared in SUPPORTED and config['version'] == startup['version'] == declared,
              'Honcho clock condition/version mismatch')
        check(config['library_sha256'] == startup['library_sha256'] == LIBRARY_SHA256 and
              config['wall_speed'] == 1 and config['monotonic'] == startup['monotonic'] == 'real' and
              config['gateway_clock'] == 'real' and config['source_timestamps'] == 'unchanged' and
              config['native_batching'] == 'unchanged', 'Honcho clock isolation declaration differs')
        provider_at = instant(startup['provider_at'])
        check(abs((instant(startup['api_date'])-provider_at).total_seconds()) <= 5 and
              abs((instant(startup['db_at'])-provider_at).total_seconds()) <= 5,
              'Honcho native API/database clocks disagree')
        previous = provider_at
        previous_real = instant(startup['real_at'])
        steps = (root/'provider-clock-steps.jsonl').read_text().splitlines()
        check(bool(steps), 'Honcho clock has no observed advances')
        for line in steps:
            step = json.loads(line)
            if step.get('kind') == 'benchmark-overhead':
                frozen = instant(step['provider_at']); real = instant(step['real_at'])
                check(declared in ('honcho-wall-clock-v3', VERSION) and step['version'] == declared and
                      step['database_paused'] is True and previous <= frozen and
                      previous_real <= instant(step['real_started_at']) <= real and
                      abs((instant(step['db_after'])-frozen).total_seconds()) <= 3 and
                      all(step['pre_step'].get(k)==0 for k in ('claims','eligible','embeddings','busy_connections')),
                      'Honcho benchmark-overhead suspension evidence is inconsistent')
                previous, previous_real = instant(step['db_after']), real
                continue
            before, after, target = (instant(step[k]) for k in ('from','to','target'))
            real = instant(step['real_at'])
            check(step['version'] == declared and previous <= before <= target <= after and
                  previous_real <= real and (after-target).total_seconds() <= 3,
                  'Honcho clock advance is inconsistent or moves backward')
            check(abs((instant(step['db_after'])-after).total_seconds()) <= 3,
                  'Honcho database did not witness clock advance')
            state = step['pre_step']
            check(all(state.get(k) == 0 for k in ('claims','eligible','embeddings','busy_connections')),
                  'Honcho clock advanced with active observed work')
            if state.get('errors'):
                check({e['id'] for e in step.get('native_errors_before',[])} >=
                      {e['id'] for e in state['errors']},
                      'Honcho native errors were not preserved before aging')
            if declared in ('honcho-wall-clock-v2','honcho-wall-clock-v3', VERSION) and step.get('horizon'):
                check(target <= instant(step['horizon']), 'Honcho clock advanced past checkpoint horizon')
            previous, previous_real = after, real
        if declared == VERSION:
            check(config['python_clock']['tls_clock']=='real' and config['python_clock']['os_clock']=='real',
                  'Honcho split clock declaration differs')
            for role,pid in config['processes'].items():
                check(startup['python_adoptions'][role]['pid']==pid,'Honcho native Python clock PID differs')
                verify_adoption(root,startup['python_adoptions'][role],role=role)
        if declared in ('honcho-wall-clock-v2','honcho-wall-clock-v3', VERSION):
            for path in (root/'turns').glob('**/turn.json'):
                turn = json.loads(path.read_text())
                observed = turn.get('provider_clock') or {}
                if declared == VERSION:
                    adoption=observed.get('python_clock') or {}
                    check(adoption.get('state')=='pass','Honcho turn lacks verified Python clock adoption')
                    verify_adoption(root,adoption['adoption'],role='hermes')
                check(observed.get('version') == declared, 'Honcho turn lacks native clock observation')
                actual = instant(observed['actual_start_at'])
                requested = instant(observed['requested_at'])
                if observed.get('role') == 'scripted':
                    check(turn['at'] == observed['requested_at'] and
                          0 <= (actual-requested).total_seconds() <= START_TOLERANCE_S,
                          'Honcho scripted turn used future native time')
                elif observed.get('role') == 'recovery':
                    check(requested <= instant(turn['at']) <= actual and
                          (actual-instant(turn['at'])).total_seconds() <= START_TOLERANCE_S,
                          'Honcho recovery clock is inconsistent')
                    if observed.get('next_checkpoint'):
                        check(actual < instant(observed['next_checkpoint']),
                              'Honcho recovery crossed next scripted checkpoint')
                else:
                    errors.append('Honcho turn clock role is unknown')
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        errors.append('Honcho native clock evidence missing or invalid: '+type(exc).__name__)
    return errors


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == '--watch-overhead':
        watch_overhead(Path(sys.argv[2]),Path(sys.argv[3]),int(sys.argv[4]),sys.argv[5])
    else:
        raise SystemExit('Internal real-clock suspension watchdog only')
