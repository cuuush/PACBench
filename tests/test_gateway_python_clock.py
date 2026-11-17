"""Clock-role identities cannot be substituted by readiness helper processes."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json
import pytest
from pacbench import gateway_python_clock as clock,python_provider_clock as runtime,clock_dispatch as dispatch

def data(tmp_path):
    home=tmp_path/'hermes-home';home.mkdir();folder=tmp_path/'provider-clock';folder.mkdir()
    offset=folder/'offset';offset.write_text('+0.000000\n')
    repo=Path(runtime.__file__).resolve().parents[1]
    env={'HERMES_HOME':str(home),'PACBENCH_PYTHON_CLOCK_FILE':str(offset),
        'PACBENCH_PYTHON_CLOCK_PROTOCOL':runtime.VERSION,'PACBENCH_PYTHON_CLOCK_NONCE':'this-turn',
        'PYTHONPATH':str(repo/'pacbench/direct_runtime')+':'+str(repo)}
    row={'protocol':runtime.VERSION,'version':runtime.PACKAGE_VERSION,'python_sha256':runtime.PACKAGE_PY_SHA256,
        'bootstrap_sha256':hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest(),
        'pid':42,'process_start_ticks':123,'turn_nonce':'this-turn','role':'hermes-gateway',
        'home':str(home),'cwd':str(tmp_path),'clock_file':str(offset),
        'os_clock':'real','tls_clock':'real','python':'/verified/python','abi':'cpython-314',
        'extension_sha256':runtime.PACKAGE_C_SHA256['cpython-314']}
    return home,env,row

@pytest.mark.parametrize('key,bad',[('role','gateway-control-probe'),('turn_nonce','other-turn'),
    ('process_start_ticks',124),('pid',43),('python','/other-venv/python'),('extension_sha256','wrong'),('home','/other-home')])
def test_wrong_adoption_is_rejected(tmp_path,key,bad):
    home,env,row=data(tmp_path);directory=tmp_path/'provider-clock/python-adoptions';directory.mkdir()
    path=directory/'42.json';path.write_text(json.dumps(row))
    assert clock.adoption(home,Path('/verified/python'),env,42,123,'hermes-gateway',cwd=tmp_path)==row
    row[key]=bad;path.write_text(json.dumps(row))
    with pytest.raises(RuntimeError):clock.adoption(home,Path('/verified/python'),env,42,123,'hermes-gateway',cwd=tmp_path)

@pytest.mark.parametrize('change',[{'LD_PRELOAD':'/fake.so'},{'PACBENCH_PYTHON_CLOCK_NONCE':''},{'PYTHONPATH':'/untrusted'}])
def test_unverified_bootstrap_cannot_launch(tmp_path,change):
    home,env,row=data(tmp_path);env.update(change)
    with pytest.raises(RuntimeError):clock.environment(home,Path('/verified/python'),env,'hermes-cli')

def test_dispatch_cannot_hide_gateway_startup_delay(tmp_path,monkeypatch):
    home,env,row=data(tmp_path);monkeypatch.setattr(clock.time,'time',lambda:1000.)
    env['PACBENCH_NOW']=datetime.fromtimestamp(999,timezone.utc).isoformat()
    assert datetime.fromisoformat(clock.dispatch_time(env)).timestamp()==1000
    env['PACBENCH_NOW']=datetime.fromtimestamp(994,timezone.utc).isoformat()
    with pytest.raises(RuntimeError,match='no question sent'):clock.dispatch_time(env)

def test_mandatory_v5_gateway_and_cli_are_distinct(tmp_path):
    home,env,row=data(tmp_path);config={'python_clock':{'protocol':runtime.VERSION,'version':runtime.PACKAGE_VERSION,
        'python_sha256':runtime.PACKAGE_PY_SHA256,'bootstrap_sha256':row['bootstrap_sha256'],'extensions_by_abi':runtime.PACKAGE_C_SHA256}}
    cli={**row,'role':'hermes-cli','pid':43,'process_start_ticks':124}
    at='2026-09-11T09:00:00-04:00';turn={'at':at,'context_isolation':{'workspace':str(tmp_path)},
        'hermes_gateway':{'turn_nonce':'this-turn','stopped':True,'pid':42,'start_time':123,
            'gateway_adoption':row,'cli_adoption':cli,'question_dispatch_at':at,'question_accepted_at':at,
            'clock_offset':'+0.000000\n','offset_sha256':hashlib.sha256(b'+0.000000\n').hexdigest()}}
    turn.update(prompt='q',session_id='session')
    config['dispatch_condition']={'version':dispatch.VERSION,'startup':dispatch.PRELUDE,'lead_seconds':60,'source_sha256':'fixture-guard'}
    turn['hermes_gateway']['native_submission']=[]
    for index,phase in enumerate(['client-submit','gateway-received','pre-commit','post-commit']):
        who=cli if index==0 else row
        turn['hermes_gateway']['native_submission'].append({
            'version':dispatch.VERSION,'phase':phase,'source_sha256':'fixture-guard','turn_nonce':'this-turn',
            'pid':who['pid'],'process_start_ticks':who['process_start_ticks'],'role':who['role'],
            'home':str(home),'offset_sha256':turn['hermes_gateway']['offset_sha256'],
            'question_sha256':hashlib.sha256(b'q').hexdigest(),'session_id':'session','request_id':'native-request',
            'provider_at':at,'real_at':at,'monotonic_ns':index,'in_window':True,'admission_id':'native-admission'})
    assert clock.audit_witness(tmp_path,turn,config)
    turn['hermes_gateway']['cli_adoption']={**cli,'role':'gateway-marker-probe'}
    with pytest.raises(ValueError):clock.audit_witness(tmp_path,turn,config)

def test_cli_uses_exact_gateway_python_instead_of_console_script_shebang(tmp_path,monkeypatch):
    home,env,row=data(tmp_path);env['PACBENCH_NOW']=datetime.now(timezone.utc).isoformat()
    python=tmp_path/'linked-venv/bin/python';captured=[]
    class Process:
        pid=42;returncode=0
    monkeypatch.setattr(clock.subprocess,'Popen',lambda command,**kw:captured.append(command) or Process())
    monkeypatch.setattr(clock,'start_ticks',lambda _:123)
    monkeypatch.setattr(clock,'adoption',lambda *a,**kw:row)
    monkeypatch.setattr('pacbench.stop.communicate_until_stop',lambda *a,**kw:('done',''))
    witness={};clock.run_cli([str(python.with_name('hermes')),'chat','-q','question'],home=home,python=python,env=env,cwd=tmp_path,timeout=1,witness=witness)
    assert captured==[[str(python),'-m','hermes_cli.main','chat','-q','question']]
    assert witness['cli_entrypoint']['python']==str(python)

@pytest.mark.parametrize('kind',['timeout','stop'])
def test_primary_timeout_or_stop_is_not_hidden_by_identity_failure(tmp_path,monkeypatch,kind):
    import subprocess
    from pacbench.stop import OperatorStop
    home,env,row=data(tmp_path);env['PACBENCH_NOW']=datetime.now(timezone.utc).isoformat();python=tmp_path/'venv/bin/python'
    class Process:
        pid=42;returncode=0
        def kill(self):pass
        def wait(self):pass
        def communicate(self):return ('partial','stderr')
    monkeypatch.setattr(clock.subprocess,'Popen',lambda *a,**kw:Process())
    monkeypatch.setattr(clock,'start_ticks',lambda _:123)
    def invalid(*a,**kw):raise RuntimeError('identity failure')
    monkeypatch.setattr(clock,'adoption',invalid)
    error=subprocess.TimeoutExpired(['native'],1) if kind=='timeout' else OperatorStop()
    def fail(*a,**kw):raise error
    monkeypatch.setattr('pacbench.stop.communicate_until_stop',fail)
    witness={}
    with pytest.raises(type(error)):clock.run_cli([str(python.with_name('hermes')),'chat'],home=home,python=python,env=env,cwd=tmp_path,timeout=1,witness=witness)
    assert witness['cli_adoption_error']['primary_failure_type']==type(error).__name__
    assert witness['cli_adoption_error']['reason']=='identity failure'
