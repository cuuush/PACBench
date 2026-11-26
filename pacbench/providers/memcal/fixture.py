"""Run the stock MemCal reader against isolated, chronological synthetic sources."""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from email import message_from_string
import json
import os
from pathlib import Path
import shutil
import sqlite3

from ...canary_capability import binding
from ...document_ingestion import prepare_sources
from ...email_fixture import MailboxServer, append_emails, mail_address
from ...imessage_fixture import APPLE_EPOCH, SCHEMA, identity
from ...imessage_membership import CONDITION
from .site import MemCalSite, inspect_memcal_site
from .workflow import _source_messages
from .source import MemCalSourceWitness, frozen_messages_digest
from pacbench.campaigns.pizza_week import compile_week
from pacbench.workflows.spec import instant


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--preflight', action='store_true')
    actions.add_argument('--canary', action='store_true')
    actions.add_argument('--after-canary', type=Path)
    parser.add_argument('--billing-confirmation', type=Path)
    args = parser.parse_args()
    campaign = args.campaign.resolve()
    with fixture(campaign) as (site, upstream):
        if args.preflight:
            result = preflight(site, upstream)
            (campaign/'preflight.json').write_text(json.dumps(result, indent=2))
        else:
            if args.billing_confirmation is None:
                parser.error('--billing-confirmation is required for live execution')
            from functools import partial
            from .workflow import open_account
            from .workflow import run_live
            result = run_live(campaign, site, partial(open_account, upstream=upstream),
                              args.billing_confirmation.resolve(), canary=args.canary, after_canary=args.after_canary)
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
