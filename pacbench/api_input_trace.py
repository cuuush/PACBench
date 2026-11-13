"""Observe Hermes system/user inputs at the direct HTTP boundary, without keys."""
import fcntl
import hashlib
import json
import os
from pathlib import Path


def record(project, receipt):
    configured=os.environ.get('PACBENCH_API_INPUT_TRACE')
    if not configured or not project.endswith('-driver'):
        return
    path=Path(configured)
    expected=Path(os.environ['HERMES_HOME']).resolve().parent/'api-inputs.jsonl'
    if path.resolve()!=expected or path.is_symlink():
        raise ValueError('API input observer is outside the run-owned Hermes home')
    body=receipt['request']
    messages=[{'role':m['role'],'content':m.get('content')} for m in body.get('messages',[])
              if m.get('role') in {'system','user'}]
    row={'protocol':'direct-hermes-api-input-v1','request_id':receipt['id'],
         'project':project,'model':body['model'],'messages':messages,
         'canonical_request_sha256':hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
    if os.environ.get('PACBENCH_PYTHON_CLOCK_NONCE'):
        row.update(protocol='direct-hermes-api-input-v2',pid=os.getpid(),
            process_start_ticks=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]),
            clock_role=os.environ.get('PACBENCH_PYTHON_CLOCK_ROLE'),
            turn_nonce=os.environ['PACBENCH_PYTHON_CLOCK_NONCE'])
    encoded=json.dumps(row,ensure_ascii=False)+'\n'
    with path.open('a') as stream:
        path.chmod(0o600)
        fcntl.flock(stream,fcntl.LOCK_EX)
        stream.write(encoded)
        stream.flush()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def record_memory(project,receipt):
    configured=os.environ.get('PACBENCH_MEMORY_API_INPUT_TRACE_ROOT')
    if not configured or not project.endswith('-memory'):return
    folder=Path(configured).resolve();owner=Path(os.environ['PACBENCH_IMPORT_ROOT']).resolve()
    if not folder.is_relative_to(owner) or Path(configured).is_symlink():
        raise ValueError('Native request trace must remain in its run-owned root')
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    row={'version':'native-memory-api-input-v1','request_id':receipt['id'],
         'project':project,'request':receipt['request'],
         'scope':'Private actual routed native payload; no authorization headers or reasoning output.'}
    with (folder/(receipt['id']+'.json')).open('x') as stream:
        os.chmod(stream.name,0o600);json.dump(row,stream,ensure_ascii=False)
