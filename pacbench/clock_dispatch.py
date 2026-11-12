"""Observe and guard the real native submission boundary; never alter prompt text.

Used only by the pinned Hermes instrumentation in a shared-offset clock trial.
The transaction guard has no filesystem/network effects: its sample is held in
memory and emitted after commit or refusal, outside the native transaction.
"""
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

VERSION = 'native-clock-admission-v1'
PRELUDE = 'forward-only-natural-startup-prelude-v1'
class ClockWindowError(RuntimeError):
    pass


_pending = {}
_lock = threading.Lock()


def active():
    return bool(os.environ.get('PACBENCH_PYTHON_CLOCK_FILE'))


def context():
    env=os.environ
    home=Path(env['HERMES_HOME']).resolve()
    path=Path(env['PACBENCH_PYTHON_CLOCK_FILE']).resolve(strict=True)
    nonce=env['PACBENCH_PYTHON_CLOCK_NONCE']
    digest=env['PACBENCH_QUESTION_SHA256']
    if (path!=(home.parent/'provider-clock/offset').resolve(strict=True) or
        not re.fullmatch('[0-9a-f]{32}',nonce) or not re.fullmatch('[0-9a-f]{64}',digest) or
        hashlib.sha256(path.read_bytes()).hexdigest()!=env['PACBENCH_CLOCK_OFFSET_SHA256']):
        raise RuntimeError('Native submission clock identity changed')
    return home,path,nonce,digest


def sample(phase,session_id,request_id,text,**extra):
    home,path,nonce,expected=context()
    digest=hashlib.sha256(text.encode()).hexdigest()
    if digest!=expected:raise RuntimeError('Native submission question differs from the verbatim question')
    import time_machine
    return {'version':VERSION,'phase':phase,'turn_nonce':nonce,
        'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'pid':os.getpid(),'process_start_ticks':int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]),
        'role':os.environ['PACBENCH_PYTHON_CLOCK_ROLE'],'home':str(home),
        'offset_sha256':os.environ['PACBENCH_CLOCK_OFFSET_SHA256'],
        'question_sha256':digest,'session_id':session_id,'request_id':request_id,
        'provider_at':datetime.fromtimestamp(time.time(),timezone.utc).isoformat(),
        'real_at':datetime.fromtimestamp(time_machine.escape_hatch.time.time(),timezone.utc).isoformat(),
        'monotonic_ns':time.monotonic_ns(),**extra}


def window(stamp,env=None):
    env=os.environ if env is None else env
    now=datetime.fromisoformat(stamp['provider_at']);target=datetime.fromisoformat(env['PACBENCH_NOW'])
    horizon=env.get('PACBENCH_CLOCK_NEXT_CHECKPOINT')
    if horizon and now>=datetime.fromisoformat(horizon):
        raise ClockWindowError('Native submission reached the next scripted checkpoint')
    if env.get('PACBENCH_CLOCK_RECOVERY')=='1':
        if now.astimezone(target.tzinfo).date()!=target.date():
            raise ClockWindowError('Native recovery left the initial local date')
    elif not 0<=(now-target).total_seconds()<=5:
        raise ClockWindowError('Native submission missed the scripted five-second window')
    return stamp


def emit(stamp):
    home,path,nonce,_=context()
    directory=path.parent/'native-submissions';directory.mkdir(exist_ok=True,mode=0o700)
    target=directory/(nonce+'.jsonl')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    try:os.write(fd,(json.dumps(stamp,sort_keys=True)+'\n').encode())
    finally:os.close(fd)


def before_submit(session_id,request_id,text):
    if not active():return
    target=datetime.fromisoformat(os.environ['PACBENCH_NOW'])
    # Let the final interval elapse naturally. Never change the shared offset.
    if os.environ.get('PACBENCH_CLOCK_RECOVERY')!='1':
        while True:
            context()
            remaining=target.timestamp()-time.time()
            if remaining<=0:break
            time.sleep(min(.1,remaining))
    stamp=sample('client-submit',session_id,request_id,text)
    try:window(stamp)
    except RuntimeError:
        emit({**stamp,'phase':'client-refused'});raise
    emit(stamp)


async def before_submit_async(session_id,request_id,text):
    if not active():return
    target=datetime.fromisoformat(os.environ['PACBENCH_NOW'])
    if os.environ.get('PACBENCH_CLOCK_RECOVERY')!='1':
        while True:
            context()
            remaining=target.timestamp()-time.time()
            if remaining<=0:break
            # Keep native CLI receive/heartbeat tasks responsive during startup.
            await asyncio.sleep(min(.1,remaining))
    before_submit(session_id,request_id,text)


def received(session_id,params):
    if not active():return
    stamp=sample('gateway-received',session_id,params.get('submission_id') or params.get('input_id'),params['text'])
    try:window(stamp)
    except RuntimeError:
        emit({**stamp,'phase':'gateway-refused'});raise
    emit(stamp)


def admission_guard(prior,session_id,request_id,payload):
    if not active():return prior
    def guard(conn):
        if prior is not None:prior(conn)
        stamp=sample('pre-commit',session_id,request_id,payload['text'])
        # In-memory only. Native SQLite retries can replace this sample.
        with _lock:_pending[(session_id,request_id)]=stamp
        window(stamp)
    return guard


def flush_pending(session_id,request_id):
    with _lock:stamp=_pending.pop((session_id,request_id),None)
    if stamp is not None:emit(stamp)


def admitted(row):
    if not active():return
    sid,rid=row['target_session_id'],row['request_id']
    flush_pending(sid,rid)
    # The native write is committed; this is an upper bound BEFORE scheduling.
    stamp=sample('post-commit',sid,rid,row['payload']['text'],admission_id=row['admission_id'])
    try:window(stamp);stamp['in_window']=True
    except RuntimeError:stamp['in_window']=False
    emit(stamp)
    # Do not undo or strand native accepted work if this upper bound is late.


def refused(session_id,params):
    if not active():return
    rid=params.get('submission_id') or params.get('input_id')
    flush_pending(session_id,rid)
    emit(sample('admission-refused',session_id,rid,params['text']))


def audit_chain(turn,witness,config):
    """Read saved admission brackets; never query a live runtime or change time."""
    condition=config.get('dispatch_condition') or {}
    if (condition.get('version')!=VERSION or condition.get('startup')!=PRELUDE or
        condition.get('lead_seconds')!=60):
        raise ValueError('Missing declared native admission/startup condition')
    rows=witness.get('native_submission') or []
    phases=['client-submit','gateway-received','pre-commit','post-commit']
    if [r.get('phase') for r in rows]!=phases:
        raise ValueError('Missing or ambiguous native submission/admission bracket')
    raw=witness.get('clock_offset','')
    from .python_provider_clock import offset_ns
    offset=offset_ns(raw)/1_000_000_000
    digest=hashlib.sha256(raw.encode()).hexdigest()
    if digest!=witness.get('offset_sha256'):raise ValueError('Native admission offset witness differs')
    question=hashlib.sha256(turn['prompt'].encode()).hexdigest()
    sid,rid=rows[0].get('session_id'),rows[0].get('request_id')
    if not sid or not rid or sid!=turn.get('session_id'):
        raise ValueError('Native admission session/request identity differs')
    clock=turn.get('provider_clock') or {}
    recovery=clock.get('role')=='recovery'
    env={'PACBENCH_NOW':clock.get('requested_at',turn['at']) if recovery else turn['at']}
    if recovery:env['PACBENCH_CLOCK_RECOVERY']='1'
    if clock.get('next_checkpoint'):env['PACBENCH_CLOCK_NEXT_CHECKPOINT']=clock['next_checkpoint']
    previous_mono=-1;previous_at=None
    for row,role,key in zip(rows,['hermes-cli','hermes-gateway','hermes-gateway','hermes-gateway'],
                            ['cli_adoption','gateway_adoption','gateway_adoption','gateway_adoption']):
        adoption=witness[key]
        if (row.get('version')!=VERSION or row.get('source_sha256')!=condition.get('source_sha256') or
            row.get('turn_nonce')!=witness['turn_nonce'] or row.get('role')!=role or
            row.get('pid')!=adoption['pid'] or row.get('process_start_ticks')!=adoption['process_start_ticks'] or
            row.get('home')!=adoption['home'] or row.get('offset_sha256')!=digest or
            row.get('question_sha256')!=question or row.get('session_id')!=sid or row.get('request_id')!=rid):
            raise ValueError('Native admission bracket identity differs')
        now=datetime.fromisoformat(row['provider_at']);real=datetime.fromisoformat(row['real_at'])
        mono=row.get('monotonic_ns')
        if (not isinstance(mono,int) or mono<previous_mono or previous_at and now<previous_at or
            abs((now-real).total_seconds()-offset)>1):
            raise ValueError('Native admission clock bracket is inconsistent')
        window(row,env)
        previous_mono=mono;previous_at=now
    if (rows[-1].get('in_window') is not True or not rows[-1].get('admission_id') or
        witness.get('question_dispatch_at')!=rows[0]['provider_at'] or
        witness.get('question_accepted_at')!=rows[-1]['provider_at']):
        raise ValueError('Native committed admission is absent, late or mislabeled')
    if clock and clock.get('actual_start_at')!=rows[-1]['provider_at']:
        raise ValueError('Actual start does not name the committed admission bound')
    return rows
