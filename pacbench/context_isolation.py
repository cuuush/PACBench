"""Keep benchmark files out of Hermes's automatically loaded project context."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .followups import save

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
    return not path.is_relative_to(repo) and not any(
        (p / '.git').exists() for p in (path, *path.parents))


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
def memory_delivery(home,session_id,prompt,prefetch):
    """Read native API-bound input evidence without invoking a provider.

    Hermes keeps display content separate from its API-bound memory injection.
    Missing sidecar evidence stays unknown; this does not infer model attention.
    """
    record={'state':'unknown','scope':'Native saved API-bound input, not proof of model attention'}
    if not isinstance(prefetch,str) or not session_id:
        return {**record,'reason':'Missing captured prefetch or native session'}
    try:
        with sqlite3.connect(f'file:{Path(home)/"state.db"}?mode=ro',uri=True) as db:
            rows=db.execute("SELECT id,api_content FROM messages WHERE session_id=? AND role='user' AND content=?",
                            (session_id,prompt)).fetchall()
    except sqlite3.Error:
        return {**record,'reason':'Native API-bound sidecar unavailable'}
    if len(rows)!=1:return {**record,'reason':'Missing or ambiguous exact native user row'}
    row_id,api_content=rows[0]
    record.update(native_user_row_id=row_id,prefetch_chars=len(prefetch),
        prefetch_sha256=hashlib.sha256(prefetch.encode()).hexdigest(),
        api_content_sha256=hashlib.sha256(api_content.encode()).hexdigest() if isinstance(api_content,str) else None)
    if not prefetch:
        return {**record,'state':'empty','reason':'Captured native hook returned no automatic memory context'}
    present=(isinstance(api_content,str) and api_content.startswith(prompt+'\n\n') and
             '<memory-context>' in api_content and prefetch in api_content)
    return {**record,'state':'confirmed' if present else 'inconsistent',
            'reason':'Exact captured provider context is in the native API-bound input' if present else
                     'Captured provider context is absent or differs in the saved API-bound input'}
