"""Keep benchmark files out of Hermes's automatically loaded project context."""
from __future__ import annotations

import atexit
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path

from pacbench.evidence_store import save

VERSION = 'empty-recall-workspace-v1'
SNAPSHOT_VERSION = 'empty-recall-workspace-v2'
SNAPSHOT_SOURCE = 'native Hermes saved system prompt, privately archived at turn completion'

def _snapshot_path(home: Path, session_id: str, prompt_hash: str) -> Path:
    if not re.fullmatch(r'[0-9a-f]{64}', prompt_hash):
        raise ValueError('Invalid prompt hash')
    session_key = hashlib.sha256(session_id.encode()).hexdigest()
    return home / 'private-system-prompts' / session_key / (prompt_hash + '.json')

def _snapshot(home: Path, session_id: str, workspace: Path, prompt_hash: str, prompt: str | None = None) -> str:
    path = _snapshot_path(home, session_id, prompt_hash)
    expected = {'version': SNAPSHOT_VERSION, 'source': SNAPSHOT_SOURCE,
                'session_id': session_id, 'workspace': str(workspace), 'sha256': prompt_hash}
    if prompt is not None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {**expected, 'prompt': prompt}
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(data, out, ensure_ascii=False)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, encoding='utf-8') as source:
        stored = json.load(source)
    if not isinstance(stored, dict) or any(stored.get(k) != v for k, v in expected.items()):
        raise ValueError('Invalid native prompt snapshot binding')
    result = stored.get('prompt')
    if not isinstance(result, str) or hashlib.sha256(result.encode()).hexdigest() != prompt_hash:
        raise ValueError('Invalid native prompt snapshot digest')
    if prompt is not None and result != prompt:
        raise ValueError('Native prompt snapshot differs from database')
    return result


def outside_project(path: Path) -> bool:
    path = path.resolve()
    repo = Path(__file__).resolve().parents[1]
    markers = (p / '.git' for p in (path, *path.parents))
    # The managed sandbox mounts empty, read-only .git placeholders under
    # writable temporary roots. A real worktree marker or metadata still blocks.
    return not path.is_relative_to(repo) and not any(
        marker.is_symlink() or marker.is_file() or (marker.is_dir() and any(marker.iterdir()))
        for marker in markers)


def validate_recall_environment():
    """Reject an unusable temporary root before paid source processing."""
    if not outside_project(Path(tempfile.gettempdir())):
        raise RuntimeError('Temporary recall root must be outside every Git project; set TMPDIR before native ingestion')


def workspace_for(home: Path) -> Path:
    """Normal CLI working directory, outside Git; keep native memory hooks enabled.

    Hermes's --ignore-rules also disables its external memory provider, so it
    cannot be used to suppress benchmark instructions during this experiment.
    """
    receipt = home.parent / 'recall-workspace.json'
    if receipt.exists():
        data = json.loads(receipt.read_text())
        workspace = Path(data['workspace'])
        if data['version'] != VERSION or not outside_project(workspace) or not workspace.is_dir():
            raise RuntimeError('Invalid or unavailable isolated recall workspace: '+json.dumps({
                'workspace':str(workspace),'version':data.get('version'),
                'directory_exists':workspace.is_dir(),
                'git_ancestors':[str(p/'.git') for p in (workspace,*workspace.parents) if (p/'.git').exists()]}))
        return workspace
    workspace = Path(tempfile.mkdtemp(prefix='pacbench-chat-'))
    if not outside_project(workspace):
        shutil.rmtree(workspace)
        raise RuntimeError('Temporary recall workspace must be outside every Git project')
    save(receipt, {'version': VERSION, 'workspace': str(workspace),
                   'memory_hooks': 'native; not disabled', 'initial_files': []})
    def cleanup():
        from .process_cleanup import cleanup_run_processes
        records = cleanup_run_processes(workspace)
        save(home.parent / 'recall-workspace-cleanup.json', records)
        shutil.rmtree(workspace, ignore_errors=True)
    atexit.register(cleanup)
    return workspace


def actual_context(home: Path, session_id: str | None, workspace: Path,
                   *, prompt_hash: str | None = None, capture: bool = False,
                   receipt_version: str = VERSION) -> dict:
    """Read the actual native saved prompt, not a declaration about its inputs."""
    version = SNAPSHOT_VERSION if capture else receipt_version
    if version not in (VERSION, SNAPSHOT_VERSION):
        raise ValueError('Unsupported native context receipt version')
    record = {'version': version, 'state': 'fail', 'workspace': str(workspace),
              'source': SNAPSHOT_SOURCE if version == SNAPSHOT_VERSION else 'native Hermes sessions/system_prompts tables'}
    if not session_id:
        return {**record, 'reason': 'Missing native session ID'}
    try:
        with sqlite3.connect(f'file:{home / "state.db"}?mode=ro', uri=True) as db:
            row = db.execute('SELECT s.cwd, COALESCE(p.prompt,s.system_prompt) '
                'FROM sessions s LEFT JOIN system_prompts p ON p.hash=s.system_prompt_hash '
                'WHERE s.id=?', (session_id,)).fetchone()
            # A resumed session can acquire a new native prompt. Audit each
            # turn against the actual deduplicated revision captured at that
            # turn, rather than requiring all turns to equal the latest one.
            if row and prompt_hash:
                historical = db.execute('SELECT prompt FROM system_prompts WHERE hash=?',
                                        (prompt_hash,)).fetchone()
                row = (row[0], historical[0] if historical else None)
    except sqlite3.Error:
        return {**record, 'reason': 'Native saved system prompt unavailable'}
    if not row:
        return {**record, 'reason': 'Native saved system prompt empty or missing'}
    cwd, prompt = row
    if cwd != str(workspace) or not outside_project(workspace):
        return {**record, 'native_cwd': cwd, 'reason': 'Native recall working directory was not isolated'}
    if version == SNAPSHOT_VERSION:
        try:
            if capture:
                if not prompt:
                    return {**record, 'reason': 'Native saved system prompt empty or missing'}
                digest = hashlib.sha256(prompt.encode()).hexdigest()
                _snapshot(home, session_id, workspace, digest, prompt)
            else:
                if not prompt_hash:
                    raise ValueError('Missing saved prompt hash')
                archived = _snapshot(home, session_id, workspace, prompt_hash)
                if prompt is not None and prompt != archived:
                    raise ValueError('Native prompt snapshot differs from database')
                prompt = archived
        except (OSError, ValueError, TypeError, UnicodeError):
            return {**record, 'reason': 'Native prompt snapshot unavailable or invalid'}
    if not prompt:
        return {**record, 'reason': 'Native saved system prompt empty or missing'}
    record.update(native_cwd=cwd, system_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    if prompt_hash and prompt_hash != record['system_prompt_sha256']:
        return {**record, 'reason': 'Native prompt revision hash mismatch'}
    if cwd != str(workspace) or not outside_project(workspace):
        return {**record, 'reason': 'Native recall working directory was not isolated'}
    if '# Project Context' in prompt:
        return {**record, 'reason': 'Hermes loaded project context into recall'}
    return {**record, 'state': 'pass', 'reason': 'No project context in native saved system prompt'}
_WIRE_UNAVAILABLE = object()


def memory_delivery(home,session_id,prompt,prefetch, *, native_row_id=None, delivered_text=_WIRE_UNAVAILABLE):
    """Read native API-bound input evidence without invoking a provider.

    Hermes keeps display content separate from its API-bound memory injection.
    Missing sidecar evidence stays unknown; this does not infer model attention.
    """
    record={'state':'unknown','scope':'Native saved API-bound input, not proof of model attention'}
    if not isinstance(prefetch,str) or not session_id:
        return {**record,'reason':'Missing captured prefetch or native session'}
    try:
        with sqlite3.connect(f'file:{Path(home)/"state.db"}?mode=ro',uri=True) as db:
            query = "SELECT id,api_content FROM messages WHERE session_id=? AND role='user' AND content=?"
            params = (session_id, prompt)
            if native_row_id is not None:
                query += ' AND id=?'
                params += (native_row_id,)
            rows = db.execute(query, params).fetchall()
    except sqlite3.Error:
        return {**record,'reason':'Native API-bound sidecar unavailable'}
    if len(rows)!=1:return {**record,'reason':'Missing or ambiguous exact native user row'}
    row_id,api_content=rows[0]
    record.update(native_user_row_id=row_id,prefetch_chars=len(prefetch),
        prefetch_sha256=hashlib.sha256(prefetch.encode()).hexdigest(),
        api_content_sha256=hashlib.sha256(api_content.encode()).hexdigest() if isinstance(api_content,str) else None)
    if delivered_text is not _WIRE_UNAVAILABLE:
        if not isinstance(delivered_text, str) or delivered_text != (api_content or prompt):
            return {**record,'reason':'Wire input differs from native message'}
        api_content = delivered_text
    if not isinstance(api_content, str) or not api_content.startswith(prompt):
        return {**record,'reason':'Missing or mismatched API-bound input'}
    # A different host injection can coexist with an empty provider hook.
    # Match only the API-bound suffix added to this exact authored question.
    suffix = (api_content or '')[len(prompt):] if isinstance(api_content,str) and api_content.startswith(prompt) else ''
    _, opening, block = suffix.partition('<memory-context>')
    inside, closing, _ = block.partition('</memory-context>')
    if not prefetch:
        if opening:
            return {**record,'state':'inconsistent','reason':'Empty native hook but the API-bound input contains a memory block'}
        return {**record,'state':'empty','reason':'Captured native hook is empty and no memory block was observed'}
    present = bool(suffix.startswith('\n\n') and opening and closing and prefetch in inside)
    return {**record,'state':'confirmed' if present else 'inconsistent',
            'reason':'Exact captured provider context is in the native API-bound input' if present else
                     'Captured provider context is absent or differs in the saved API-bound input'}


LEGACY_CLOCK_PREFIX='user-clock-prefix-v1'


SYSTEM_CLOCK_PROTOCOL='ephemeral-system-clock-v2'


NATIVE_QUESTION='native-question-v2'


CLOCK_CONTEXT_PROTOCOLS=(LEGACY_CLOCK_PREFIX,SYSTEM_CLOCK_PROTOCOL,NATIVE_QUESTION)


# Exact English session-title request from the pinned native Hermes checkout.
# This auxiliary call has no answer role; it remains in costs and input traces.
NATIVE_TITLE_PROMPT_SHA256='3f1eef0616ddd2c13d45a3f8cef4ed648dadfdacdba7fbedceebf5a04d549998'


NATIVE_TITLE_SOURCE_SHA256='8d5e38f31819f89677d1893e4332e049889a662fbaf09bcff77b8a3f004f70ab'


# Both reviewed pinned revisions render the identical default title prompt.
# Historical records keep their original hashes and auxiliary classification.
NATIVE_TITLE_SOURCE_HASHES={NATIVE_TITLE_SOURCE_SHA256,
    'e5beaa6185f91e887d27a35a5acdde613c0144c156e63ed053566127ff25ce0f'}


# Stock Hermes adds this exact continuation after a partial network stream.
# It remains an actor call, with the original question/context still verified.
NATIVE_NETWORK_CONTINUATION_SHA256='e4403cfa28638e7bd7b96162d6ee75a886bbf99089c9e0f086863d30443c3495'


NATIVE_CONTINUATION_SOURCE_SHA256='2230c0d6f50d85ac27739f7205c11a4c5538bc7796b89c7de960c494328560da'


def instruction(at):
    return f'[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]'


def expected_prompt(at,question,protocol=LEGACY_CLOCK_PREFIX):
    if protocol==LEGACY_CLOCK_PREFIX:return instruction(at)+'\n\n'+question
    if protocol in (SYSTEM_CLOCK_PROTOCOL,NATIVE_QUESTION):return question
    raise ValueError('Unknown clock context protocol')


def judge_question(turn):
    """Keep the judge's explicit date context without polluting native queries."""
    if (getattr(turn,'clock_context',None) or {}).get('protocol') in (SYSTEM_CLOCK_PROTOCOL,NATIVE_QUESTION):
        return instruction(turn.at)+'\n\n'+turn.prompt
    return turn.prompt


def delivery(root, turn, *, before=(), identifiers=None, hook_before=0, hook_index=None, protocol=SYSTEM_CLOCK_PROTOCOL):
    """Verify independently observed HTTP input and native provider query."""
    from pacbench.context_isolation import PROXY_PROTOCOL
    from pacbench.context_isolation import input_rows as rows
    record={'protocol':protocol,'at':turn.at,'state':'fail',
            'query_sha256':hashlib.sha256(turn.prompt.encode()).hexdigest()}
    try:
        if protocol not in (SYSTEM_CLOCK_PROTOCOL,NATIVE_QUESTION):raise ValueError('Unsupported actual-input protocol')
        candidates=rows(root/'api-inputs.jsonl')
        if identifiers is not None:
            candidates=[r for r in candidates if r.get('request_id') in identifiers]
        else:
            candidates=[r for r in candidates if r.get('request_id') not in set(before)]
        if not candidates or identifiers is not None and len(candidates)!=len(identifiers):
            raise ValueError('Missing/ambiguous actual HTTP input witness')
        actor_ids=[];auxiliary_ids=[];continuation_ids=[]
        for row in candidates:
            if row.get('protocol') not in ('direct-hermes-api-input-v1','direct-hermes-api-input-v2','workflow-driver-input-v1',PROXY_PROTOCOL):
                raise ValueError('Unknown HTTP input witness protocol')
            systems=[m.get('content') for m in row['messages'] if m['role']=='system']
            users=[m.get('content') for m in row['messages'] if m['role']=='user']
            if protocol==NATIVE_QUESTION and users and isinstance(users[-1],str) and hashlib.sha256(users[-1].encode()).hexdigest()==NATIVE_NETWORK_CONTINUATION_SHA256:
                from pathlib import Path
                source=Path(__file__).resolve().parents[1]/'vendor/hermes-isolated/agent/conversation_loop.py'
                if hashlib.sha256(source.read_bytes()).hexdigest()!=NATIVE_CONTINUATION_SOURCE_SHA256:
                    raise ValueError('Native network-continuation source differs from pinned classifier')
                users=users[:-1]
                continuation_ids.append(row['request_id'])
            if (len(systems)==1 and isinstance(systems[0],str) and
                    hashlib.sha256(systems[0].encode()).hexdigest()==NATIVE_TITLE_PROMPT_SHA256):
                from pathlib import Path
                source=Path(__file__).resolve().parents[1]/'vendor/hermes-isolated/agent/title_generator.py'
                if hashlib.sha256(source.read_bytes()).hexdigest() not in NATIVE_TITLE_SOURCE_HASHES:
                    raise ValueError('Native auxiliary-title source differs from pinned classifier')
                auxiliary_ids.append(row['request_id'])
                continue
            if (len(systems)!=1 or not isinstance(systems[0],str) or
                    not users or not isinstance(users[-1],str) or not
                    (users[-1]==turn.prompt or users[-1].startswith(turn.prompt+'\n\n')) or
                    turn.prefetch_context and turn.prefetch_context not in users[-1]):
                raise ValueError('Scenario clock or natural question differs at HTTP boundary')
            if protocol==SYSTEM_CLOCK_PROTOCOL:
                if not systems[0].endswith(instruction(turn.at)):
                    raise ValueError('Scenario clock missing at historical HTTP boundary')
            else:
                native=(getattr(turn,'context_isolation',None) or {})
                if (native.get('state')!='pass' or
                        hashlib.sha256(systems[0].encode()).hexdigest()!=native.get('system_prompt_sha256') or
                        '[Simulated now:' in systems[0]):
                    raise ValueError('Actual system input differs from the native saved prompt or contains benchmark clock instructions')
                if (getattr(turn,'provider_clock',None) or {}).get('version')=='honcho-wall-clock-v5':
                    gateway=getattr(turn,'hermes_gateway',None) or {}
                    if (row.get('protocol') not in ('direct-hermes-api-input-v2','workflow-driver-input-v1') or row.get('pid')!=gateway.get('pid') or
                        row.get('process_start_ticks')!=gateway.get('start_time') or
                        row.get('clock_role')!='hermes-gateway' or row.get('turn_nonce')!=gateway.get('turn_nonce')):
                        raise ValueError('Actual actor request lacks the verified gateway clock identity')
            actor_ids.append(row['request_id'])
        if not actor_ids:
            raise ValueError('No actual answer request; auxiliary calls cannot prove clock delivery')
        hooks=[json.loads(line) for line in (root/'context.jsonl').read_text().splitlines()]
        matches=[(index,h) for index,h in enumerate(hooks) if index>=hook_before and
                 (hook_index is None or index==hook_index) and h.get('session_id')==turn.session_id and
                 h.get('query')==turn.prompt and h.get('prefetch')==turn.prefetch_context]
        if len(matches)!=1:
            raise ValueError('Native automatic-retrieval query differs from natural question')
        index,hook=matches[0]
        record.update(state='pass',native_query_index=index,
            actor_api_input_ids=actor_ids,auxiliary_title_api_input_ids=auxiliary_ids,
            native_query_sha256=hashlib.sha256(json.dumps(hook,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            api_input_ids=[r['request_id'] for r in candidates],
            api_input_sha256=[hashlib.sha256(json.dumps(r,sort_keys=True,ensure_ascii=False).encode()).hexdigest() for r in candidates],
            reason=('Actual HTTP system input carries the clock; native retrieval receives the unchanged natural question' if protocol==SYSTEM_CLOCK_PROTOCOL else
                    'Actual HTTP system input equals the native saved prompt; native retrieval receives the verbatim question with no benchmark clock instructions'))
        if continuation_ids:record['native_network_continuation_api_input_ids']=continuation_ids
    except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
        record['reason']=str(exc)
    return record


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


def record_input(project, receipt):
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
            from pacbench.provider_clock import CHECKPOINT_CLOCK_VERSION as VERSION
            from pacbench.provider_clock import descriptor
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


def input_rows(path):
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
