"""Run the stock MemCal reader against isolated, chronological synthetic sources."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from email import message_from_string
from pathlib import Path

import pacbench.corpus.sources as frozen
from pacbench.corpus.r2 import SPEC, compile_week
from pacbench.evidence_store import file_binding as binding
from pacbench.imessage_fixture import CONDITION
from pacbench.provider_versions import verify_memcal_source
from pacbench.providers.memcal.source import memcal_archive_readback
from pacbench.workflows.delivery import prepare_sources
from pacbench.workflows.spec import nightly_documents

from ...email_fixture import MailboxServer, append_emails, mail_address
from ...imessage_fixture import APPLE_EPOCH, SCHEMA, identity
from .site import MemCalSite, inspect_memcal_site
from .source import MemCalSourceWitness, frozen_messages_digest
from .workflow import _source_messages

CHAT = Path('Library/Messages/chat.db')
CONTACTS = Path('Library/Application Support/AddressBook/Sources/synthetic/AddressBook-v22.abcddb')


def read(path):
    return json.loads(path.read_text())


def replace(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.staged')
    shutil.copyfile(source, temporary)
    temporary.replace(target)


class SyntheticSources:
    def __init__(self, root, site, mailbox, messages, documents):
        self.root, self.site, self.mailbox = root, site, mailbox
        self.chat = Path(site.os_home) / CHAT
        self.rows = {row['source_id']: row for row in messages}
        self.cutoffs = list(dict.fromkeys(d.cutoff for d in documents))
        self.snapshots = dict(zip(self.cutoffs, sorted((root/'private-fixtures/sources').iterdir()), strict=True))
        self.cutoff = None
        self.fixture = self.chat.with_suffix('.fixture.json')
        self.bindings_path = root/'source-bindings.json'
        latest = self.snapshots[self.cutoffs[-1]]
        manifest = read(latest/'chat.fixture.json')
        apple = self.messages(latest/'chat.db', latest/'chat.fixture.json')
        messages_by_guid = {r['external_id']: r for r in apple[0]}
        people = {channel: {m['sender'] for m in messages if m['channel'] == channel} for channel in ('imessage','email')}
        people['imessage'].update(name for m in messages if m['channel']=='imessage' for name in m['structured']['participants'])
        self.people = {'imessage':{name:identity(name)['handle'] for name in people['imessage']},
                       'email':{name:mail_address(name) for name in people['email']}}
        records = {}
        for sid, row in manifest['messages'].items():
            guid = row['guid']
            observed = messages_by_guid[guid]
            records[sid] = dict(external_id=guid, archive_thread=observed['thread'], archive_handle=observed['archive_handle'])
        for row in read(latest/'mailbox.json')['messages']:
            source = row['source']
            observed = self.mail(row)
            owned = source['from_owner']
            records[source['source_id']] = dict(external_id=observed['external_id'], archive_thread=observed['thread'],
                                                archive_handle=None if owned else observed['sender'])
        threads = {self.rows[sid]['thread']: row['archive_thread'] for sid,row in records.items()
                   if self.rows[sid]['channel'] == 'imessage'}
        self.bindings_path.write_text(json.dumps(dict(account=site.account, frozen_messages_sha256=frozen_messages_digest(messages),
            people=self.people, threads={'imessage':threads}, records=records), sort_keys=True))
        self.bindings_sha256 = binding(self.bindings_path)['sha256']
        self.proof = root/'synthetic-source-provenance.json'
        self.proof.write_text(json.dumps(dict(protocol='synthetic-chatdb-imap-v1', apple_delivery=False,
            account=site.account, source_map_sha256=self.bindings_sha256)))

    @staticmethod
    def messages(path, fixture):
        manifest = read(fixture)
        by_guid = {v['guid']:k for k,v in manifest['messages'].items()}
        rosters = {}
        for thread in manifest['threads'].values():
            for entry in thread['history']:
                rosters[entry['boundary_source_id']] = entry['participants']
        result = []
        current = {}
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as conn:
            for guid, stamp, owner, text, thread, handle in conn.execute(
                    'SELECT m.guid,m.date,m.is_from_me,m.text,c.chat_identifier,h.id '
                    'FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID '
                    'JOIN chat c ON j.chat_id=c.ROWID LEFT JOIN handle h ON m.handle_id=h.ROWID '
                    'ORDER BY m.ROWID'):
                if guid not in by_guid:
                    if text is not None:
                        raise ValueError('Unexpected dialogue in synthetic chat.db')
                    continue
                if by_guid[guid] in rosters:
                    current[thread] = rosters[by_guid[guid]]
                written = datetime.fromtimestamp(APPLE_EPOCH.timestamp() + stamp/1_000_000_000, timezone.utc).isoformat()
                result.append(dict(external_id=guid, sender=identity('Me')['handle'] if owner else handle,
                    archive_handle=handle, thread='id:'+thread, from_owner=bool(owner), written_at=written, arrived_at=written,
                    text=text, participants_at_message=[identity(name)['handle'] for name in current[thread]]))
            last = conn.execute('SELECT coalesce(max(ROWID),0) FROM message').fetchone()[0]
        return result, last

    @staticmethod
    def mail(row):
        from memcal.sources import proton
        source = row['source']
        own, parent, chain = proton.message_ids(message_from_string(row['rfc822']))
        return dict(external_id=own, sender=mail_address(source['sender']), thread=next(iter(chain), parent or own),
                    from_owner=source['from_owner'], written_at=source['written_at'], arrived_at=source['arrived_at'],
                    folder=row['folder'], folder_role=row['folder'], uid=row['uid'], uidvalidity=1, rfc822=row['rfc822'])

    def release(self, cutoff):
        index = 0 if self.cutoff is None else self.cutoffs.index(self.cutoff)+1
        if index >= len(self.cutoffs) or self.cutoffs[index] != cutoff:
            raise ValueError('Synthetic sources can only advance one release at a time')
        snapshot = self.snapshots[cutoff]
        replace(snapshot/'chat.db', self.chat)
        replace(snapshot/'chat.fixture.json', self.fixture)
        replace(snapshot/'chat.contacts.abcddb', Path(self.site.os_home)/CONTACTS)
        self.mailbox.server.messages = read(snapshot/'mailbox.json')['messages']
        self.cutoff = cutoff
        return self.observe(cutoff)

    def observe(self, cutoff):
        if cutoff != self.cutoff:
            raise ValueError('Synthetic site observation cannot change source visibility')
        messages, last = self.messages(self.chat, self.fixture)
        emails = [self.mail(row) for row in self.mailbox.server.messages]
        folders = {name:name for name in ('INBOX','Sent')}
        cursors = {name:dict(uidvalidity=1, highest_uid=max((r['uid'] for r in emails if r['folder']==name),default=0))
                   for name in folders}
        return dict(account=self.site.account, cutoff=cutoff, as_of=cutoff,
                    service_at=datetime.now(timezone.utc).isoformat(), channels={
                       'imessage':dict(as_of=cutoff, records=messages, cursor={'last_rowid':last}),
                       'email':dict(as_of=cutoff, records=emails, folders=folders, cursor=cursors)})

    def deployment(self):
        return dict(account=self.site.account, store_path=str(Path(self.site.memcal_home)/'memcal.db'),
            synthetic_source_provenance='PACBench synthetic chat.db, Contacts and isolated TLS IMAP',
            observed_connector_grants={'imessage':['run-owned synthetic reader database'], 'email':['read-only INBOX and Sent']},
            as_of_upstream='Only the released SQLite snapshot and IMAP prefix are mounted at the source endpoints',
            synthetic_chatdb_fixture=binding(self.proof))


@contextmanager
def fixture(campaign):
    from memcal import db

    from .. import run_environment

    home = campaign/'source-home'
    store = campaign/'memcal/memcal-home'
    if Path.home() != home:
        raise ValueError('Start the process with HOME set to the new campaign source-home')
    campaign.mkdir(mode=0o700, parents=True, exist_ok=False)
    store.mkdir(mode=0o700, parents=True)
    (home/CHAT).parent.mkdir(parents=True)
    with closing(sqlite3.connect(home/CHAT)) as empty:
        empty.executescript(SCHEMA)
    (home/CHAT).with_suffix('.fixture.json').write_text('{"messages": {}, "threads": {}}')
    with closing(db.open_db(store/'memcal.db')):
        pass
    messages = _source_messages()
    _, documents, _, _ = compile_week('continuing-account')
    prepare_sources(campaign/'private-fixtures', messages, 'America/New_York', membership_condition=CONDITION,
                    observed_email_headers_only=True)
    initial = campaign/'empty-mailbox.json'
    append_emails(initial, [], observed_headers_only=True)
    with MailboxServer(initial) as mailbox:
        site = MemCalSite(campaign.name, str(home), str(store), 'chatdb', '127.0.0.1', mailbox.port,
                         mailbox.user, synthetic_chatdb=True)
        with run_environment(dict(PROTON_BRIDGE_HOST=site.imap_host, PROTON_BRIDGE_PORT=str(site.imap_port),
                PROTON_BRIDGE_USER=site.imap_user, PROTON_BRIDGE_PASSWORD=mailbox.password,
                MEMCAL_IMESSAGE_BACKEND='chatdb', MEMCAL_IMESSAGE_FALLBACK='0')):
            yield site, SyntheticSources(campaign, site, mailbox, messages, documents)


def preflight(site, upstream):
    from functools import partial

    from memcal import archive, config, db

    from pacbench.workflows.delivery import MemCalReaders

    from .site import verify_memcal_mailbox

    access = inspect_memcal_site(site)
    cfg = config.load(home=site.memcal_home)
    _, documents, _, _ = compile_week('continuing-account')
    with closing(db.connect(cfg.db_path)) as conn:
        readers = MemCalReaders(conn, cfg, site=site)
        readers.preflight(site.account, ('email','imessage'))
        witness = MemCalSourceWitness(conn, site, _source_messages(), documents, bindings_path=upstream.bindings_path,
            bindings_sha256=upstream.bindings_sha256, release=upstream.release, observe=upstream.observe)
        witness.verify_mailbox = partial(verify_memcal_mailbox, site, conn, cfg, checked={})
        if witness.source_inventory():
            raise ValueError('Synthetic preflight requires an empty source inventory')
        receipts = []
        for at in upstream.cutoffs:
            witness.collection_clock(at)
            db.set_today(at)
            try:
                accepted = witness.connector_acceptance(readers.collect(('email','imessage')), at)
            finally:
                db.set_today(None)
            # Offline collection proves archive and cursor coverage. The live owner
            # must separately witness Dream and native readiness before recall.
            witness.last_observation = witness.active
            witness.collection_ids = witness.receipt['ids']
            witness.active = witness.receipt = None
            witness.completed.append(at)
            witness.source_inventory()
            native_lookup_ids = {row['external_id'] for channel in ('email', 'imessage')
                                 for row in archive.search_filtered(conn, '', channel=channel, limit=len(witness.rows))}
            expected = {witness.bindings['records'][source_id]['external_id'] for source_id in witness.collection_ids}
            if native_lookup_ids != expected:
                raise ValueError('Native archive lookup did not return exactly the released source prefix')
            receipts.append(dict(cutoff=at, records=len(accepted['rows']), cumulative=len(witness.collection_ids)))
    return dict(source_access=access, releases=receipts, paid_calls=0)


ROOT = Path(__file__).resolve().parents[3]


COLLECT = """
import json, sys
from pathlib import Path
from memcal import config, db
from contextlib import closing
from provider_adapters.document_connector import collect_memcal_sources, memcal_source_mailbox
cfg = config.load()
source, root = map(Path, sys.argv[1:])
with memcal_source_mailbox(source, root, cfg) as (messages, mailbox):
    cfg.ensure_dirs()
    with closing(db.open_db(cfg.db_path)) as conn:
        result = collect_memcal_sources(source, root, cfg, conn, messages, mailbox)
print(json.dumps(result, default=sorted))
"""


def transport_preflight(bundle_path, expected_hash, output, server_python):
    pin = verify_memcal_source(ROOT)
    bundle = frozen.load_bundle(bundle_path, expected_hash)
    if len(bundle['lives']) != 1:
        raise ValueError('Expected a single synthetic life')
    records = next(iter(bundle['lives'].values()))['messages']
    if {r['channel'] for r in records} != {'email', 'imessage'}:
        raise ValueError('Expected both email and iMessage')
    spec = json.loads(SPEC.read_text())
    documents = nightly_documents(records, spec['timezone'], spec['nightly_cutoff'])
    releases = {}
    for document in documents:
        releases.setdefault(document.cutoff, set()).update(document.source_ids)
    output = Path(output).resolve()
    output.mkdir(mode=0o700, parents=True)
    schedule = prepare_sources(output, records, spec['timezone'], membership_condition=CONDITION,
                               observed_email_headers_only=True)
    if len(schedule) != len(releases):
        raise ValueError('Fixture days differ from v2 nightly eligibility')
    root = output/'repeat-1/memcal'
    home = root/'fallback-home'
    home.mkdir(parents=True)
    store = root/'memcal-home'
    env = {'HOME':str(home), 'MEMCAL_HOME':str(store), 'PATH':os.defpath,
           'PYTHONPATH':os.pathsep.join([pin['source'], str(ROOT)]),
           'PACBENCH_IMAP_SERVER_PYTHON':str(Path(server_python).absolute()),
           'TZ':'America/New_York', 'LANG':'C.UTF-8'}
    checks = []
    days = []
    for batch, (at, eligible_ids) in zip(schedule, releases.items()):
        day = batch['id']
        if day != at[:10] or set(batch['source_ids']) != eligible_ids:
            raise ValueError('Fixture release differs from canonical nightly eligibility')
        started = datetime.now(timezone.utc).isoformat()
        result = subprocess.run([sys.executable, '-c', COLLECT, str(output/'sources'/day), str(root)],
                                 env={**env, 'MEMCAL_TODAY':at}, cwd=home, capture_output=True,
                                 text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f'Native collection failed on {day}: {result.stderr}')
        receipt = json.loads(result.stdout)
        source_ids = {channel:[r['source_id'] for r in records if r['source_id'] in eligible_ids
                               and r['channel']==channel] for channel in ('email','imessage')}
        expected_mail = len(source_ids['email'])
        operations = [c['operation'] for c in receipt['email_transport']['commands']]
        if ('STARTTLS' not in operations or 'SEARCH' not in operations or ('FETCH' in operations) != bool(expected_mail)
                or receipt['email_ingest']['read'] != expected_mail):
            raise ValueError('Stock IMAP search/fetch evidence differs from the eligible release')
        days.append(day)
        readback = memcal_archive_readback(output, days, store)
        with closing(sqlite3.connect((store/'memcal.db').as_uri()+'?mode=ro', uri=True)) as conn:
            total = conn.execute('SELECT count(*) FROM archive').fetchone()[0]
        if total != sum(len(b['source_ids']) for b in schedule[:len(days)]):
            raise ValueError('Historical archive has missing or extra records')
        item = {'day':day, 'collection_at':at, 'cumulative_archive_count':total,
                'physical_started_utc':started, 'physical_finished_utc':datetime.now(timezone.utc).isoformat(),
                'released_source_ids':source_ids, 'receipt':receipt, 'readback':readback}
        (output/(day+'.json')).write_text(json.dumps(item, indent=2)+'\n')
        checks.append({'day':day, 'scenario_cutoff':at, 'cumulative_archive_count':total,
                       'iMessages':len(source_ids['imessage']),
                       'email':receipt.get('email_ingest',{}).get('read',0)})
    report = {'state':'collection-transport-pass', 'memcal_revision':pin['revision'],
              'pacbench_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'bundle_hash':expected_hash, 'batches':checks, 'model_calls':0, 'questions_asked':0,
              'email_transport':'Stock reader against local private pymap Maildir via STARTTLS',
              'v2_nightly_document_count':len(documents),
              'reply_and_address_headers':'source-supplied only; synthetic sender addresses and stable Message-IDs for transport',
              'imessage_transport':'Stock chat.db and Contacts readers against Linux schema fixtures',
              'apple_created_source_verified':False, 'dream_executed':False}
    (output/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description='Check synthetic MemCal source collection.')
    parser.add_argument('output', type=Path, help='new fixture directory')
    parser.add_argument('--bundle', type=Path, help='frozen source bundle to replay through the native readers')
    parser.add_argument('--bundle-hash', help='expected SHA-256 of the source bundle')
    parser.add_argument('--server-python', type=Path, help='Maildir server interpreter for a frozen bundle')
    args = parser.parse_args(argv)
    supplied = (args.bundle, args.bundle_hash, args.server_python)
    if any(supplied) and not all(supplied):
        parser.error('a frozen transport check requires --bundle, --bundle-hash and --server-python together')
    output = args.output.resolve()
    if args.bundle:
        result = transport_preflight(args.bundle, args.bundle_hash, output, args.server_python)
    else:
        with fixture(output) as (site, upstream):
            result = preflight(site, upstream)
            (output / 'preflight.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
