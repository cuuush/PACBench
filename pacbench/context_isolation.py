"""Keep benchmark files out of Hermes's automatically loaded project context."""
from __future__ import annotations

import atexit
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .followups import save

VERSION = 'empty-recall-workspace-v1'


def outside_project(path: Path) -> bool:
    path = path.resolve()
    repo = Path(__file__).resolve().parents[1]
    return not path.is_relative_to(repo) and not any(
        (p / '.git').exists() for p in (path, *path.parents))


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
                   *, prompt_hash: str | None = None) -> dict:
    """Read the actual native saved prompt, not a declaration about its inputs."""
    record = {'version': VERSION, 'state': 'fail', 'workspace': str(workspace),
              'source': 'native Hermes sessions/system_prompts tables'}
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
    if not row or not row[1]:
        return {**record, 'reason': 'Native saved system prompt empty or missing'}
    cwd, prompt = row
    record.update(native_cwd=cwd, system_prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    if prompt_hash and prompt_hash != record['system_prompt_sha256']:
        return {**record, 'reason': 'Native prompt revision hash mismatch'}
    if cwd != str(workspace) or not outside_project(workspace):
        return {**record, 'reason': 'Native recall working directory was not isolated'}
    if '# Project Context' in prompt:
        return {**record, 'reason': 'Hermes loaded project context into recall'}
    return {**record, 'state': 'pass', 'reason': 'No project context in native saved system prompt'}
