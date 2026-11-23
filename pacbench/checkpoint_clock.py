"""Versioned clock for isolated question observations, never canonical ingestion.

The owned Hermes process keeps one adoption identity. Immutable descriptors and
single-use session/request bindings identify individual questions. Only its
controller may step this separate clock, after a complete native-write fence.
"""
from datetime import datetime,timezone
import hashlib,json,os,re,sqlite3,subprocess,time,uuid
from pathlib import Path

VERSION='checkpoint-observation-clock-v1'
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
    from .python_provider_clock import clock_value_ns
    bounded = env.get(CEILING_ENV)==CEILING_MODE
    return clock_value_ns(path.read_text(), round(real*1_000_000_000),
                          allow_ceiling=bounded)/1_000_000_000


def descriptor(*,validate_clock=True,pointer=None,home=None):
    explicit=pointer is not None
    pointer=Path(pointer if explicit else os.environ[ENV]);state=json.loads(pointer.read_text())
    if state['protocol']!=VERSION:raise RuntimeError('Unknown persistent clock condition')
    if not re.fullmatch('[0-9a-f]{32}',state.get('nonce','')):raise RuntimeError('Invalid question clock nonce')
    path=pointer.parent/('preludes' if state.get('prelude') is True else 'questions')/(state['nonce']+'.json');raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=state['sha256']:raise RuntimeError('Question clock descriptor changed')
    value=json.loads(raw)
    if value['protocol']!=VERSION or value['home']!=str(home if explicit else os.environ['HERMES_HOME']):raise RuntimeError('Question clock scope differs')
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
    value={'protocol':VERSION,'nonce':d['nonce'],'session_id':session_id,'request_id':request_id,'question_sha256':d['question_sha256']}
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
    atomic(pointer,{'protocol':VERSION,'nonce':d['nonce'],'sha256':sha})


def sample(phase,session_id,request_id,text,**extra):
    d=descriptor();path=Path(os.environ[ENV]).parent/'bindings'/(d['nonce']+'.json')
    b=json.loads(path.read_text())
    if any(b[k]!=v for k,v in {'session_id':session_id,'request_id':request_id,'question_sha256':hashlib.sha256(text.encode()).hexdigest(),'nonce':d['nonce']}.items()):
        raise RuntimeError('Question request binding differs')
    pid=os.getpid();started=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19])
    role=os.environ['PACBENCH_PYTHON_CLOCK_ROLE']
    if role=='hermes-gateway' and (pid!=d['gateway_pid'] or started!=d['gateway_start_time']):raise RuntimeError('Question gateway identity differs')
    return {'version':VERSION,'phase':phase,'turn_nonce':d['nonce'],'pid':pid,'process_start_ticks':started,
        'role':role,'home':d['home'],'offset_sha256':d['offset_sha256'],'question_sha256':d['question_sha256'],
        'session_id':session_id,'request_id':request_id,'provider_at':datetime.fromtimestamp(time.time(),timezone.utc).isoformat(),
        'real_at':datetime.fromtimestamp(real_time(),timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),**extra}


def window(stamp):
    d=descriptor();now=datetime.fromisoformat(stamp['provider_at']);at=datetime.fromisoformat(d['at'])
    if not 0<=(now-at).total_seconds()<=5 or d.get('horizon') and now>=datetime.fromisoformat(d['horizon']):
        from .clock_dispatch import ClockWindowError
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
        from .python_provider_clock import VERSION as PYTHON_VERSION
        repo=Path(__file__).resolve().parents[1]
        if CEILING_ENV in env or self.bounded and env.get('PACBENCH_WORKFLOW_CAPTURE')!='pizza-week-conversation-v2':
            raise RuntimeError('Unexpected bounded observation clock scope')
        return {**env,ENV:str(self.folder/'active.json'),**({CEILING_ENV:CEILING_MODE} if self.bounded else {}),'PACBENCH_PYTHON_CLOCK_FILE':str(self.path),
            'PACBENCH_PYTHON_CLOCK_PROTOCOL':PYTHON_VERSION,'PACBENCH_PYTHON_CLOCK_NONCE':self.process_nonce,
            'PACBENCH_DIRECT_CLOCK':env.get('PACBENCH_DIRECT_CLOCK',f'{real_time()},{time.monotonic()}'),
            'PYTHONPATH':str(repo/'pacbench/direct_runtime')+os.pathsep+str(repo)+os.pathsep+env.get('PYTHONPATH','')}
    def prepare(self,owner,prompt,at,horizon=None,*,continuation=None):
        # No caller-supplied boolean substitutes for actual ownership/drain evidence.
        fence=owner.fence();from .hermes_gateway import _identity
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
        nonce=uuid.uuid4().hex;value={'protocol':VERSION,'nonce':nonce,'at':at,'horizon':horizon,
            'checkpoint':self.checkpoint,'home':str(owner.home),'gateway_pid':owner.witness['pid'],
            'gateway_start_time':owner.witness['start_time'],'question_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'offset_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),'drain_sha256':hashlib.sha256(json.dumps(fence,sort_keys=True).encode()).hexdigest(),
            'source_clock_stepped':False,'clock_scope':'isolated Hermes observations only'}
        if self.bounded:value['clock_ceiling']=CEILING_MODE
        if continuation:value.update(kind='continuation',**continuation)
        else:value['kind']='fresh'
        sha=atomic(self.folder/('preludes' if deferred else 'questions')/(nonce+'.json'),value)
        atomic(self.folder/'active.json',{'protocol':VERSION,'nonce':nonce,'sha256':sha,**({'prelude':True} if deferred else {})})
        return value


def adoption(home,env,pid):
    from .python_provider_clock import package_metadata,VERSION as PYTHON_VERSION
    value=json.loads((Path(env['PACBENCH_PYTHON_CLOCK_FILE']).parent/'python-adoptions'/f'{pid}.json').read_text())
    expected={**package_metadata(),'protocol':PYTHON_VERSION,'pid':pid,'turn_nonce':env['PACBENCH_PYTHON_CLOCK_NONCE'],
        'clock_file':env['PACBENCH_PYTHON_CLOCK_FILE'],'home':str(home),'os_clock':'real','tls_clock':'real'}
    if any(value.get(k)!=v for k,v in expected.items()):raise RuntimeError('Persistent process clock adoption differs')
    return value


def probe(home,python,env,role,code,*,timeout=5,args=()):
    child={**env,'PACBENCH_PYTHON_CLOCK_ROLE':role}
    proc=subprocess.run([str(python),'-c',code,*map(str,args)],env=child,cwd=home,capture_output=True,text=True,timeout=timeout)
    return proc


def marker_time(home,python,env):
    r=probe(home,python,env,'gateway-marker-probe','import time,json;from pacbench.direct_accounting import now;print(json.dumps({"wall":time.time(),"billing":now()}))',
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
        if any(r.get(k)!=v for k,v in {'version':VERSION,'turn_nonce':d['nonce'],'session_id':sid,
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
    from .python_provider_clock import expected_metadata,package_metadata,VERSION as PYTHON_VERSION
    metadata=expected_metadata(process['abi']) if remote_runtime else package_metadata()
    for row in [cli,process]:
        if any(row.get(k)!=v for k,v in {**metadata,'protocol':PYTHON_VERSION,'home':d['home'],
            'clock_file':str(folder/'offset'),'os_clock':'real','tls_clock':'real'}.items()):
            raise RuntimeError('Persistent adoption runtime/clock path differs')
    return {'protocol':VERSION,'state':'pass','descriptor':d,'binding':binding,'native_admission':rows,
        'gateway_process_adoption':process,'cli_process_adoption':cli,'canonical_ingestion_clock_changed':False}
