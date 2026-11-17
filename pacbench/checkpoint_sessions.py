"""Cold native Hermes history transfer at a source checkpoint.

Copy committed SQLite state with SQLite's backup API; never synthesize message
rows or convert a deliberate conversation into independent chats. Provider memory
stores and pending writes are not transferred with conversation history.
"""
from pathlib import Path
import hashlib,json,sqlite3,os
from .hermes_gateway import GatewayLifecycleError,_live
from .gateway_reuse import atomic
from .checkpoint_clock import history

PROTOCOL='native-cold-session-checkpoint-v1'

def seal(owner,session_ids,path):
    if owner.failed:raise GatewayLifecycleError('Failed native owner cannot seal a checkpoint')
    owner.fence()
    rows={}
    for sid in session_ids:
        context=owner.session_context.get(sid)
        if not context or context.get('authored_pending'):raise GatewayLifecycleError('Uncommitted native session cannot cross checkpoint')
        h=history(owner.home,sid)
        if h['sha256']!=context['history_sha256']:raise GatewayLifecycleError('Native history changed before checkpoint seal')
        rows[sid]={'history':h,'session_started_at':context.get('session_started_at',context['initial_at'])}
    result={'protocol':PROTOCOL,'source_home':str(owner.home),'source_owner':owner.witness,
        'config_sha256':owner.config_sha256,'pins':owner.pins,'sessions':rows,
        'checkpoint':owner.checkpoint,'pending_native_writes':'drained'}
    atomic(Path(path),result)
    return {'path':str(path),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


def transfer(binding,destination):
    p=Path(binding['path'])
    if hashlib.sha256(p.read_bytes()).hexdigest()!=binding['sha256']:raise GatewayLifecycleError('Native session seal changed')
    r=json.loads(p.read_text());destination=Path(destination).resolve()
    if r.get('protocol')!=PROTOCOL or r.get('pending_native_writes')!='drained' or _live(r['source_owner']['pid']):
        raise GatewayLifecycleError('Native source owner must be stopped after verified drain')
    if hashlib.sha256((destination/'config.yaml').read_bytes()).hexdigest()!=r['config_sha256']:
        raise GatewayLifecycleError('Native history cannot change model, stock instructions or configuration')
    target=destination/'state.db';source=Path(r['source_home'])/'state.db'
    if target.exists() or target.is_symlink() or not source.is_file() or source.is_symlink():raise GatewayLifecycleError('Native history target must be a fresh owned home')
    for sid,row in r['sessions'].items():
        if history(source.parent,sid)!=row['history']:raise GatewayLifecycleError('Sealed native history changed after shutdown')
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
    with sqlite3.connect(f'file:{source}?mode=ro',uri=True) as old,sqlite3.connect(target) as new:
        old.backup(new)
        if new.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise GatewayLifecycleError('Native history backup failed integrity check')
    for sid,row in r['sessions'].items():
        if history(destination,sid)!=row['history'] or history(source.parent,sid)!=row['history']:
            raise GatewayLifecycleError('Native history backup differs')
    result={'protocol':PROTOCOL,'source_seal':binding,'destination_home':str(destination),
        'sessions':r['sessions'],'config_sha256':r['config_sha256'],'pins':r['pins'],
        'method':'SQLite readonly backup; unchanged rows; no provider store or pending work copied'}
    path=destination.parent/'session-checkpoint-transfer.json';atomic(path,result)
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def verify(binding,owner,sid):
    p=Path(binding['path'])
    if hashlib.sha256(p.read_bytes()).hexdigest()!=binding['sha256']:raise GatewayLifecycleError('Native transfer receipt changed')
    r=json.loads(p.read_text())
    if (r.get('protocol')!=PROTOCOL or r.get('destination_home')!=str(owner.home) or sid not in r['sessions'] or
        r.get('config_sha256')!=owner.config_sha256 or r.get('pins')!=owner.pins):
        raise GatewayLifecycleError('Native transfer does not bind this exact owner/session')
    seal_path=Path(r['source_seal']['path'])
    if hashlib.sha256(seal_path.read_bytes()).hexdigest()!=r['source_seal']['sha256']:raise GatewayLifecycleError('Native source seal changed')
    sealed=json.loads(seal_path.read_text())
    if (r['sessions']!=sealed['sessions'] or r['pins']!=sealed['pins'] or r['config_sha256']!=sealed['config_sha256']):
        raise GatewayLifecycleError('Native transfer differs from its sealed session history')
    if _live(sealed['source_owner']['pid']):raise GatewayLifecycleError('Previous native owner is live')
    row=r['sessions'][sid]
    if history(owner.home,sid)!=row['history']:raise GatewayLifecycleError('Restored native history changed')
    return row
