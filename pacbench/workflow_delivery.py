"""Submit public exports through the documented native call. Acceptance is not readiness.

Clients are already provisioned, account-bound upstream clients. This module does not
install software, start services, change provider configuration, or resubmit writes.
"""
from contextlib import closing
import os
import tempfile
from pathlib import Path

from .workflow_tracks import eligibility


def submit_document(provider, client, account_scope, document):
    if (not document.filename.endswith('.txt') or Path(document.filename).name != document.filename
            or '\\' in document.filename):
        raise ValueError('expected a single public export filename')
    route = eligibility(provider, 'nightly-document')
    if not route['documented_for_scope']:
        raise ValueError(route['boundary'])
    method = route['routes'][0]['method']
    if method == 'Memory.add':
        return client.add(document.text, user_id=account_scope, infer=True,
                          metadata={'filename': document.filename, 'collected_through': document.cutoff})
    if method == 'remember_permanent':
        return client.remember_permanent(text=document.text, dataset=account_scope,
                                         session_ids=[], timeout=1800)
    if method == 'add':
        return client.add(account_scope, content=document.text, id=document.filename,
                          date=document.cutoff)
    if method == 'auto_resource':
        directory = Path(account_scope) / 'resource'
        directory.mkdir(exist_ok=True)
        destination = directory / document.filename
        # Publish only complete bytes, and never replace a previous import.
        with tempfile.NamedTemporaryFile(dir=directory.parent, delete=False) as staged:
            temporary = Path(staged.name)
            try:
                staged.write(document.text.encode())
                staged.flush()
                os.link(temporary, destination)
            finally:
                temporary.unlink()
        return {'resource': str(destination)}
    with tempfile.TemporaryDirectory(prefix='pacbench-public-upload-') as staging:
        path = Path(staging) / document.filename
        path.write_bytes(document.text.encode())
        if method == 'retain_files':
            return client.retain_files(bank_id=account_scope, files=[path],
                                       files_metadata=[{'document_id': document.filename}])
        if method == 'resources':
            upload = client.upload_temp_file(path)
            return client.post('/api/v1/resources',
                               {'temp_file_id': upload, 'source_name': document.filename, 'wait': True})
    raise ValueError(f'unsupported native method {method}')


class MemCalReaders:
    """Read an already provisioned synthetic Mac Messages store / IMAP mailbox.

    The synthetic upstream controls visibility at its own clock. This route does
    not copy a fabricated Messages database or inject text into the archive.
    """
    def __init__(self, connection, config, *, site=None):
        self.connection, self.config = connection, config
        self.site = site
        self.qualified_channels = None

    def preflight(self, account, channels):
        from .workflow_memcal import inspect_memcal_readers

        self.qualified_channels = None
        if self.site is None or self.site.account != account:
            raise ValueError('A bound synthetic MemCal site matching the workflow account is required')
        report = inspect_memcal_readers(self.site, self.connection, self.config, channels=channels)
        self.qualified_channels = tuple(channels)
        return report

    def collect(self, channels):
        if tuple(channels) != self.qualified_channels:
            raise ValueError('MemCal mixed reader preflight is required before native collection')
        from .workflow_memcal import _bound_sources
        readers, _ = _bound_sources(self.site, self.connection, self.config)
        from memcal import archive, identity, sources

        conn, cfg = self.connection, self.config
        identity.import_contacts(conn)
        collection = archive.open_collection(conn, mode='cli')
        failure = None
        reports = {}
        try:
            for channel in channels:
                report = sources.catch_up(readers[channel],conn,cfg,collection_id=collection)
                reports[channel] = {'read':report.read,'archived':report.archived,
                                    'more':report.more,'error':bool(report.error)}
                if report.error or report.more:
                    raise RuntimeError('native source reader incomplete')
        except BaseException as exc:
            failure = type(exc).__name__
            raise
        finally:
            archive.close_collection(conn,collection,error=failure)
        return {'collection_id':collection,'readers':reports}

    def process(self):
        from memcal import brief, db
        from memcal.dream.run import dream

        result = dream(self.connection,self.config)
        if result.errors:
            raise RuntimeError('native nightly processing incomplete')
        # A non-error return can still leave queued work. Verify the committed
        # outcome from a second connection, including rows visible through WAL.
        with closing(db.connect(self.config.db_path, read_only=True)) as reader:
            proof = completed_dream(reader, result.run_id)
        brief.write(self.connection,self.config)
        return proof


def completed_dream(reader, run_id):
    row = reader.execute(
        "SELECT finished_at, error, mode FROM runs WHERE id = ?", (run_id,)
    ).fetchone()
    pending = reader.execute("SELECT count(*) FROM spool WHERE processed_at IS NULL").fetchone()[0]
    if row is None or not row[0] or row[1] or row[2] != 'nightly' or pending:
        raise RuntimeError('native nightly Dream did not finish cleanly with an empty spool')
    return {'run_id': run_id, 'finished_at': row[0], 'pending_spool': pending}
