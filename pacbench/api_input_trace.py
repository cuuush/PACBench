"""Observe Hermes system/user inputs at the HTTP boundary without headers."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat


TRACE_NAME = 'api-inputs.jsonl'
PROXY_PROTOCOL = 'proxy-hermes-api-input-v1'


def _input_row(project, receipt, protocol):
    body = receipt['request']
    return {'protocol': protocol, 'request_id': receipt['id'], 'project': project, 'model': body['model'],
            'messages': [{'role': m['role'], 'content': m.get('content')}
                         for m in body.get('messages', []) if m.get('role') in {'system', 'user'}],
            'canonical_request_sha256': hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}


def _append(path, row, *, dir_fd=None):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                 0o600, dir_fd=dir_fd)
    with os.fdopen(fd, 'a', encoding='utf-8') as stream:
        identity = os.fstat(fd)
        if not stat.S_ISREG(identity.st_mode) or identity.st_nlink != 1:
            raise ValueError('API input observer requires a private regular file')
        os.fchmod(fd, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def record_proxy(project, receipt, arm_fd):
    _append(TRACE_NAME, _input_row(project, receipt, PROXY_PROTOCOL), dir_fd=arm_fd)


def record(project, receipt):
    configured=os.environ.get('PACBENCH_API_INPUT_TRACE')
    if not configured or not project.endswith('-driver'):
        return
    path=Path(configured)
    expected=Path(os.environ['HERMES_HOME']).resolve().parent/TRACE_NAME
    if path.resolve()!=expected or path.is_symlink():
        raise ValueError('API input observer is outside the run-owned Hermes home')
    body=receipt['request']
    row = _input_row(project, receipt, 'direct-hermes-api-input-v1')
    if os.environ.get('PACBENCH_PYTHON_CLOCK_NONCE'):
        row.update(protocol='direct-hermes-api-input-v2',pid=os.getpid(),
            process_start_ticks=int(Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()[19]),
            clock_role=os.environ.get('PACBENCH_PYTHON_CLOCK_ROLE'),
            turn_nonce=os.environ['PACBENCH_PYTHON_CLOCK_NONCE'])
        if os.environ.get('PACBENCH_REUSE_CLOCK_STATE'):
            from .checkpoint_clock import descriptor,VERSION
            d=descriptor()
            row.update(protocol='direct-hermes-api-input-v3',clock_condition=VERSION,
                process_clock_nonce=os.environ['PACBENCH_PYTHON_CLOCK_NONCE'],
                turn_nonce=d['nonce'],question_sha256=d['question_sha256'])
    if os.environ.get('PACBENCH_WORKFLOW_CAPTURE') == 'pizza-week-conversation-v2':
        # The complete private model payload is the witness. HTTP headers and
        # credentials are not part of it. Preserve tools, tool IDs and grouping.
        row.update(protocol='workflow-driver-input-v1', request=body,
                   messages=body['messages'],
                   tools=[t['function']['name'] for t in body.get('tools', [])])
    _append(path, row)


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
