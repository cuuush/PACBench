"""Serial native owner with isolated per-question observations and clock audits.

The new observation-clock condition never migrates historical scored r2 or
steps its canonical ingestion clock. Paid execution remains separately gated.
"""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import time
import uuid

from .hermes_gateway import GatewayLifecycleError,_identity,_live,turn_gateway
from .gateway.reuse_runtime import PROTOCOL


NATIVE_CHAT_CLOSE = ('import asyncio,sys;from hermes_cli.gateway_client import connect_gateway;'
                  '\nasync def main():\n async with connect_gateway() as c:\n'
                  '  timeout=float(sys.argv[2]) if len(sys.argv)>2 else None;'
                  '\n  s=await c.rpc("session.resume",_timeout=timeout,session_id=sys.argv[1]);'
                  '\n  await c.rpc("session.detach",_timeout=timeout,session_id=sys.argv[1],subscription_id=s["subscription_id"],end_reason="cli_close")'
                  '\nasyncio.run(main())')


def native_children(pid):
    """Linux PPID witness; does not require CONFIG_CHECKPOINT_RESTORE."""
    children=[]
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():continue
        try:fields=(process/'stat').read_text().rsplit(')',1)[1].split()
        except (FileNotFoundError,ProcessLookupError):continue
        if int(fields[1])==pid and fields[0] not in {'Z','X'}:children.append(int(process.name))
    return children


def tree_manifest(root):
    root=Path(root);files={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():raise GatewayLifecycleError('Observation snapshots cannot contain links')
        if path.is_file():files[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    digest=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
    return {'sha256':digest,'files':files}


def clone_observation(checkpoint,destination):
    """Fork an already quiescent checkpoint; never copy an open native store.

    This verifies byte fidelity, not ingestion or native database portability.
    The caller must retain its separate native readiness/quiescence witness.
    """
    checkpoint=Path(checkpoint);before=tree_manifest(checkpoint)
    shutil.copytree(checkpoint,destination)
    if tree_manifest(checkpoint)!=before or tree_manifest(destination)!=before:
        raise GatewayLifecycleError('Checkpoint changed while cloning')
    return {'checkpoint':before['sha256'],'snapshot_manifest':before,'workspace':str(Path(destination).resolve())}


def atomic(path,value):
    raw=(json.dumps(value,sort_keys=True)+'\n').encode();temporary=path.with_suffix('.tmp')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)
    return hashlib.sha256(raw).hexdigest()


class RecoveryHorizonExceeded(GatewayLifecycleError):
    """A drained observation passed its recovery window before dispatch."""


class PersistentGateway:
    """A run-owned process; fresh sessions via native CLI, no implicit retries.

    Caller supplies a new isolated native observation backend for each question.
    Native ReMe HTTP, pinned Mem0 OSS and explicit MemCal review trees have
    separate bindings. Ended rows never prove drained writes. Failures poison
    this owner until explicit exit; incomplete live qualification stays gated.
    """
    def __init__(self,home,python,env,*,cwd,checkpoint,cleanup_timeout=45,observation_clock=None,provider='reme',canary=None,full_run=None):
        self.home=Path(home).resolve();self.python=Path(python);self.env=dict(env);self.cwd=Path(cwd)
        self.checkpoint=checkpoint;self.cleanup_timeout=cleanup_timeout
        self.provider=provider
        self.canary=canary
        self.full_run=full_run
        if canary is not None and full_run is not None:raise GatewayLifecycleError('Conflicting execution capabilities')
        if provider not in {'reme','mem0','memcal','memos','openviking','hindsight','supermemory','honcho','cognee'}:raise GatewayLifecycleError('Unknown native observation provider')
        self.alias=None
        workflow_memcal = provider == 'memcal' and self.env.get('PACBENCH_WORKFLOW_CAPTURE') == 'pizza-week-conversation-v2'
        if workflow_memcal:
            # The collector and continuing chat must share the same run-owned store.
            store = self.home.parent / 'memcal-home'
            if self.env.get('MEMCAL_HOME') != str(store) or store.resolve() != store or not store.is_dir():
                raise GatewayLifecycleError('Continuing MemCal chat requires its run-owned native store')
        elif provider in {'mem0','memcal','memos'}:
            self.alias=self.home.parent/(provider+'-observation')
            self.env[{'mem0':'MEM0_DIR','memcal':'MEMCAL_HOME','memos':'MEMOS_HOME'}[provider]]=str(self.alias)
        if provider=='memcal' and observation_clock is not None:self.env.pop('MEMCAL_TODAY',None)
        from .gateway.preclose import ENV,CONDITION
        self.preclose_condition=self.env.get(ENV)
        if self.preclose_condition is not None and (self.preclose_condition!=CONDITION or provider!='reme' or observation_clock is None):
            raise GatewayLifecycleError('Unknown pre-close lifecycle condition or missing exact clock')
        self.first_native_failure=None
        self.failed=False;self.sessions=set();self.endpoints=set();self.workspaces=set();self.witness=None
        self.session_context={}
        self.observation_clock=observation_clock
        if observation_clock is not None:
            if observation_clock.home!=self.home or observation_clock.checkpoint!=checkpoint:
                raise GatewayLifecycleError('Observation clock does not bind this owner/checkpoint')
            if self.env.get('PACBENCH_PYTHON_CLOCK_FILE'):
                raise GatewayLifecycleError('Canonical ingestion clock cannot become a reusable observation clock')
            self.env=observation_clock.environment(self.env)
        if (self.env.get('PACBENCH_PYTHON_CLOCK_FILE') or self.env.get('LD_PRELOAD') or
            any(k.startswith('FAKETIME') for k in self.env) or (self.home.parent/'provider-clock.json').exists()):
            if observation_clock is None or self.env.get('LD_PRELOAD') or any(k.startswith('FAKETIME') for k in self.env) or (self.home.parent/'provider-clock.json').exists():
                raise GatewayLifecycleError('Persistent simulated-clock scoring requires a new versioned request-bound clock protocol')
        if self.env.get('PACBENCH_DIRECT')=='1':
            from pacbench.workflows import capability as workflow_capability
            if self.env.get(workflow_capability.ENV):
                if canary or full_run:
                    raise GatewayLifecycleError('Conflicting workflow and legacy gateway owners')
                workflow_capability.owner(self.env,self.home,checkpoint,provider,observation_clock)
            elif canary is None and full_run is None:raise GatewayLifecycleError('Funded scored execution cannot use the unqualified fixed-clock reuse condition')
        if canary is not None:
            from .canary_capability import owner
            owner(canary,self.env,self.home,checkpoint,provider,observation_clock)
        if full_run is not None:
            from .full_run_capability import owner
            owner(full_run,self.env,self.home,checkpoint,provider,observation_clock)
        if not checkpoint or not self.env.get('PACBENCH_NOW'):
            raise GatewayLifecycleError('An explicit fixed checkpoint is required')
        if self.env.get('PACBENCH_NO_BUILTIN_MEMORY')!='1' or self.env.get('PACBENCH_NO_SESSION_SEARCH')!='1':
            raise GatewayLifecycleError('Persistent observations require disabled builtin memory and session search')
        self.env['PACBENCH_GATEWAY_REUSE']=PROTOCOL
        self.env['PACBENCH_CONTEXT_TRACE']=str(self.home.parent/'reuse-context.jsonl')
        repo=Path(__file__).resolve().parents[1]
        if observation_clock is None:self.env['PYTHONPATH']=str(repo)+os.pathsep+self.env.get('PYTHONPATH','')
        self.config_path=self.home/'config.yaml'
        self.config_sha256=hashlib.sha256(self.config_path.read_bytes()).hexdigest()
        self.environment_sha256=hashlib.sha256(json.dumps(self.env,sort_keys=True).encode()).hexdigest()
        tree=repo/'vendor/hermes-isolated'
        pin=next(line.split('=',1)[1] for line in (repo/'vendor/HERMES_PIN').read_text().splitlines() if line.startswith('HERMES_PIN='))
        def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],text=True)
        if (git(tree,'rev-parse','HEAD').strip()!=pin or
            git(tree,'diff','--abbrev=8','HEAD')!=(repo/'patches/hermes-isolated.diff').read_text() or
            git(tree,'ls-files','--others','--exclude-standard').strip()):
            raise GatewayLifecycleError('Persistent owner requires the exact pinned Hermes isolation tree')
        from .observation_adapters import native_source
        self.pins={'hermes':pin,**native_source(provider,self.home,repo,self.env,self.python),
            'hermes_isolation_sha256':hashlib.sha256((repo/'patches/hermes-isolated.diff').read_bytes()).hexdigest()}
        self.runtime_observer='memos_reuse_runtime' if provider in {'memos','openviking'} else 'reuse_runtime'
        self.pins['reuse_observer_sha256']=hashlib.sha256((repo/'pacbench/gateway'/(self.runtime_observer+'.py')).read_bytes()).hexdigest()
        if provider in {'hindsight','supermemory','honcho','cognee'}:
            self.runtime_observer=provider+'_reuse_runtime'
            self.pins['provider_observer_adapter_sha256']=hashlib.sha256((repo/'pacbench/gateway'/(self.runtime_observer+'.py')).read_bytes()).hexdigest()
            self.pins['reuse_observer_sha256']=hashlib.sha256((repo/'pacbench/gateway/memos_reuse_runtime.py').read_bytes()).hexdigest()

    def __enter__(self):
        self.owner=turn_gateway(self.home,self.python,self.env,cwd=self.cwd,
            shutdown_timeout=self.cleanup_timeout,runtime_module='pacbench.gateway.'+self.runtime_observer)
        self.witness=self.owner.__enter__();return self

    def __exit__(self,*exc):return self.owner.__exit__(*exc)

    def fence(self,session=None):
        deadline=time.monotonic()+self.cleanup_timeout
        while True:
            try:
                _identity(self.home,self.witness['pid'])
                if (self.home/'reuse-observer-error.json').exists():raise GatewayLifecycleError('Native observer failed; preserve its diagnostic receipt')
                path=self.home/'reuse-observer.json'
                if path.exists():
                    value=json.loads(path.read_text())
                    if (value.get('protocol')!=PROTOCOL or value.get('pid')!=self.witness['pid'] or
                        value.get('start_time')!=self.witness['start_time'] or
                        value.get('observer_sha256')!=self.pins['reuse_observer_sha256']):
                        raise GatewayLifecycleError('Reuse observer identity changed')
                    # Preserve the first native failure before a late worker
                    # changes the observer. This is evidence, never readiness.
                    if value.get('native_failures') and getattr(self,'first_native_failure',None) is None:
                        self.first_native_failure=value
                    rows=value['sessions']
                    if ((session is None or any(r['session_id']==session for r in rows))
                        and not value.get('native_release_threads') and not value.get('native_memory_threads')
                        and not value.get('native_children')
                        and not value.get('native_failures') and all(r['clean'] for r in rows)):
                        children=native_children(self.witness['pid'])
                        if not children:return {**value,'native_children':children,'child_witness':'read-only Linux PPID scan after native drain'}
            except (OSError,ValueError,KeyError,TypeError,GatewayLifecycleError) as exc:
                self.failed=True
                raise GatewayLifecycleError('Native reuse fence evidence unavailable; owner requires checkpoint recovery') from exc
            if time.monotonic()>=deadline:
                self.failed=True
                raise GatewayLifecycleError('Native writer drain unverified; next question refused')
            time.sleep(.02)

    def adopt_session(self,binding,session_id,observation,question):
        from .checkpoint_sessions import verify
        row=verify(binding,self,session_id)
        if session_id in self.session_context:raise GatewayLifecycleError('Native session already admitted')
        self.session_context[session_id]={'initial_at':row['session_started_at'],'session_started_at':row['session_started_at'],
            'history_sha256':row['history']['sha256'],'user_texts':row['history']['user_texts'],
            'observation':json.loads(json.dumps(observation)),'followups':0,'authored_pending':question}

    def run(self,prompt,*,observation,timeout=120,at=None,horizon=None,session_id=None,authored_resume=False):
        """Drive one exact question; caller retains a typed provenance capture.

        ``observation`` is the verified isolated native-service receipt: a unique
        endpoint, workspace, checkpoint hash, and its local service PID. The
        source fixture and provider-native settings must be checked separately.
        """
        if self.failed:raise GatewayLifecycleError('Owner requires checkpoint recovery; no replay')
        if self.canary is not None:
            from .canary_capability import question
            question(self.canary,self,prompt,at,observation,session_id)
        if self.full_run is not None:
            from .full_run_capability import question
            question(self.full_run,self,prompt,at,observation,session_id)
        if (hashlib.sha256(self.config_path.read_bytes()).hexdigest()!=self.config_sha256 or
            hashlib.sha256(json.dumps(self.env,sort_keys=True).encode()).hexdigest()!=self.environment_sha256):
            self.failed=True
            raise GatewayLifecycleError('Persistent owner model, instructions or environment changed; restart at a verified checkpoint')
        drain=self.fence()
        continuation=None;prior_users=[];session_origin=None
        if session_id:
            from .checkpoint_clock import history
            context=self.session_context.get(session_id)
            if not context or (context['followups'] and not authored_resume) or observation!=context['observation']:
                raise GatewayLifecycleError('Follow-up requires this question\'s exact session/store and a single unused continuation')
            pending=context.get('authored_pending')
            if authored_resume:
                if not pending or pending['at']!=at or pending['prompt']!=prompt:
                    raise GatewayLifecycleError('Authored native continuation differs from frozen question')
            elif pending:raise GatewayLifecycleError('Authored checkpoint continuation must be explicit')
            previous=history(self.home,session_id)
            if previous['user_texts']!=context['user_texts'] or previous['sha256']!=context['history_sha256']:
                raise GatewayLifecycleError('Unexpected sender/history before follow-up')
            prior_users=previous['user_texts']
            session_origin=context.get('session_started_at',context['initial_at'])
            if authored_resume:context.pop('authored_pending')
            else:
                context['followups']+=1;horizon=context['horizon'];at=context['initial_at']
            continuation={'initial_at':session_origin,'continued_session_id':session_id,'history_sha256':previous['sha256'],
                'continuation_reason':'authored-checkpoint' if authored_resume else 'omission-recovery'}
        if self.provider=='honcho' and continuation and not authored_resume:
            from provider_adapters.honcho_clock import load as load_clock
            native_clock,_=load_clock(Path(observation['service_root']))
            if horizon and native_clock.now()>=datetime.fromisoformat(horizon):
                raise RecoveryHorizonExceeded('Native Honcho recovery clock crossed the next scripted question horizon; no follow-up dispatched')
        if observation.get('checkpoint')!=self.checkpoint or not Path(observation.get('workspace','')).is_dir():
            raise GatewayLifecycleError('A verified checkpoint observation backend is required')
        if observation.get('workspace') in self.workspaces and (not continuation or authored_resume):
            raise GatewayLifecycleError('A fresh verified checkpoint observation backend is required')
        if self.provider in {'reme','openviking','supermemory','honcho','cognee'} and (not continuation or authored_resume) and (observation.get('endpoint') in self.endpoints or not observation.get('endpoint') or
            type(observation.get('pid')) is not int or observation['pid']<=1 or not _live(observation['pid'])):
            raise GatewayLifecycleError('A distinct native ReMe service is required')
        manifest=tree_manifest(observation['workspace'])
        startup=None
        if not continuation or authored_resume:
            if (self.provider=='openviking' and observation.get('native_resource_uris') and
                    observation['snapshot_manifest']['sha256']==self.checkpoint):
                from .providers.openviking import verify_open_clone
                startup=verify_open_clone(observation,manifest)
            elif self.provider=='supermemory' and observation.get('documents') and observation['snapshot_manifest']['sha256']==self.checkpoint:
                import re
                before=observation['snapshot_manifest']['files'];after=manifest['files']
                changed={k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)}
                runtime={'data','data-shm','data-wal','.instance.lock',
                         'runtime/rivet/engine.json','runtime/rivet/engine.pid'}
                unexpected=sorted(path for path in changed-runtime if not re.fullmatch(
                    r'runtime/rivet/data/rivet-engine/db/(?:CURRENT|LOCK|LOG(?:\.old\.[0-9]+)?|MANIFEST-[0-9]+|OPTIONS-[0-9]+|[0-9]+\.(?:log|sst))',path))
                if unexpected:
                    raise GatewayLifecycleError('Supermemory startup changed unexpected store files: '+', '.join(unexpected))
                startup={'changed_storage_files':sorted(changed),'native_documents':observation['documents']}
            elif manifest!=observation.get('snapshot_manifest') or manifest['sha256']!=self.checkpoint:
                raise GatewayLifecycleError('Observation is not an exact checkpoint clone')
        from .observation_adapters import plan,switch_alias
        config_path=self.home/('reme/config.json' if self.provider=='reme' else 'mem0.json')
        config=json.loads(config_path.read_text()) if self.provider in {'mem0','reme'} else {}
        binding=plan(self.provider,config,observation['workspace'],endpoint=observation.get('endpoint'))
        if self.alias:
            target=Path(binding['state_alias_target']);target.mkdir(parents=True,exist_ok=True)
            if self.alias.is_symlink() and (not continuation or authored_resume):switch_alias(self.alias,target,drain)
            elif self.alias.is_symlink() and continuation:
                if self.alias.resolve()!=target:raise GatewayLifecycleError('Follow-up native binding changed')
            elif self.alias.exists():raise GatewayLifecycleError('Refuse to replace an existing native store')
            else:self.alias.symlink_to(target,target_is_directory=True)
        if self.provider in {'mem0','reme'}:atomic(config_path,binding['config'])
        if self.provider=='honcho':
            from provider_adapters.honcho_checkpoint import bind_profile
            bind_profile(self.home, observation, binding['endpoint'])
        if self.provider=='cognee':
            from provider_adapters.cognee_checkpoint import bind_profile
            bind_profile(self.home, observation, binding['endpoint'])
        if self.provider=='hindsight':
            import re
            bank=observation.get('bank','')
            if not re.fullmatch('pacbench-q-[a-f0-9]{20}',bank) or not observation.get('clone_operation'):
                raise GatewayLifecycleError('Hindsight observation must use its verified native clone')
            config_path=self.home/'hindsight/config.json'
            native_config=json.loads(config_path.read_text())
            if native_config.get('api_url')!=binding['endpoint'] or native_config.get('mode')!='local_external':
                raise GatewayLifecycleError('Hindsight observation endpoint or native mode differs')
            atomic(config_path,{**native_config,'bank_id':bank})
        if self.provider=='supermemory':
            key=self.env['SUPERMEMORY_API_KEY']
            if hashlib.sha256(key.encode()).hexdigest()!=observation['open_api_key_sha256']:
                raise GatewayLifecycleError('Supermemory observation key differs from the local native store')
            atomic(self.home/'supermemory.json',{'base_url':binding['endpoint'],'container_tag':observation['namespace']})
        if self.provider=='openviking':
            if not binding['endpoint'].startswith('http://127.0.0.1:'):
                raise GatewayLifecycleError('OpenViking endpoint is outside the local observation')
            envfile=self.home/'.env'
            if envfile.is_symlink() or (envfile.exists() and not envfile.read_text().startswith('OPENVIKING_ENDPOINT=http://127.0.0.1:')):
                raise GatewayLifecycleError('Unexpected native OpenViking profile environment')
            temporary=envfile.with_suffix('.observation-next')
            temporary.write_text('OPENVIKING_ENDPOINT='+binding['endpoint']+'\n')
            temporary.chmod(0o600);temporary.replace(envfile)
        if self.provider in {'reme','openviking','supermemory','honcho','cognee'}:self.endpoints.add(observation['endpoint'])
        self.workspaces.add(observation['workspace']);attempt=uuid.uuid4().hex
        folder=self.home.parent/'reuse-turns'/attempt;folder.mkdir(parents=True,mode=0o700)
        start=time.monotonic();started=datetime.now(timezone.utc).isoformat()
        command=[str(self.python),'-m','hermes_cli.main','chat','-q',prompt,'-Q','--format','stream-json']
        question_at=at or self.env['PACBENCH_NOW'];clock_descriptor=None
        initial_at=question_at
        if continuation:
            command.extend(['--resume',session_id])
            if self.observation_clock and not authored_resume:
                question_at=max(datetime.fromtimestamp(time.time()+float(self.observation_clock.path.read_text()),timezone.utc),datetime.fromisoformat(initial_at)).astimezone(datetime.fromisoformat(initial_at).tzinfo).isoformat()
                if self.provider=='honcho':
                    question_at=max(datetime.fromisoformat(question_at),native_clock.now()).isoformat()
        child_env=self.env
        try:
            if self.observation_clock is not None:
                clock_descriptor=self.observation_clock.prepare(self,prompt,question_at,horizon,continuation=continuation)
                child_env={**self.env,'PACBENCH_PYTHON_CLOCK_ROLE':'hermes-cli'}
            elif question_at!=self.env['PACBENCH_NOW']:
                raise GatewayLifecycleError('Different question times require the request-bound observation clock')
        except Exception as exc:
            self.failed=True
            atomic(folder/'failed.json',{'protocol':PROTOCOL,'state':'blocked-before-dispatch',
                'error_type':type(exc).__name__,'automatic_replay':False,'model_request_dispatched':False})
            raise
        context_trace=Path(self.env['PACBENCH_CONTEXT_TRACE']);context_before=context_trace.read_bytes() if context_trace.exists() else b''
        from .harness import provider_trace_snapshot
        trace_env=self.env
        if self.provider=='honcho':
            trace_env={**self.env,'PACBENCH_HONCHO_TRACE_FILE':str(Path(observation['service_root'])/'honcho-reasoning-traces.jsonl')}
        provider_before=provider_trace_snapshot(trace_env)
        try:
            proc=subprocess.run(command,env=child_env,cwd=self.cwd,capture_output=True,text=True,timeout=timeout)
            from .harness import _parse_turn
            turn=_parse_turn(prompt,question_at,proc,time.monotonic()-start)
            if proc.returncode and self.env.get('PACBENCH_R2_LIVE_SCOPE'):
                stderr=proc.stderr
                for name in ('PACBENCH_DIRECT_UPSTREAM_KEY','OPENROUTER_API_KEY','OPENAI_API_KEY'):
                    secret=child_env.get(name)
                    if secret:stderr=stderr.replace(secret,'<redacted>')
                atomic(folder/'cli-error.private.json',{'stderr':stderr[-8000:]})
            atomic(folder/'native-result.json',{'native_stream':proc.stdout,'returncode':proc.returncode,
                'session_id':turn.session_id,'provenance_verified':False})
            if turn.returncode!=0 or not turn.answer or not turn.session_id or (turn.session_id in self.sessions and not continuation) or (continuation and turn.session_id!=session_id):
                raise GatewayLifecycleError('Native answer failed or fresh session identity missing')
            self.sessions.add(turn.session_id)
            from .context_isolation import actual_context
            limit=time.monotonic()+min(5,self.cleanup_timeout)
            while True:
                provenance=actual_context(self.home,turn.session_id,self.cwd,capture=True)
                if provenance.get('state')=='pass':break
                if time.monotonic()>=limit:
                    atomic(folder/'context-failure.json',provenance)
                    raise GatewayLifecycleError('Native context isolation failed')
                time.sleep(.02)
            turn.context_isolation=provenance
            # A fresh native transcript must contain precisely this question.
            import sqlite3
            with sqlite3.connect(f'file:{self.home/"state.db"}?mode=ro',uri=True) as db:
                questions=db.execute('SELECT content FROM messages WHERE session_id=? AND role="user"',(turn.session_id,)).fetchall()
                db.row_factory=sqlite3.Row
                visible=[dict(row) for row in db.execute('SELECT role,content,api_content,tool_name FROM messages WHERE session_id=? ORDER BY id',(turn.session_id,))]
            if questions!=[(q,) for q in [*prior_users,prompt]]:raise GatewayLifecycleError('Session transcript contains an unexpected sender or altered input')
            from .harness import capture_prefetch,capture_prefetch_provenance,capture_provider_traces,_recover_tool_results
            capture_prefetch(turn,context_trace,context_before);capture_prefetch_provenance(turn,self.env)
            _recover_tool_results(turn,self.home/'state.db');capture_provider_traces(turn,trace_env,provider_before)
            from dataclasses import asdict
            clock_evidence=None
            if clock_descriptor:
                from .checkpoint_clock import audit_question
                if self.env.get('PACBENCH_R2_LIVE_SCOPE'):
                    clock_descriptor=json.loads((self.observation_clock.folder/'questions'/(clock_descriptor['nonce']+'.json')).read_text())
                    question_at=clock_descriptor['at']
                clock_evidence=audit_question(self.observation_clock.folder,clock_descriptor,turn,self.witness)
                current=json.loads((self.home/'reuse-observer.json').read_text()).get('application_clock',{})
                if (current.get('nonce')!=clock_descriptor['nonce'] or
                    datetime.fromisoformat(current['now'])!=datetime.fromisoformat(question_at)):
                    raise GatewayLifecycleError('Native application clock does not match the exact question')
                clock_evidence['native_application_clock']=current
            turn.session_started_at=session_origin or initial_at
            turn.provider_clock=({'version':'checkpoint-observation-clock-v1','role':'recovery' if continuation and not authored_resume else 'scripted',
                'requested_at':initial_at,'actual_start_at':clock_evidence['native_admission'][-1]['provider_at']} if clock_evidence else None)
            # Preserve native stream and the actual saved context together before cleanup.
            atomic(folder/'answer-ready.json',{'protocol':PROTOCOL,'attempt_id':attempt,
                'session_id':turn.session_id,'gateway_pid':self.witness['pid'],'checkpoint':self.checkpoint,
                'started_at':started,'answer_ready_at':datetime.now(timezone.utc).isoformat(),
                'answer_ready_latency_s':time.monotonic()-start,'native_stream':proc.stdout,
                'context_isolation':provenance,'observation':observation,'native_turn':asdict(turn),
                'observation_native_startup':startup,
                'answer_visible_records':visible,'pins':self.pins,'config_sha256':self.config_sha256,
                'observation_clock':clock_evidence,
                'retention_credit':'ungraded experimental lifecycle condition'})
            ready=time.monotonic()
            # Native supported close. This RPC deliberately does NOT prove drain.
            code=NATIVE_CHAT_CLOSE
            preclose=None
            if self.preclose_condition:
                from .gateway.preclose import drain_then_close
                preclose=drain_then_close(self,turn,folder,clock_evidence)
            else:
                # Native Cognee can still finish a cache write while ending the
                # session. Keep the close RPC inside its audited cleanup window.
                timeout_arg=[str(self.cleanup_timeout - 30)] if self.provider=='cognee' else []
                closed=subprocess.run([str(self.python),'-c',code,turn.session_id,*timeout_arg],env=self.env,cwd=self.cwd,
                    capture_output=True,text=True,timeout=self.cleanup_timeout)
                if closed.returncode:
                    last=closed.stderr.splitlines()[-1:] or ['']
                    import re
                    error_type=re.match(r'^[A-Za-z_][A-Za-z_0-9.]+(?=:|$)',last[0])
                    atomic(folder/'native-close-error.json',{'returncode':closed.returncode,
                           'error_type':error_type.group() if error_type else None})
                    raise GatewayLifecycleError('Native close failed')
            drain=self.fence(turn.session_id)
            if self.provider=='openviking' and not any(p.get('endpoint')==observation['endpoint'] for s in drain['sessions'] if s['session_id']==turn.session_id for p in s['providers'] if p['name']=='openviking'):
                raise GatewayLifecycleError('Native OpenViking observation endpoint was not used')
            if self.provider=='honcho':
                from provider_adapters.honcho_checkpoint import verify_turn
                drain['native_honcho_writes']=verify_turn(observation,turn,drain)
            if self.provider=='cognee':
                from provider_adapters.cognee_checkpoint import verify_turn
                drain['native_cognee_writes']=verify_turn(observation,turn,drain)
            if self.provider=='hindsight':
                if not any(p.get('bank')==observation['bank'] and p.get('endpoint')==observation['endpoint']
                           for s in drain['sessions'] if s['session_id']==turn.session_id
                           for p in s['providers'] if p['name']=='hindsight'):
                    raise GatewayLifecycleError('Native Hindsight observation bank was not used')
                from .providers.hindsight.evidence import settled_operations
                drain['native_bank_operations']=settled_operations(observation['endpoint'],observation['bank'])
            if self.provider=='supermemory':
                if not any(p.get('namespace')==observation['namespace'] and p.get('endpoint')==observation['endpoint']
                           for s in drain['sessions'] if s['session_id']==turn.session_id
                           for p in s['providers'] if p['name']=='supermemory'):
                    raise GatewayLifecycleError('Native Supermemory observation store was not used')
                from .providers.supermemory.observation import wait_for_settled_checkpoint
                drain['native_document_statuses']=wait_for_settled_checkpoint(observation['endpoint'],self.env['SUPERMEMORY_API_KEY'],observation['namespace'])
            preclose_binding=({'path':str(folder/'preclose.json'),'sha256':hashlib.sha256((folder/'preclose.json').read_bytes()).hexdigest()} if preclose else None)
            atomic(folder/'cleanup.json',{'protocol':PROTOCOL,'state':'drained','post_answer_cleanup_s':time.monotonic()-ready,
                'native_observation':drain,'preclose':preclose_binding})
            from .context_isolation import memory_delivery
            delivery=memory_delivery(self.home,turn.session_id,prompt,turn.prefetch_context)
            turn.hermes_gateway={'version':'checkpoint-session-v1','pid':self.witness['pid'],'start_time':self.witness['start_time'],
                'clock_evidence':clock_evidence,'receipt':str(folder),'native_drain':'complete','fresh_session':not bool(continuation),'authored_checkpoint_resume':authored_resume,'native_result_token_scope':'turn',
                'prior_user_count':len(prior_users),'new_user_count':1,'memory_delivery':delivery,
                'close_condition':self.preclose_condition,'preclose':preclose_binding,
                'answer_ready_sha256':hashlib.sha256((folder/'answer-ready.json').read_bytes()).hexdigest(),
                'cleanup_sha256':hashlib.sha256((folder/'cleanup.json').read_bytes()).hexdigest(),
                'answer_visible_capture':'complete' if delivery['state'] in {'confirmed','empty'} else 'unknown'}
            turn.lifecycle_timing={'protocol':'turn-lifecycle-timing-v1','runner_wall_s':time.monotonic()-start,
                'answer_ready_latency_s':ready-start,'post_answer_cleanup_s':time.monotonic()-ready,'gateway_persisted':True,
                'preclose_wait_s':preclose['preclose_wait_s'] if preclose else 0}
            from .checkpoint_clock import history
            history_sha=history(self.home,turn.session_id)['sha256']
            if not continuation:self.session_context[turn.session_id]={'initial_at':initial_at,'horizon':horizon,'history_sha256':history_sha,
                'observation':json.loads(json.dumps(observation)),'user_texts':[prompt],'followups':0}
            else:
                context=self.session_context[turn.session_id]
                context['user_texts'].append(prompt);context['history_sha256']=history_sha
                if authored_resume:context.update(initial_at=initial_at,session_started_at=session_origin,horizon=horizon,followups=0)
            # Commit before caller-side usage reporting can fail.
            atomic(folder/'completed-turn.json',asdict(turn))
            return turn,folder
        except BaseException as exc:
            self.failed=True
            if isinstance(exc,subprocess.TimeoutExpired):
                def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
                atomic(folder/'interrupted-result.json',{'native_stream':decoded(exc.stdout),
                    'outcome':'unknown; native process may have accepted work','automatic_replay':False})
            evidence={'first_native_failure':getattr(self,'first_native_failure',None)}
            if exc.__cause__ is not None:
                cause=exc.__cause__
                evidence['cause']={'type':type(cause).__name__,'errno':getattr(cause,'errno',None)}
                if isinstance(cause,GatewayLifecycleError):evidence['cause']['reason']=str(cause)[:260]
            try:
                latest=json.loads((self.home/'reuse-observer.json').read_text())
                if (latest.get('protocol')!=PROTOCOL or latest.get('pid')!=self.witness['pid'] or
                        latest.get('start_time')!=self.witness['start_time'] or
                        latest.get('observer_sha256')!=self.pins['reuse_observer_sha256']):
                    raise ValueError('Observer identity differs')
                evidence['latest_validated_observation']=latest
            except (OSError,ValueError,KeyError,TypeError) as capture_error:
                evidence['latest_observation_error']=type(capture_error).__name__
            diagnostic=folder/'native-lifecycle-failure.json';diagnostic_sha=atomic(diagnostic,evidence)
            atomic(folder/'failed.json',{'protocol':PROTOCOL,'state':'blocked','error_type':type(exc).__name__,
                'reason':str(exc)[:300] if isinstance(exc,GatewayLifecycleError) else None,
                'lifecycle_evidence':{'path':str(diagnostic),'sha256':diagnostic_sha},
                'answer_ready_preserved':(folder/'answer-ready.json').exists(),'automatic_replay':False})
            raise
