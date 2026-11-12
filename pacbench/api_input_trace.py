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
    encoded=json.dumps(row,ensure_ascii=False)+'\n'
    with path.open('a') as stream:
        path.chmod(0o600)
        fcntl.flock(stream,fcntl.LOCK_EX)
        stream.write(encoded)
        stream.flush()


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
