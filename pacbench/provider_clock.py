"""Isolated, advancing wall clocks for native provider processes.

The controller itself uses the real host clock. Callers must establish native
quiescence before each advance, and keep the gateway outside this environment.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from datetime import time as daytime
from pathlib import Path
from zoneinfo import ZoneInfo

from pacbench.python_provider_clock import (
    PACKAGE_C_SHA256,
    PACKAGE_PY_SHA256,
    PACKAGE_VERSION,
    VERSION,
)

LEGACY_LIBRARY = Path(__file__).resolve().parents[1] / 'vendor/libfaketime/runtime/libfaketime.so.1'
LEGACY_LIBRARY_SHA256 = '45c4822a7516e9c41ec1b5498b0f08665ba4877330e9e9573c1213d16d6629a0'
LIBRARY = Path(__file__).resolve().parents[1] / 'vendor/libfaketime/runtime-v4/libfaketime.so.1'
LIBRARY_SHA256 = 'd69f77e806fd01db81d8e26e5722704ad3951c3de2e68dfbcd9d32a2aff817fa'


@contextmanager
def without_hermes(directory):
    """Exclude native gateway startup and refuse surviving clock consumers."""
    home=directory.parent/'hermes-home'
    if not home.exists():
        yield
        return
    from .hermes_gateway import _assert_clear
    path=home/'.pacbench-gateway.lock'
    if path.is_symlink():raise RuntimeError('Gateway lifecycle lock must not be a symlink')
    with path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Cannot step a clock while Hermes owns its lifecycle') from None
        _assert_clear(home)
        for receipt in (directory/'python-adoptions').glob('*.json'):
            row=json.loads(receipt.read_text())
            if row.get('role') not in {'hermes','hermes-gateway','hermes-cli'}:continue
            proc=Path('/proc')/str(row['pid'])/'stat'
            try:started=int(proc.read_text().rsplit(')',1)[1].split()[19])
            except FileNotFoundError:continue
            if started==row.get('process_start_ticks'):
                raise RuntimeError('A native Hermes clock consumer still exists; no clock step')
        yield


def nightly_time(collection_day: date, timezone_name: str = 'America/New_York') -> datetime:
    """The 03:00 local processing time after a collection day, in UTC."""
    return datetime.combine(collection_day + timedelta(days=1), daytime(3),
                            ZoneInfo(timezone_name)).astimezone(timezone.utc)


class ProviderClock:
    """One shared libfaketime relative offset, advancing at real-time speed."""

    def __init__(self, directory: Path, target: datetime):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o755)
        self.directory.chmod(0o755)
        self.path = self.directory / 'offset'
        self._offset = None
        self.advance(target)

    def advance(self, target: datetime) -> float:
        if target.tzinfo is None or target.utcoffset() is None:
            raise ValueError('Clock target must include a timezone')
        with without_hermes(self.directory), (self.directory / 'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            offset = target.timestamp() - time.time()
            previous = float(self.path.read_text()) if self.path.exists() else None
            if previous is not None and offset < previous:
                raise ValueError('Provider clock cannot move backward')
            fd, name = tempfile.mkstemp(prefix='.offset-', dir=self.directory)
            try:
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    stream.write(f'{offset:+.6f}\n')
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        self._offset = offset
        return offset

    @classmethod
    def attach(cls, directory: Path):
        """Attach a real-time harness process to an existing isolated clock."""
        clock = cls.__new__(cls)
        clock.directory = Path(directory).resolve()
        clock.path = clock.directory / 'offset'
        clock._offset = float(clock.path.read_text())
        if not math.isfinite(clock._offset):
            raise ValueError('Invalid provider clock offset')
        return clock

    def now(self) -> datetime:
        return datetime.fromtimestamp(time.time() + float(self.path.read_text()), timezone.utc)

    def resume_suspended(self, at: datetime, verify_suspended):
        """Exclude harness overhead only while every native clock user is stopped.

        This changes the offset without rewinding any *observed* provider time.
        Normal advances still reject backward changes. The caller supplies and
        records independent process/container suspension verification.
        """
        if at.tzinfo is None or not verify_suspended():
            raise RuntimeError('Clock compensation requires a fully suspended native runtime')
        with without_hermes(self.directory), (self.directory/'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not verify_suspended():
                raise RuntimeError('Native runtime resumed during clock compensation')
            fd, name = tempfile.mkstemp(prefix='.offset-',dir=self.directory)
            try:
                with os.fdopen(fd,'w',encoding='ascii') as stream:
                    os.fchmod(stream.fileno(),0o644)
                    stream.write(f'{at.timestamp()-time.time():+.6f}\n')
                os.replace(name,self.path)
            finally:
                if os.path.exists(name):os.unlink(name)

    def environment(self, base: dict[str, str], *, library: str | Path = LIBRARY,
                    timestamp_file: str | Path | None = None) -> dict[str, str]:
        env = {k: v for k, v in base.items()
               if k != 'FAKETIME' and not k.startswith('FAKETIME_') and k != 'DONT_FAKE_MONOTONIC'}
        if env.get('LD_PRELOAD'):
            raise ValueError('Cannot combine provider clock with inherited LD_PRELOAD')
        env.update(LD_PRELOAD=str(library), FAKETIME_TIMESTAMP_FILE=str(timestamp_file or self.path),
                   FAKETIME_NO_CACHE='1', FAKETIME_DONT_FAKE_MONOTONIC='1')
        return env

    def python_environment(self, base: dict[str, str]) -> dict[str, str]:
        from .python_provider_clock import VERSION
        if base.get('LD_PRELOAD'):
            raise ValueError('Python provider clock refuses inherited OS clock interception')
        env = {k:v for k,v in base.items() if k != 'FAKETIME' and not k.startswith('FAKETIME_')
               and k != 'DONT_FAKE_MONOTONIC'}
        repo = Path(__file__).resolve().parents[1]
        paths = [str(repo/'pacbench/clock_bootstrap'),str(repo),*filter(None,env.get('PYTHONPATH','').split(os.pathsep))]
        env.update(PACBENCH_PYTHON_CLOCK_FILE=str(self.path),
            PACBENCH_PYTHON_CLOCK_PROTOCOL=VERSION,PYTHONPATH=os.pathsep.join(dict.fromkeys(paths)))
        return env


ROLES={'hermes-gateway','hermes-cli','gateway-control-probe','gateway-marker-probe'}


def start_ticks(pid):
    return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])


def gateway_clock_environment(home,python,env,role):
    repo=Path(__file__).resolve().parents[1]
    expected=(home.parent/'provider-clock/offset').resolve(strict=True)
    paths=env.get('PYTHONPATH','').split(os.pathsep)
    if (role not in ROLES or not env.get('PACBENCH_PYTHON_CLOCK_NONCE') or
        env.get('PACBENCH_PYTHON_CLOCK_PROTOCOL')!=VERSION or
        Path(env.get('PACBENCH_PYTHON_CLOCK_FILE','')).resolve()!=expected or
        env.get('HERMES_HOME')!=str(home) or env.get('LD_PRELOAD') or
        any(k.startswith('FAKETIME') for k in env) or
        [str(Path(p).resolve()) for p in paths[:2]]!=[str(repo/'pacbench/clock_bootstrap'),str(repo)]):
        raise RuntimeError('Unsupported split-clock gateway environment')
    return {**env,'PACBENCH_PYTHON_CLOCK_ROLE':role}


def gateway_clock_adoption(home,python,env,pid,started,role,*,cwd):
    row=json.loads((home.parent/'provider-clock/python-adoptions'/f'{pid}.json').read_text())
    from pacbench import python_provider_clock
    expected={'protocol':VERSION,'version':PACKAGE_VERSION,'python_sha256':PACKAGE_PY_SHA256,
        'bootstrap_sha256':hashlib.sha256(Path(python_provider_clock.__file__).read_bytes()).hexdigest(),
        'pid':pid,'process_start_ticks':started,'turn_nonce':env['PACBENCH_PYTHON_CLOCK_NONCE'],
        'role':role,'home':str(home),'cwd':str(cwd),'clock_file':str((home.parent/'provider-clock/offset').resolve()),
        'os_clock':'real','tls_clock':'real'}
    if (any(row.get(k)!=v for k,v in expected.items()) or
        row.get('extension_sha256')!=PACKAGE_C_SHA256.get(row.get('abi')) or
        Path(row.get('python','')).absolute()!=Path(python).absolute()):
        raise RuntimeError('Split-clock adoption differs from spawned process identity')
    return row


def probe_gateway_clock(home,python,env,role,code,*,timeout=5,args=()):
    child=gateway_clock_environment(home,python,env,role)
    # Normal startup intentionally loads the pinned PYTHONPATH sitecustomize.
    # -I/-S would silently bypass the clock and direct accounting bootstrap.
    proc=subprocess.Popen([str(python),'-c',code,*map(str,args)],env=child,cwd=home,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    started=start_ticks(proc.pid)
    try:out,err=proc.communicate(timeout=timeout)
    except BaseException:
        proc.kill();proc.wait();raise
    receipt=gateway_clock_adoption(home,python,child,proc.pid,started,role,cwd=home)
    return subprocess.CompletedProcess(proc.args,proc.returncode,out,err),receipt


def marker_time(home,python,env):
    code='import json,time,time_machine;print(json.dumps({"wall":time.time(),"real":time_machine.escape_hatch.time.time()}))'
    result,receipt=probe_gateway_clock(home,python,env,'gateway-marker-probe',code)
    if result.returncode:raise RuntimeError('Split-clock marker probe failed')
    values=json.loads(result.stdout)
    offset=float((home.parent/'provider-clock/offset').read_text())
    if abs(values['real']-time.time())>5 or abs(values['wall']-values['real']-offset)>1:
        raise RuntimeError('Split-clock marker probe did not adopt the shared offset')
    return datetime.fromtimestamp(values['wall'],timezone.utc)


def dispatch_time(env):
    value=datetime.fromtimestamp(time.time()+float(Path(env['PACBENCH_PYTHON_CLOCK_FILE']).read_text()),timezone.utc)
    requested=datetime.fromisoformat(env['PACBENCH_NOW'])
    if not 0<=(value-requested).total_seconds()<=5:
        raise RuntimeError('Gateway startup passed the scripted dispatch window; no question sent')
    horizon=env.get('PACBENCH_CLOCK_NEXT_CHECKPOINT')
    if horizon and value>=datetime.fromisoformat(horizon):
        raise RuntimeError('Question dispatch crossed the next scripted checkpoint')
    return value.isoformat()


def run_cli(cmd,*,home,python,env,cwd,timeout,witness):
    child=gateway_clock_environment(home,python,env,'hermes-cli')
    # A shared venv's console-script shebang can name another checkout.
    # Launch the native module with exactly the interpreter whose identity
    # the gateway and adoption records require; do not relax identity checks.
    if not cmd or Path(cmd[0]).absolute()!=python.with_name('hermes').absolute() or len(cmd)<2 or cmd[1]!='chat':
        raise RuntimeError('Unexpected native CLI entry point')
    cmd=[str(python),'-m','hermes_cli.main',*cmd[1:]]
    witness['cli_entrypoint']={'version':'explicit-pinned-python-module-v1','python':str(python),'module':'hermes_cli.main'}
    # This is launch time only. The pinned native seams separately record the
    # actual client submit and committed acceptance before native scheduling.
    launch=datetime.fromtimestamp(time.time()+float(Path(child['PACBENCH_PYTHON_CLOCK_FILE']).read_text()),timezone.utc)
    target=datetime.fromisoformat(child['PACBENCH_NOW'])
    horizon=child.get('PACBENCH_CLOCK_NEXT_CHECKPOINT')
    if ((child.get('PACBENCH_CLOCK_RECOVERY')!='1' and (launch-target).total_seconds()>5) or
        horizon and launch>=datetime.fromisoformat(horizon)):
        raise RuntimeError('Gateway startup passed the admission window; CLI not launched')
    witness['cli_launch_at']=launch.isoformat()
    proc=subprocess.Popen(cmd,env=child,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    started=start_ticks(proc.pid)
    from pacbench.process_cleanup import communicate_until_stop
    failure=None
    try:out,err=communicate_until_stop(proc,timeout=timeout,env=child)
    except subprocess.TimeoutExpired as error:
        failure=error
        proc.kill();out,err=proc.communicate()
        raise subprocess.TimeoutExpired(cmd,timeout,output=out,stderr=err)
    except BaseException as error:
        failure=error
        proc.kill();proc.wait();raise
    finally:
        try:witness['cli_adoption']=gateway_clock_adoption(home,python,child,proc.pid,started,'hermes-cli',cwd=cwd)
        except Exception as identity_error:
            witness['cli_adoption_error']={'type':type(identity_error).__name__,'reason':str(identity_error),
                'primary_failure_type':type(failure).__name__ if failure is not None else None}
            if failure is None:raise
        path=home.parent/'provider-clock/native-submissions'/(child['PACBENCH_PYTHON_CLOCK_NONCE']+'.jsonl')
        rows=[json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        witness['native_submission']=rows
        submitted=[r for r in rows if r.get('phase')=='client-submit']
        accepted=[r for r in rows if r.get('phase')=='post-commit']
        if len(submitted)==1:witness['question_dispatch_at']=submitted[0]['provider_at']
        if len(accepted)==1:witness['question_accepted_at']=accepted[0]['provider_at']
    return subprocess.CompletedProcess(cmd,proc.returncode,out,err)


def audit_witness(root,turn,config):
    """Verify saved roles and hashes, never query a provider or a current PID."""
    witness=turn.get('hermes_gateway') or {};nonce=witness.get('turn_nonce')
    if not nonce or witness.get('stopped') is not True:raise ValueError('Missing clean split-clock gateway lifecycle')
    metadata=config['python_clock'];seen=[]
    entry=witness.get('cli_entrypoint')
    if entry is not None and (entry.get('version')!='explicit-pinned-python-module-v1' or entry.get('module')!='hermes_cli.main' or
            entry.get('python')!=(witness.get('cli_adoption') or {}).get('python') or
            entry.get('python')!=(witness.get('gateway_adoption') or {}).get('python')):
        raise ValueError('Explicit split-clock interpreter identity differs')
    for key,role in [('gateway_adoption','hermes-gateway'),('cli_adoption','hermes-cli')]:
        row=witness.get(key) or {};seen.append(row.get('pid'))
        if (row.get('role')!=role or row.get('turn_nonce')!=nonce or
            row.get('protocol')!=metadata['protocol'] or row.get('version')!=metadata['version'] or
            row.get('python_sha256')!=metadata['python_sha256'] or
            row.get('extension_sha256')!=metadata['extensions_by_abi'].get(row.get('abi')) or
            row.get('bootstrap_sha256')!=metadata['bootstrap_sha256'] or
            row.get('clock_file')!=str((root/'provider-clock/offset').resolve()) or
            row.get('home')!=str((root/'hermes-home').resolve()) or
            row.get('cwd')!=(turn.get('context_isolation') or {}).get('workspace') or
            row.get('os_clock')!='real' or row.get('tls_clock')!='real' or
            not isinstance(row.get('process_start_ticks'),int)):
            raise ValueError('Split-clock gateway/CLI adoption identity mismatch')
    if len(set(seen))!=2 or seen[0]!=witness.get('pid') or witness['gateway_adoption']['process_start_ticks']!=witness.get('start_time'):
        raise ValueError('Gateway or CLI process identity is ambiguous')
    from pacbench.clock_dispatch import audit_chain
    audit_chain(turn,witness,config)
    return witness


CHECKPOINT_CLOCK_VERSION='checkpoint-observation-clock-v1'


ENV='PACBENCH_REUSE_CLOCK_STATE'


CEILING_ENV='PACBENCH_REUSE_CLOCK_CEILING'


CEILING_MODE='bounded-observation-v1'


OBSERVATION_LIMIT_S=60


def atomic(path,value):
    raw=(json.dumps(value,sort_keys=True)+'\n').encode();temp=path.with_suffix('.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    temp.replace(path);return hashlib.sha256(raw).hexdigest()


def real_time():
    try:
        import time_machine
        return time_machine.escape_hatch.time.time() if time_machine.escape_hatch.is_travelling() else time.time()
    except ImportError:return time.time()


def observation_epoch(path, real, env):
    from pacbench.python_provider_clock import clock_value_ns
    bounded = env.get(CEILING_ENV)==CEILING_MODE
    return clock_value_ns(path.read_text(), round(real*1_000_000_000),
                          allow_ceiling=bounded)/1_000_000_000


def descriptor(*,validate_clock=True,pointer=None,home=None):
    explicit=pointer is not None
    pointer=Path(pointer if explicit else os.environ[ENV]);state=json.loads(pointer.read_text())
    if state['protocol']!=CHECKPOINT_CLOCK_VERSION:raise RuntimeError('Unknown persistent clock condition')
    if not re.fullmatch('[0-9a-f]{32}',state.get('nonce','')):raise RuntimeError('Invalid question clock nonce')
    path=pointer.parent/('preludes' if state.get('prelude') is True else 'questions')/(state['nonce']+'.json');raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=state['sha256']:raise RuntimeError('Question clock descriptor changed')
    value=json.loads(raw)
    if value['protocol']!=CHECKPOINT_CLOCK_VERSION or value['home']!=str(home if explicit else os.environ['HERMES_HOME']):raise RuntimeError('Question clock scope differs')
    if not re.fullmatch('[0-9a-f]{64}',value.get('question_sha256','')):raise RuntimeError('Invalid question digest')
    offset=pointer.parent/'offset' if explicit else Path(os.environ['PACBENCH_PYTHON_CLOCK_FILE'])
    if offset.resolve()!=pointer.parent/'offset' or validate_clock and hashlib.sha256(offset.read_bytes()).hexdigest()!=value['offset_sha256']:
        raise RuntimeError('Question observation clock changed while active')
    return value


def benchmark_env(name,default=''):
    if not os.environ.get(ENV):return os.environ.get(name,default)
    pointer=Path(os.environ[ENV])
    if not pointer.exists():return os.environ.get(name,default)  # startup, before any question
    d=descriptor(validate_clock=False)
    return {'PACBENCH_NOW':d['at'],'PACBENCH_SESSION_STARTED_AT':d.get('initial_at',d['at'])}.get(name,os.environ.get(name,default))


def history(home,session_id):
    with sqlite3.connect(f'file:{Path(home)/"state.db"}?mode=ro',uri=True) as db:
        rows=db.execute('SELECT role,content,tool_name,api_content FROM messages WHERE session_id=? ORDER BY id',(session_id,)).fetchall()
    return {'sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest(),
        'user_texts':[r[1] for r in rows if r[0]=='user']}


def bind(session_id,request_id,text):
    d=descriptor();folder=Path(os.environ[ENV]).parent/'bindings';path=folder/(d['nonce']+'.json')
    if d.get('kind','fresh') not in {'fresh','continuation'}:raise RuntimeError('Unknown native session binding kind')
    if hashlib.sha256(text.encode()).hexdigest()!=d['question_sha256']:raise RuntimeError('Question text differs')
    if not isinstance(session_id,str) or not session_id or not isinstance(request_id,str) or not request_id:
        raise RuntimeError('Missing native request/session identity')
    with sqlite3.connect(f'file:{Path(d["home"])/"state.db"}?mode=ro',uri=True) as db:
        if db.execute('SELECT COUNT(*) FROM sessions WHERE id=?',(session_id,)).fetchone()[0]!=1:
            raise RuntimeError('Native session does not exist')
        if d.get('kind','fresh')=='fresh' and db.execute('SELECT COUNT(*) FROM messages WHERE session_id=? AND role="user"',(session_id,)).fetchone()[0]:
            raise RuntimeError('Persistent question requires an empty fresh native session')
    if d.get('kind')=='continuation' and (d.get('continued_session_id')!=session_id or history(d['home'],session_id)['sha256']!=d.get('history_sha256')):
        raise RuntimeError('Follow-up session or committed public history differs')
    value={'protocol':CHECKPOINT_CLOCK_VERSION,'nonce':d['nonce'],'session_id':session_id,'request_id':request_id,'question_sha256':d['question_sha256']}
    raw=(json.dumps(value,sort_keys=True)+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return value


def deferred_submission(env):
    return bool(env.get('PACBENCH_R2_LIVE_SCOPE') or env.get('PACBENCH_WORKFLOW_SCOPE')
                or env.get(CEILING_ENV)==CEILING_MODE)


def release_at_submission():
    """Advance a bound observation clock immediately before stock CLI submit."""
    pointer=Path(os.environ[ENV]); active=json.loads(pointer.read_text())
    if active.get('prelude') is not True:
        return
    if os.environ.get('PACBENCH_PYTHON_CLOCK_ROLE')!='hermes-cli' or not deferred_submission(os.environ):
        raise RuntimeError('Only the bound native CLI may release the r2 observation clock')
    d=descriptor();folder=pointer.parent
    binding=json.loads((folder/'bindings'/(d['nonce']+'.json')).read_text())
    if binding['question_sha256']!=d['question_sha256']:
        raise RuntimeError('Native submission binding differs')
    path=folder/'offset';real=real_time();current=observation_epoch(path,real,os.environ)
    target=datetime.fromisoformat(d['at']).timestamp()
    bounded=d.get('clock_ceiling')==CEILING_MODE
    if bounded!=(os.environ.get(CEILING_ENV)==CEILING_MODE) or bounded and current>=target:
        raise RuntimeError('Bounded observation requires a strictly forward native checkpoint')
    if d.get('continuation_reason')=='omission-recovery' and current>target:
        d['at']=datetime.fromtimestamp(current,timezone.utc).isoformat()
        target=current
    if current>target+5 or d.get('horizon') and max(current,target)>=datetime.fromisoformat(d['horizon']).timestamp():
        raise RuntimeError('Native CLI startup missed the authored checkpoint')
    if current<target:
        offset=f'{target-real:+.6f}'
        if bounded:
            offset+=f' ceiling={int(target+OBSERVATION_LIMIT_S)*1_000_000_000}'
        temp=path.with_suffix('.submission');temp.write_text(offset+'\n');temp.replace(path)
    d['offset_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    d['prelude_sha256']=active['sha256']
    sha=atomic(folder/'questions'/(d['nonce']+'.json'),d)
    atomic(pointer,{'protocol':CHECKPOINT_CLOCK_VERSION,'nonce':d['nonce'],'sha256':sha})


def sample(phase,session_id,request_id,text,**extra):
    d=descriptor();path=Path(os.environ[ENV]).parent/'bindings'/(d['nonce']+'.json')
    b=json.loads(path.read_text())
    if any(b[k]!=v for k,v in {'session_id':session_id,'request_id':request_id,'question_sha256':hashlib.sha256(text.encode()).hexdigest(),'nonce':d['nonce']}.items()):
        raise RuntimeError('Question request binding differs')
    pid=os.getpid();started=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
    role=os.environ['PACBENCH_PYTHON_CLOCK_ROLE']
    if role=='hermes-gateway' and (pid!=d['gateway_pid'] or started!=d['gateway_start_time']):raise RuntimeError('Question gateway identity differs')
    return {'version':CHECKPOINT_CLOCK_VERSION,'phase':phase,'turn_nonce':d['nonce'],'pid':pid,'process_start_ticks':started,
        'role':role,'home':d['home'],'offset_sha256':d['offset_sha256'],'question_sha256':d['question_sha256'],
        'session_id':session_id,'request_id':request_id,'provider_at':datetime.fromtimestamp(time.time(),timezone.utc).isoformat(),
        'real_at':datetime.fromtimestamp(real_time(),timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),**extra}


def window(stamp):
    d=descriptor();now=datetime.fromisoformat(stamp['provider_at']);at=datetime.fromisoformat(d['at'])
    if not 0<=(now-at).total_seconds()<=5 or d.get('horizon') and now>=datetime.fromisoformat(d['horizon']):
        from pacbench.clock_dispatch import ClockWindowError
        raise ClockWindowError('Persistent question missed its exact clock window')
    return stamp


def emit(stamp):
    d=descriptor();path=Path(os.environ[ENV]).parent/'submissions'/(d['nonce']+'.jsonl')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    try:os.write(fd,(json.dumps(stamp,sort_keys=True)+'\n').encode())
    finally:os.close(fd)


class CheckpointClock:
    def __init__(self,home,at,checkpoint,*,startup_lead_s=60,bounded=False):
        self.home=Path(home).resolve();self.folder=self.home.parent/'reuse-clock';self.folder.mkdir(mode=0o700)
        for name in ['questions','bindings','submissions','preludes']: (self.folder/name).mkdir(mode=0o700)
        self.path=self.folder/'offset';self.checkpoint=checkpoint;self.process_nonce=uuid.uuid4().hex;self.bounded=bounded
        self.path.write_text(f'{datetime.fromisoformat(at).timestamp()-time.time()-startup_lead_s:+.6f}\n')
    def environment(self,env):
        from pacbench.python_provider_clock import VERSION as PYTHON_VERSION
        repo=Path(__file__).resolve().parents[1]
        if CEILING_ENV in env or self.bounded and env.get('PACBENCH_WORKFLOW_CAPTURE')!='pizza-week-conversation-v2':
            raise RuntimeError('Unexpected bounded observation clock scope')
        return {**env,ENV:str(self.folder/'active.json'),**({CEILING_ENV:CEILING_MODE} if self.bounded else {}),'PACBENCH_PYTHON_CLOCK_FILE':str(self.path),
            'PACBENCH_PYTHON_CLOCK_PROTOCOL':PYTHON_VERSION,'PACBENCH_PYTHON_CLOCK_NONCE':self.process_nonce,
            'PACBENCH_DIRECT_CLOCK':env.get('PACBENCH_DIRECT_CLOCK',f'{real_time()},{time.monotonic()}'),
            'PYTHONPATH':str(repo/'pacbench/clock_bootstrap')+os.pathsep+str(repo)+os.pathsep+env.get('PYTHONPATH','')}
    def prepare(self,owner,prompt,at,horizon=None,*,continuation=None):
        # No caller-supplied boolean substitutes for actual ownership/drain evidence.
        from pacbench.hermes_gateway import _identity

        fence = owner.fence()
        _identity(owner.home,owner.witness['pid'])
        own=False
        for path in (self.folder/'python-adoptions').glob('*.json'):
            row=json.loads(path.read_text());process=Path('/proc')/str(row['pid'])/'stat'
            try:fields=process.read_text().rsplit(')',1)[1].split()
            except FileNotFoundError:continue
            if fields[0] in {'Z','X'} or int(fields[19])!=row['process_start_ticks']:continue
            if (row['pid']!=owner.witness['pid'] or row['process_start_ticks']!=owner.witness['start_time'] or
                row.get('role')!='hermes-gateway'):
                raise RuntimeError('Observation clock has another live consumer; no clock step')
            own=True
        if not own:raise RuntimeError('Owned gateway clock adoption is missing')
        target=datetime.fromisoformat(at)
        if target.tzinfo is None or horizon and target>=datetime.fromisoformat(horizon):raise RuntimeError('Invalid question horizon')
        env=getattr(owner,'env',{})
        now=datetime.fromtimestamp(observation_epoch(self.path,time.time(),env),timezone.utc)
        if (now-target).total_seconds()>5:raise RuntimeError('Observation clock cannot rewind to a missed question')
        deferred=deferred_submission(env)
        if self.bounded and (not deferred or env.get(CEILING_ENV)!=CEILING_MODE or now>=target):
            raise RuntimeError('Bounded observation requires a strictly forward native checkpoint')
        if now<=target and not deferred:
            offset=f'{target.timestamp()-time.time():+.6f}\n';temporary=self.path.with_suffix('.tmp');temporary.write_text(offset);temporary.replace(self.path)
        nonce=uuid.uuid4().hex;value={'protocol':CHECKPOINT_CLOCK_VERSION,'nonce':nonce,'at':at,'horizon':horizon,
            'checkpoint':self.checkpoint,'home':str(owner.home),'gateway_pid':owner.witness['pid'],
            'gateway_start_time':owner.witness['start_time'],'question_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'offset_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),'drain_sha256':hashlib.sha256(json.dumps(fence,sort_keys=True).encode()).hexdigest(),
            'source_clock_stepped':False,'clock_scope':'isolated Hermes observations only'}
        if self.bounded:value['clock_ceiling']=CEILING_MODE
        if continuation:value.update(kind='continuation',**continuation)
        else:value['kind']='fresh'
        sha=atomic(self.folder/('preludes' if deferred else 'questions')/(nonce+'.json'),value)
        atomic(self.folder/'active.json',{'protocol':CHECKPOINT_CLOCK_VERSION,'nonce':nonce,'sha256':sha,**({'prelude':True} if deferred else {})})
        return value


def adoption(home,env,pid):
    from pacbench.python_provider_clock import VERSION as PYTHON_VERSION
    from pacbench.python_provider_clock import package_metadata
    value=json.loads((Path(env['PACBENCH_PYTHON_CLOCK_FILE']).parent/'python-adoptions'/f'{pid}.json').read_text())
    expected={**package_metadata(),'protocol':PYTHON_VERSION,'pid':pid,'turn_nonce':env['PACBENCH_PYTHON_CLOCK_NONCE'],
        'clock_file':env['PACBENCH_PYTHON_CLOCK_FILE'],'home':str(home),'os_clock':'real','tls_clock':'real'}
    if any(value.get(k)!=v for k,v in expected.items()):raise RuntimeError('Persistent process clock adoption differs')
    return value


def probe(home,python,env,role,code,*,timeout=5,args=()):
    child={**env,'PACBENCH_PYTHON_CLOCK_ROLE':role}
    proc=subprocess.run([str(python),'-c',code,*map(str,args)],env=child,cwd=home,capture_output=True,text=True,timeout=timeout)
    return proc


def checkpoint_clock_marker_time(home,python,env):
    r=probe(home,python,env,'gateway-marker-probe','import time,json;from pacbench.receipt_costs import now;print(json.dumps({"wall":time.time(),"billing":now()}))',
        timeout=30 if deferred_submission(env) else 5)
    if r.returncode:raise RuntimeError('Persistent clock marker failed')
    expected=observation_epoch(Path(env['PACBENCH_PYTHON_CLOCK_FILE']),time.time(),env)
    values=json.loads(r.stdout)
    if abs(values['wall']-expected)>5 or abs(datetime.fromisoformat(values['billing']).timestamp()-real_time())>5:
        raise RuntimeError('Persistent simulated wall or real billing clock differs')
    return datetime.fromtimestamp(values['wall'],timezone.utc)


def audit_question(folder,d,turn,witness,*,remote_runtime=False):
    """Offline audit of a complete native admission chain, distinct from v5."""
    folder=Path(folder);binding=json.loads((folder/'bindings'/(d['nonce']+'.json')).read_text())
    rows=[json.loads(line) for line in (folder/'submissions'/(d['nonce']+'.jsonl')).read_text().splitlines()]
    if [r['phase'] for r in rows]!=['client-submit','gateway-received','pre-commit','post-commit']:
        raise RuntimeError('Persistent admission chain incomplete; no clock qualification')
    prompt=turn.prompt if hasattr(turn,'prompt') else turn['prompt'];sid=turn.session_id if hasattr(turn,'session_id') else turn['session_id']
    if (d['question_sha256']!=hashlib.sha256(prompt.encode()).hexdigest() or binding['session_id']!=sid or
        d['gateway_pid']!=witness['pid'] or d['gateway_start_time']!=witness['start_time']):
        raise RuntimeError('Persistent question/owner binding differs')
    for index,r in enumerate(rows):
        if any(r.get(k)!=v for k,v in {'version':CHECKPOINT_CLOCK_VERSION,'turn_nonce':d['nonce'],'session_id':sid,
            'request_id':binding['request_id'],'offset_sha256':d['offset_sha256'],'question_sha256':d['question_sha256'],
            'role':'hermes-cli' if index==0 else 'hermes-gateway'}.items()):raise RuntimeError('Persistent admission identity differs')
        if index and (r['pid']!=witness['pid'] or r['process_start_ticks']!=witness['start_time']):raise RuntimeError('Persistent gateway PID differs')
        now=datetime.fromisoformat(r['provider_at']);at=datetime.fromisoformat(d['at'])
        if not 0<=(now-at).total_seconds()<=5 or d.get('horizon') and now>=datetime.fromisoformat(d['horizon']):
            raise RuntimeError('Persistent native admission missed exact question time')
    cli=json.loads((folder/'python-adoptions'/f'{rows[0]["pid"]}.json').read_text())
    process=witness['process_adoption']
    if (cli['pid']==process['pid'] or cli['process_start_ticks']!=rows[0]['process_start_ticks'] or
        cli['turn_nonce']!=process['turn_nonce'] or cli['role']!='hermes-cli' or process['role']!='hermes-gateway'):
        raise RuntimeError('Persistent process adoption differs from native request roles')
    from pacbench.python_provider_clock import VERSION as PYTHON_VERSION
    from pacbench.python_provider_clock import expected_metadata, package_metadata
    metadata=expected_metadata(process['abi']) if remote_runtime else package_metadata()
    for row in [cli,process]:
        if any(row.get(k)!=v for k,v in {**metadata,'protocol':PYTHON_VERSION,'home':d['home'],
            'clock_file':str(folder/'offset'),'os_clock':'real','tls_clock':'real'}.items()):
            raise RuntimeError('Persistent adoption runtime/clock path differs')
    return {'protocol':CHECKPOINT_CLOCK_VERSION,'state':'pass','descriptor':d,'binding':binding,'native_admission':rows,
        'gateway_process_adoption':process,'cli_process_adoption':cli,'canonical_ingestion_clock_changed':False}
