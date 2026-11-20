from contextlib import closing
import sqlite3

import pytest

from pacbench.workflow_delivery import completed_dream


@pytest.mark.parametrize('state', ['completed', 'dry-run', 'failed', 'unfinished', 'missing', 'pending'])
def test_only_finished_nightly_dream_with_empty_spool_qualifies(tmp_path, state):
    path = tmp_path / 'memcal.db'
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode = WAL')
        writer.executescript('CREATE TABLE runs(id INTEGER PRIMARY KEY, finished_at, error, mode); '
                             'CREATE TABLE spool(processed_at);')
        if state != 'missing':
            writer.execute('INSERT INTO runs VALUES (7, ?, ?, ?)', (
                None if state == 'unfinished' else '2026-10-05T21:01:00-04:00',
                'failed' if state == 'failed' else None,
                'dry-run' if state == 'dry-run' else 'nightly',
            ))
        writer.execute('INSERT INTO spool VALUES (?)', (None if state == 'pending' else 'processed',))
        writer.commit()
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as reader:
            if state == 'completed':
                assert completed_dream(reader, 7) == {
                    'run_id': 7, 'finished_at': '2026-10-05T21:01:00-04:00', 'pending_spool': 0,
                }
            else:
                with pytest.raises(RuntimeError, match='nightly Dream'):
                    completed_dream(reader, 7)
