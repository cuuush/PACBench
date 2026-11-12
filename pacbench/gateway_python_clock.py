"""Identity-bound shared Python clock witnesses for a run-owned Hermes gateway."""
from datetime import datetime,timezone
import hashlib,json,os,subprocess,time
from pathlib import Path
from .python_provider_clock import VERSION,PACKAGE_VERSION,PACKAGE_PY_SHA256,PACKAGE_C_SHA256

ROLES={'hermes-gateway','hermes-cli','gateway-control-probe','gateway-marker-probe'}

def start_ticks(pid):
    return int(Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19])

def environment(home,python,env,role):
    repo=Path(__file__).resolve().parents[1]
    expected=(home.parent/'provider-clock/offset').resolve(strict=True)
    paths=env.get('PYTHONPATH','').split(os.pathsep)
    if (role not in ROLES or not env.get('PACBENCH_PYTHON_CLOCK_NONCE') or
        env.get('PACBENCH_PYTHON_CLOCK_PROTOCOL')!=VERSION or
        Path(env.get('PACBENCH_PYTHON_CLOCK_FILE','')).resolve()!=expected or
        env.get('HERMES_HOME')!=str(home) or env.get('LD_PRELOAD') or
        any(k.startswith('FAKETIME') for k in env) or
        [str(Path(p).resolve()) for p in paths[:2]]!=[str(repo/'pacbench/direct_runtime'),str(repo)]):
        raise RuntimeError('Unsupported split-clock gateway environment')
    return {**env,'PACBENCH_PYTHON_CLOCK_ROLE':role}

def adoption(home,python,env,pid,started,role,*,cwd):
    row=json.loads((home.parent/'provider-clock/python-adoptions'/f'{pid}.json').read_text())
    from . import python_provider_clock
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

def probe(home,python,env,role,code,*,timeout=5,args=()):
    child=environment(home,python,env,role)
    # Normal startup intentionally loads the pinned PYTHONPATH sitecustomize.
    # -I/-S would silently bypass the clock and direct accounting bootstrap.
    proc=subprocess.Popen([str(python),'-c',code,*map(str,args)],env=child,cwd=home,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    started=start_ticks(proc.pid)
    try:out,err=proc.communicate(timeout=timeout)
    except BaseException:
        proc.kill();proc.wait();raise
    receipt=adoption(home,python,child,proc.pid,started,role,cwd=home)
    return subprocess.CompletedProcess(proc.args,proc.returncode,out,err),receipt

def marker_time(home,python,env):
    code='import json,time,time_machine;print(json.dumps({"wall":time.time(),"real":time_machine.escape_hatch.time.time()}))'
    result,receipt=probe(home,python,env,'gateway-marker-probe',code)
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
    child=environment(home,python,env,'hermes-cli')
    witness['question_dispatch_at']=dispatch_time(child)
    proc=subprocess.Popen(cmd,env=child,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    started=start_ticks(proc.pid)
    try:out,err=proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill();out,err=proc.communicate()
        raise subprocess.TimeoutExpired(cmd,timeout,output=out,stderr=err)
    except BaseException:
        proc.kill();proc.wait();raise
    finally:
        witness['cli_adoption']=adoption(home,python,child,proc.pid,started,'hermes-cli',cwd=cwd)
    return subprocess.CompletedProcess(cmd,proc.returncode,out,err)

def audit_witness(root,turn,config):
    """Verify saved roles and hashes, never query a provider or a current PID."""
    witness=turn.get('hermes_gateway') or {};nonce=witness.get('turn_nonce')
    if not nonce or witness.get('stopped') is not True:raise ValueError('Missing clean split-clock gateway lifecycle')
    metadata=config['python_clock'];seen=[]
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
    at=datetime.fromisoformat(witness['question_dispatch_at']);requested=datetime.fromisoformat(turn['at'])
    if not 0<=(at-requested).total_seconds()<=5:raise ValueError('Question dispatch missed its scripted window')
    return witness
