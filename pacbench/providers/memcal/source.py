"""Join site-observed synthetic source releases to MemCal's actual native archive.

The site's release(cutoff) advances its own isolated upstream and reads it back.
It takes only the requested clock. Messages, expected inventories, and rubric
data are never passed to it. Its observed cumulative rows use platform identities.
The separate hash-pinned bindings map frozen source IDs, thread names and people
to those identities. All inspection takes place outside the answering agent.

The private mapping JSON has account, frozen_messages_sha256 (computed by
frozen_messages_digest), people[channel][source_name], threads[channel][source_thread],
and records[source_id] containing external_id, archive_thread and archive_handle.
Use the native archive thread selected by real headers, including a standalone
Message-ID for headerless mail. The map digest comes from the independent
platform provisioning record.

release and observe return account, cutoff, as_of, service_at and channels.
observe only inspects the existing deployment; it never switches the source
clock. For observe(None) before release, both record lists must be empty.
Each channel supplies its own as_of, cumulative records and native cursor.
Every observed item supplies external_id, sender and thread (upstream IDs),
from_owner, written_at and arrived_at. Apple items additionally carry text
and the platform-observed participants_at_message. Email carries the actual
RFC 5322 wire string, folder, logical folder_role, uid and uidvalidity. The
email channel includes a folders mapping from Sent/INBOX to the selected
names and a cursor keyed by those names with highest_uid and uidvalidity.
Direct Messages uses last_rowid (including native control records).
BlueBubbles uses after_ms and boundary_ids from that source. The test doubles
exercise the verifier; real deployment callbacks must obtain these observations
from the authorized platform and read-only mailbox.

Per-turn source inventory still requires a fresh physical upstream observation
of every visible record (O(N) source rows and their text), a complete UID SEARCH
in each IMAP folder, and native cursor reads. Stock read cursors alone do not
prove that earlier Apple text, membership, or visibility stayed fixed. The
native archive reuses a check only while the same SQLite handle, file inode,
local change count, schema version, and external data version remain unchanged.
A chat write can therefore force another full archive scan. IMAP body/date
FETCHes can reuse a prior check of the same UIDVALIDITY and UID on the same
bound mailbox. A new process starts with an empty cache.

At thousands of nightly releases, daily cumulative reads can require
quadratic I/O in the number of days. Bounded per-turn I/O would require a
site that exposes a separately verifiable immutable snapshot identity covering
both source inventories, historical edits and rosters, and the release clock,
or an authenticated complete change feed with deletions and replay protection.
A last rowid, timestamp, count, or operator-invented version is insufficient.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections import Counter
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from email import message_from_bytes
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from types import SimpleNamespace

from pacbench.email_fixture import (
    OBSERVED_HEADERS,
    MailboxServer,
    email_server,
    mail_address,
)
from pacbench.hermes_gateway import GatewayLifecycleError
from pacbench.imessage_fixture import identity
from pacbench.provider_versions import (
    MEMCAL_WEEK_REVISION,
    MEMCAL_WEEK_TREE,
    memcal_source,
    verify_memcal_source,
)
from pacbench.providers import BOTH
from pacbench.workflows.spec import instant


def digest(data):
    return hashlib.sha256(data).hexdigest()


def frozen_messages_digest(messages):
    return digest(json.dumps(messages, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':')).encode())


def _mail(source, observed, binding):
    from memcal.sources import proton
    raw = observed['rfc822']
    message = message_from_bytes(raw.encode())
    own, parent, chain = proton.message_ids(message)
    subject = proton.header_str(message, 'Subject')
    body = proton.extract_text(message).replace('\r\n', '\n')
    frozen_body = source['text'].replace('\r\n', '\n')
    folder = observed['folder']
    if (own != binding['external_id'] or (not parent and not chain and binding['archive_thread'] != own)
            or parseaddr(proton.header_str(message, 'From'))[1].lower()
            != observed['sender'].lower() or subject != source['structured']['email_subject']
            or parsedate_to_datetime(message['Date']) != instant(source['written_at'])
            or body not in (frozen_body, frozen_body + '\n')
            or observed['folder_role'] != ('Sent' if source['from_owner'] else 'INBOX')
            or not folder or type(observed['uid']) is not int or observed['uid'] <= 0
            or not str(observed['uidvalidity']).isdigit() or int(observed['uidvalidity']) <= 0):
        raise ValueError('Source-side IMAP message differs from frozen email or delivery identity')
    declared = source['structured'].get('email_headers', {})
    for name in {'To', 'Cc', 'In-Reply-To', 'References'} | set(declared):
        if proton.header_str(message, name) != declared.get(name, ''):
            raise ValueError('Source-side IMAP header differs from a supplied header')
    # The pinned product can intentionally strip quotations and normalize URLs.
    # Check its exact output for the actual wire body, preserving both digests.
    text, body_meta = proton._shape_body(proton.extract_text(message), subject)
    meta = {**body_meta, 'folder': folder, 'uid': observed['uid'], 'uidvalidity': str(observed['uidvalidity']),
            'subject': subject, 'message_id': own, 'in_reply_to': parent, 'references': chain}
    return text.strip(), meta


class MemCalSourceWitness:
    def __init__(self, connection, site, messages, documents, *, bindings_path, bindings_sha256, release, observe):
        path = Path(bindings_path)
        if path.is_symlink() or not path.is_file():
            raise ValueError('A separate pinned site source-identity mapping is required')
        raw = path.read_bytes()
        if digest(raw) != bindings_sha256:
            raise ValueError('Site source-identity mapping changed')
        bindings = json.loads(raw)
        self.rows = {m['source_id']: m for m in messages}
        if (len(self.rows) != len(messages) or bindings['account'] != site.account
                or bindings['frozen_messages_sha256'] != frozen_messages_digest(messages)
                or set(bindings['records']) != set(self.rows)):
            raise ValueError('Site bindings differ from the frozen corpus or account')
        self.ids = {(m['channel'], bindings['records'][sid]['external_id']): sid for sid, m in self.rows.items()}
        if len(self.ids) != len(messages) or any(not key[1] for key in self.ids):
            raise ValueError('Site bindings repeat or omit a native identity')
        self.db, self.site, self.release, self.observe = connection, site, release, observe
        self.store = Path(site.memcal_home) / "memcal.db"
        actual = connection.execute("PRAGMA database_list").fetchone()[2]
        if not self.store.is_absolute() or self.store.resolve() != self.store or Path(actual).resolve() != self.store:
            raise ValueError("Source witness connection differs from the bound native store")
        stat = self.store.stat()
        self.store_identity = (stat.st_dev, stat.st_ino)
        self.checked_archive = None
        self.bindings, self.bindings_sha256 = bindings, bindings_sha256
        self.documents = list(documents)
        self.cutoffs = list(dict.fromkeys(d.cutoff for d in self.documents))
        self.completed = []
        if not callable(release) or not callable(observe):
            raise ValueError("Site must supply distinct release and read-only upstream observation operations")
        self.started = set()
        self.last_observation = None
        self.active = None
        self.expected = {}
        self.collection_ids = {}
        self.receipt = None
        self.verify_mailbox = None

    def _now(self):
        return (instant(self.active['cutoff']) + timedelta(seconds=time.monotonic()-self.start)).isoformat()

    @staticmethod
    def _boot_id():
        # The supported native gateway and admission owner already bind this host clock.
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def collection_clock(self, cutoff):
        if (self.active is not None or len(self.completed) >= len(self.cutoffs)
                or self.cutoffs[len(self.completed)] != cutoff or cutoff in self.started):
            raise ValueError('Source release must advance exactly once in frozen order')
        self.source_inventory()
        self.started.add(cutoff)
        self.start = time.monotonic()
        observed = self._observe(cutoff, self.release(cutoff))
        return {**observed, 'release_monotonic':self.start, 'host_boot_id':self._boot_id()}

    def _observe(self, cutoff, observation):
        if (observation['account'] != self.site.account or observation['cutoff'] != cutoff
                or instant(observation['as_of']) != instant(cutoff) or set(observation['channels']) != set(BOTH)):
            raise ValueError('Site did not observe both source channels at the requested cutoff')
        instant(observation['service_at'])
        visible = {sid for doc in self.documents if instant(doc.cutoff) <= instant(cutoff) for sid in doc.source_ids}
        expected = {}
        for channel, channel_record in observation['channels'].items():
            if instant(channel_record['as_of']) != instant(cutoff):
                raise ValueError('Source-side channel observation has a different cutoff')
            for observed in channel_record['records']:
                sid = self.ids.get((channel, observed['external_id']))
                if sid not in visible or sid in expected:
                    raise ValueError('Source-side inventory contains an unknown, repeated or future item')
                source = self.rows[sid]
                binding = self.bindings['records'][sid]
                people = self.bindings['people'][channel]
                thread = (self.bindings['threads'][channel][source['thread']] if channel == 'imessage'
                          else binding['archive_thread'])
                if (observed['thread'] != thread
                        or observed['sender'] != people[source['sender']]
                        or type(observed['from_owner']) is not bool or observed['from_owner'] != source['from_owner']
                        or instant(observed['written_at']) != instant(source['written_at'])
                        or instant(observed['arrived_at']) != instant(source['arrived_at'])):
                    raise ValueError('Source-side sender, thread, direction or clock differs from frozen input')
                if channel == 'imessage':
                    # Historical roster evidence is an observation per message, never the current chat roster.
                    wanted = [people[name] for name in source['structured']['participants']]
                    if (sorted(observed['participants_at_message']) != sorted(wanted)
                            or observed['text'] != source['text']):
                        raise ValueError('Historical Messages roster or frozen body differs')
                    text, meta = source['text'], {}
                else:
                    if observed['folder'] != channel_record['folders'][observed['folder_role']]:
                        raise ValueError('Source-side IMAP folder differs from its observed role')
                    text, meta = _mail(source, observed, binding)
                expected[sid] = (digest(text.encode()), meta)
        if set(expected) != visible or any(expected.get(sid) != value for sid, value in self.expected.items()):
            raise ValueError('Source-side prefix missing or previously observed records changed')
        if self.verify_mailbox:
            self.verify_mailbox(observation['channels']['email'], bodies=True)
        self.expected = expected
        self.active = deepcopy(observation)
        return {**{key: observation[key] for key in ('cutoff', 'as_of', 'service_at')},
                'observation_sha256': self._observation_digest(), 'source_map_sha256': self.bindings_sha256}

    def _observation_digest(self):
        # Exclude observation time so a fresh read of the same prefix binds identically.
        return digest(json.dumps(self.active['channels'], sort_keys=True, ensure_ascii=False,
                                 separators=(',', ':')).encode())

    def _archive_revision(self):
        info = self.store.stat()
        if (info.st_dev, info.st_ino) != self.store_identity:
            raise ValueError('Bound native store was replaced during verification')
        # data_version covers external commits; total_changes covers this handle's
        # own writes. A rollback may not advance either, so never cache inside a transaction.
        if self.db.in_transaction:
            return None
        data_version = self.db.execute('PRAGMA data_version').fetchone()[0]
        schema_version = self.db.execute('PRAGMA schema_version').fetchone()[0]
        return self.db, self.db.total_changes, data_version, schema_version

    def _archive(self, collection_ids):
        before = self._archive_revision()
        actual = {}
        for ch, key, ts, thread, handle, owner, text, meta, collection in self.db.execute(
                "SELECT channel,external_id,ts,thread,handle,from_me,text,meta,collection_id "
                "FROM archive WHERE channel != 'agent'"):
            sid = self.ids.get((ch, key))
            if sid not in collection_ids or sid in actual:
                raise ValueError('Native archive contains an unknown, duplicated or future source')
            source, binding = self.rows[sid], self.bindings['records'][sid]
            target, fields = self.expected[sid]
            stored = json.loads(meta)
            if (collection != collection_ids[sid] or instant(ts) != instant(source['written_at'])
                    or owner != int(source['from_owner']) or thread != binding['archive_thread']
                    or handle != binding['archive_handle'] or digest(text.encode()) != target
                    or any(stored.get(k) != v for k, v in fields.items())
                    or stored.get('body_error') or stored.get('body_excluded')):
                raise ValueError('Native archive differs from the submitted source or stock email shaping')
            actual[sid] = {'archive_sha256': target, 'source_sha256': digest(source['text'].encode())}
        if set(actual) != set(collection_ids):
            raise ValueError('Native archive is missing a released source')
        after = self._archive_revision()
        if before is not None and after != before:
            raise ValueError('Native store changed during archive verification')
        self.checked_archive = (after, collection_ids) if after is not None else None
        return actual

    def _cursors(self, observation):
        result = {}
        for channel, observed in observation['channels'].items():
            cursor = observed['cursor']
            if channel == 'email':
                folders = observed['folders']
                if set(folders) != {'INBOX', 'Sent'} or len(set(folders.values())) != 2 or set(cursor) != set(folders.values()):
                    raise ValueError('Missing distinct synthetic Inbox and Sent observations')
                wanted = {}
                for folder, watermark in cursor.items():
                    highest, validity = watermark['highest_uid'], watermark['uidvalidity']
                    if type(highest) is not int or highest < 0 or not str(validity).isdigit() or int(validity) <= 0:
                        raise ValueError('Invalid source-side IMAP watermark')
                    seen = [item['uid'] for item in observed['records'] if item['folder'] == folder]
                    if max(seen, default=0) != highest or any(
                            str(item['uidvalidity']) != str(validity) for item in observed['records'] if item['folder'] == folder):
                        raise ValueError('Source-side IMAP inventory differs from its live folder cursor')
                    wanted['watermark.proton.' + folder] = f'{validity}:{highest}' if highest else None
            elif self.site.imessage_backend == 'chatdb':
                last = cursor['last_rowid']
                if type(last) is not int or last < 0 or (observed['records'] and not last):
                    raise ValueError('Invalid platform Messages row cursor')
                wanted = {'imessage.rowid': str(last) if last else None}
            else:
                boundary = cursor['boundary_ids']
                after = cursor['after_ms']
                if (type(after) is not int or after < 0 or not isinstance(boundary, list)
                        or len(set(boundary)) != len(boundary) or observed['records'] and (not after or not boundary)):
                    raise ValueError('Invalid BlueBubbles boundary observation')
                wanted = {'bluebubbles.after': str(after) if after else None,
                          'bluebubbles.cursor_version': '2' if after else None,
                          'bluebubbles.boundary_ids': sorted(boundary) if after else None}
            readback = {}
            for key, expected in wanted.items():
                row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
                value = row[0] if row else None
                if key == 'bluebubbles.boundary_ids' and value is not None:
                    value = sorted(json.loads(value))
                if value != expected:
                    raise ValueError('Native cursor differs from independently observed source watermark')
                readback[key] = value
            result[channel] = readback
        return result

    def connector_acceptance(self, raw, cutoff):
        if self.active is None or cutoff != self.active['cutoff'] or self.receipt is not None:
            raise ValueError('Collection has no matching source release')
        collection = raw['collection_id']
        row = self.db.execute('SELECT finished_at,error FROM collections WHERE id=?', (collection,)).fetchone()
        if not row or not row[0] or row[1] or type(collection) is not int or collection in self.collection_ids.values():
            raise ValueError('Native collection is incomplete or already attributed')
        ids = {sid for d in self.documents if d.cutoff == cutoff for sid in d.source_ids}
        native = {ch: (count, status, err) for ch, count, status, err in self.db.execute(
            'SELECT channel,archived,status,error FROM collection_sources WHERE collection_id=?', (collection,))}
        if set(native) != set(BOTH) or set(raw['readers']) != set(BOTH):
            raise ValueError('Native collection did not run both sources')
        for ch in BOTH:
            count = sum(self.rows[sid]['channel'] == ch for sid in ids)
            r = raw['readers'][ch]
            if native[ch][:2] != (count, 'complete') or native[ch][2] or r['archived'] != count or r['more'] or r['error']:
                raise ValueError('Native collection counters or terminal status differ')
        pending = {**self.collection_ids, **dict.fromkeys(ids, collection)}
        rows = self._archive(pending)
        cursors = self._cursors(self.active)
        self.receipt = {'native_id': str(collection), 'ids': pending, 'cutoff': cutoff, 'cursors': cursors}
        return {'native_id': str(collection), 'accepted': True, 'at': self._now(),
                'request': {'sync_through': cutoff, 'channels': list(BOTH)},
                'source_map_sha256': self.bindings_sha256, 'source_observation_sha256': self._observation_digest(),
                'rows': {sid: rows[sid] for sid in sorted(ids)},
                'native_cursors': cursors}

    def connector_readback(self, accepted, *, processing):
        if (not self.receipt or accepted['native_id'] != self.receipt['native_id']
                or type(processing.get('run_id')) is not int or not processing.get('finished_at')
                or processing.get('pending_spool') != 0):
            raise ValueError('Missing native nightly process or source acceptance')
        cutoff, pending = self.receipt['cutoff'], self.receipt['ids']
        self._archive(pending)
        cursors = self._cursors(self.active)
        if cursors != self.receipt['cursors']:
            raise ValueError('Source cursor changed during processing')
        new = sorted(set(pending) - set(self.collection_ids))
        ready = {'native_id': accepted['native_id'], 'ready': True, 'at': self._now(),
                 'readback': processing, 'new_source_ids': new, 'source_cursor': cursors,
                 'channel_readback': {ch: {'cursor': cursors[ch], 'as_of': cutoff,
                     'new_source_ids': [sid for sid in new if self.rows[sid]['channel'] == ch]} for ch in BOTH}}
        self.last_observation = self.active
        self.collection_ids, self.receipt, self.active = pending, None, None
        self.completed.append(cutoff)
        return ready

    def source_inventory(self):
        revision = self._archive_revision()
        cached = self.checked_archive
        # Each completed release replaces the private collection_ids mapping.
        if revision is None or cached is None or revision != cached[0] or self.collection_ids is not cached[1]:
            self._archive(self.collection_ids)
        cutoff = self.completed[-1] if self.completed else None
        current = self.observe(cutoff)
        if current['account'] != self.site.account or current['cutoff'] != cutoff or set(current['channels']) != set(BOTH):
            raise ValueError('Live site upstream differs from the bound account or completed cutoff')
        if self.last_observation is None:
            if any(channel['records'] for channel in current['channels'].values()):
                raise ValueError('Fresh synthetic upstream already exposes source records')
            self._cursors(current)
        else:
            if current['as_of'] != self.last_observation['as_of'] or current['channels'] != self.last_observation['channels']:
                raise ValueError('Live site source inventory changed since verified collection')
            self._cursors(current)
        if self.verify_mailbox:
            self.verify_mailbox(current['channels']['email'], bodies=False)
        return [doc.filename for doc in self.documents if doc.cutoff in self.completed]

    def verify_source_resume(self, progress):
        """Restore only receipt-backed effects, checking the current site without advancing it."""
        saved_db = self.db
        try:
            with closing(sqlite3.connect(self.store.as_uri() + '?mode=ro', uri=True)) as self.db:
                return self._restore(progress)
        finally:
            self.db = saved_db

    def _restore(self, progress):
        last = progress.last_cutoff()
        if last is not None and last not in self.cutoffs:
            raise ValueError('Saved cutoff is outside the frozen week')
        index = self.cutoffs.index(last) + 1 if last is not None else 0
        receipts = []
        for i, at in enumerate(self.cutoffs):
            # get_effect rejects unfinished/unknown native operations without replay.
            clock, accepted, dream = [progress.get_effect(phase, at) for phase in ('source-clock', 'sync', 'dream')]
            if ((i < index and not (clock and accepted and dream)) or (dream and not accepted)
                    or (accepted and not clock) or (clock and i > index)):
                raise ValueError('Source restoration requires a contiguous verified release prefix')
            if clock:
                if clock['source_map_sha256'] != self.bindings_sha256:
                    raise ValueError('Source bindings changed since release')
                receipts.append((at, clock, accepted, dream))
        self.completed = self.cutoffs[:index]
        self.started = {at for at, *_ in receipts}
        self.collection_ids, self.receipt, self.active, self.expected, self.last_observation = {}, None, None, {}, None
        native_cursors = {ch: {} for ch in BOTH}
        upstream_cutoff = receipts[-1][0] if receipts else None
        if receipts:
            run_ids = set()
            for at, _, _, dream in receipts:
                if dream:
                    run = self.db.execute('SELECT finished_at, mode, error FROM runs WHERE id=?', (dream['run_id'],)).fetchone()
                    if (dream['run_id'] in run_ids or dream['pending_spool'] != 0 or not run or not run[0]
                            or run[0] != dream['finished_at'] or run[1] != 'nightly' or run[2]):
                        raise ValueError('Native nightly Dream differs from its durable completion')
                    run_ids.add(dream['run_id'])
            last_clock = receipts[-1][1]
            if upstream_cutoff != last:
                if (last_clock.get('host_boot_id') != self._boot_id() or
                        not isinstance(last_clock.get('release_monotonic'), (int, float)) or
                        not 0 <= last_clock['release_monotonic'] <= time.monotonic()):
                    raise ValueError('partial release lacks an original same-host timing witness')
                self.start = last_clock['release_monotonic']
            else:
                self.start = time.monotonic()
            actual = self._observe(upstream_cutoff, self.observe(upstream_cutoff))
            if actual['observation_sha256'] != receipts[-1][1]['observation_sha256']:
                raise ValueError('Fresh upstream readback differs from the saved release')
            ids = {}
            for at, clock, accepted, dream in receipts:
                if not accepted:
                    continue
                expected = {sid for d in self.documents if d.cutoff == at for sid in d.source_ids}
                if (accepted['source_map_sha256'] != self.bindings_sha256 or set(accepted['rows']) != expected
                        or accepted['source_observation_sha256'] != clock['observation_sha256']
                        or any(accepted['rows'][sid]['archive_sha256'] != self.expected[sid][0] for sid in expected)):
                    raise ValueError('Saved source acceptance does not match the observed prefix')
                cid = int(accepted['native_id'])
                if cid in ids.values():
                    raise ValueError('A native collection is bound to multiple releases')
                collection = self.db.execute('SELECT finished_at,error FROM collections WHERE id=?', (cid,)).fetchone()
                status = dict(self.db.execute('SELECT channel,status FROM collection_sources WHERE collection_id=?', (cid,)))
                if not collection or not collection[0] or collection[1] or status != {ch: 'complete' for ch in BOTH}:
                    raise ValueError('Saved native collection no longer has both completed readers')
                ids.update(dict.fromkeys(expected, cid))
                native_cursors = accepted['native_cursors']
                if at in self.completed:
                    self.collection_ids = ids.copy()
                else:
                    self.receipt = {'native_id': accepted['native_id'], 'ids': ids.copy(),
                                    'cutoff': at, 'cursors': native_cursors}
            self._archive(ids)
            if receipts[-1][2]:
                if self._cursors(self.active) != native_cursors:
                    raise ValueError('Native source cursor changed after its saved collection')
            else:
                for channel in native_cursors.values():
                    for key, expected in channel.items():
                        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
                        value = row[0] if row else None
                        if key == 'bluebubbles.boundary_ids' and value is not None:
                            value = sorted(json.loads(value))
                        if value != expected:
                            raise ValueError('Uncollected release has an unexpected native cursor')
            if upstream_cutoff == last:
                self.last_observation, self.active = self.active, None
        else:
            self.source_inventory()
        state = self.store.stat()
        return {'store_path': str(self.store), 'store_device': state.st_dev, 'store_inode': state.st_ino, 'cutoff': last, 'upstream_cutoff': upstream_cutoff,
                'source_ids': sorted(self.receipt['ids'] if self.receipt else self.collection_ids),
                'channels': native_cursors, 'source_map_sha256': self.bindings_sha256}


# Digests of the reviewed 2026-11-19 recovery archive members, including LF.
SOURCE_BINDING = 'a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a'


SOURCE_INDEX = 'eab426c68f478ed4cfbda11d8a89dfc0ae7c0b5622953f93fa9d0d084f405f39'


CONTINUING_ACCOUNT = '5eed5aea85bbca691d776fe1edf497fcb13285d5f49daff4b3c1049417e7cef0'


def member_digest(value):
    raw = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_contract(docs, turns, source_basis):
    index = [{'name':d.filename, 'cutoff':d.cutoff, 'hash':d.sha256, 'ids':list(d.source_ids)} for d in docs]
    if (source_basis.get('sha256') != SOURCE_BINDING or member_digest(index) != SOURCE_INDEX
            or member_digest([asdict(t) for t in turns]) != CONTINUING_ACCOUNT):
        raise ValueError('Frozen MemCal source releases or continuing-account evaluation differ')
    return dict(source_binding=source_basis['sha256'], source_index_sha256=SOURCE_INDEX,
                contract_sha256=CONTINUING_ACCOUNT, records=sum(len(d.source_ids) for d in docs),
                document_channels=dict(Counter(d.channel for d in docs)),
                nightly_cutoffs=sorted({d.cutoff for d in docs}), turns=len(turns),
                chats=len({t.session for t in turns}), gradable_turns=sum(bool(t.claims) for t in turns),
                criteria=sum(len(t.claims) for t in turns))


def source_preflight(contract, site_path):
    from pacbench.providers.memcal.site import (
        MemCalSite,
        MemCalSiteError,
        inspect_memcal_site,
    )
    result = dict(contract=contract, paid_calls=0, provider_answer_calls=0)
    if site_path is None:
        return {**result, 'state':'blocked-before-source-access',
                'reason':'No dedicated synthetic macOS Messages/Contacts or BlueBubbles and IMAP site was supplied.'}
    try:
        with site_path.open() as stream:
            site = MemCalSite(**json.load(stream))
        observation = inspect_memcal_site(site)
    except MemCalSiteError as exc:
        return {**result, 'state':'blocked-at-source-access', 'reason':str(exc)}
    except (OSError, TypeError, ValueError):
        return {**result, 'state':'blocked-at-source-access', 'reason':'Invalid explicit synthetic site declaration.'}
    return {**result, 'state':'source-access-verified', 'source_access':observation}


# SHA-256 of the complete OpenAI function envelopes, sorted by function name.
# UTF-8 JSON, sorted keys, compact separators. Only catalog order is immaterial.
SCHEMAS_SHA256 = 'e75e7a0cedbac49f5c21a363dd96717fbdb47a4fcfa0dd179288d7e27fecd069'


TOOLS = ('memcal_activity', 'memcal_add', 'memcal_alias', 'memcal_answer',
         'memcal_conversation', 'memcal_drop', 'memcal_list_days', 'memcal_list_month',
         'memcal_merge', 'memcal_move_once', 'memcal_note', 'memcal_open',
         'memcal_open_source', 'memcal_refresh', 'memcal_reviewed', 'memcal_schedule',
         'memcal_search_archive', 'memcal_search_wiki', 'memcal_todo', 'memcal_update')


def manifest():
    return dict(memcal_revision=MEMCAL_WEEK_REVISION, memcal_tree=MEMCAL_WEEK_TREE,
                driver_schemas_sha256=SCHEMAS_SHA256)


def verify_driver_schemas(tools):
    if not isinstance(tools, list):
        raise ValueError('missing MemCal driver schemas')
    try:
        ordered = sorted(tools, key=lambda t: t['function']['name'])
        encoded = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('malformed MemCal driver schemas') from exc
    if hashlib.sha256(encoded.encode()).hexdigest() != SCHEMAS_SHA256:
        raise ValueError('MemCal driver schemas differ from the pinned stock manifest')


def verify_captured_driver(request):
    from pacbench.evidence_store import binding_digest as digest
    body = request.get('request')
    if (not isinstance(body, dict) or not request.get('request_id') or not request.get('messages')
            or request.get('canonical_request_sha256') != digest(body)
            or body.get('messages') != request.get('messages') or body.get('model') != request.get('model')
            or sorted(request.get('tools', [])) != list(TOOLS)):
        raise ValueError('MemCal driver capture differs from its recorded request')
    verify_driver_schemas(body.get('tools'))


def verify_envelope(message, m, observed_headers):
    if parsedate_to_datetime(message['Date'])!=datetime.fromisoformat(m['written_at']):raise ValueError('Source email time changed')
    if parseaddr(message['From'])[1]!=mail_address(m['sender']):raise ValueError('Source email author changed')
    if observed_headers:
        headers=m['structured'].get('email_headers',{})
        for header in ('To','Cc','References','In-Reply-To'):
            if message.get(header)!=headers.get(header):
                raise ValueError('Native email routing or reply headers differ from the observed source')
        transport_id=f"<{uuid.uuid5(uuid.NAMESPACE_URL,m['source_id'])}@mail.example.test>"
        if (str(message['Subject'])!=m['structured']['email_subject'] or
                message.get('Message-ID')!=headers.get('Message-ID',transport_id)):
            raise ValueError('Native email subject or identity changed')
    else:
        recipients={addr for _,addr in getaddresses(message.get_all('To',[]))};wanted={mail_address(p) for p in m['structured']['participants'] if p!=m['sender']}
        if recipients!=wanted:raise ValueError('Source email recipients changed')


def audit_import(component):
    repo=Path(__file__).resolve().parents[3];verify_memcal_source(repo)
    from memcal import textclean
    from memcal.sources import proton
    if not Path(proton.__file__).resolve().is_relative_to(memcal_source(repo).resolve()):
        raise ValueError('Wrong native Memcal module')
    component=Path(component).resolve();root=component/'repeat-1/memcal'
    source=[m for path in sorted((component/'sources').glob('*/messages.json')) for m in json.loads(path.read_text())]
    if len({m['source_id'] for m in source})!=len(source):raise ValueError('Duplicate source identity')
    with sqlite3.connect((root/'memcal-home/memcal.db').as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row;archive=[dict(r) for r in db.execute('select * from archive')]
    if len(archive)!=len(source):raise ValueError('Archive count differs from full source')
    native={(r['channel'],r['external_id']):r for r in archive}
    if len(native)!=len(archive):raise ValueError('Duplicate native archive identity')
    checks=[];mailboxes=sorted((component/'sources').glob('*/mailbox.json'));mailbox=mailboxes[-1]
    snapshot=json.loads(mailbox.read_text());observed_headers=snapshot.get('header_condition')==OBSERVED_HEADERS
    fixture=snapshot['messages'];email_sources={m['source_id']:m for m in source if m['channel']=='email'}
    if {r['source']['source_id'] for r in fixture}!=set(email_sources):raise ValueError('Mailbox omits source email')
    service=email_server(mailbox,root,verify_only=True) if observed_headers else MailboxServer(mailbox)
    with service as server:
        secrets={'PROTON_BRIDGE_HOST':'127.0.0.1','PROTON_BRIDGE_PORT':str(server.port),'PROTON_BRIDGE_USER':server.user,'PROTON_BRIDGE_PASSWORD':server.password}
        cfg=SimpleNamespace(secret=lambda name,alias:secrets.get(name))
        with proton.Bridge(cfg) as bridge:
            for folder in ['Sent','INBOX']:
                ok,_=bridge.select(folder)
                if not ok:raise ValueError('Native mailbox select failed')
                expected=[r for r in fixture if r['folder']==folder]
                if bridge.uids_since(0)!=[r['uid'] for r in expected]:raise ValueError('Native mailbox UID coverage differs')
                for row in expected:
                    m=email_sources[row['source']['source_id']]
                    if row['source']!=m:
                        raise ValueError('Mailbox source metadata differs from the daily frozen input')
                    message=bridge.message(row['uid']);raw=proton.extract_text(message)
                    if raw.replace('\r\n','\n').strip()!=m['text'].strip():raise ValueError('Native reader did not receive complete email body')
                    verify_envelope(message, m, observed_headers)
                    key=('email',row['message_id'].strip('<>'));a=native[key];shaped,meta=proton._shape_body(raw,str(message['Subject']))
                    if shaped!=a['text'] or bool(a['from_me'])!=m['from_owner'] or datetime.fromisoformat(a['ts'])!=datetime.fromisoformat(m['written_at']):raise ValueError('Stored native email differs from stock reader output')
                    quotes=textclean.strip_quotes(raw)!=raw
                    urls=any(textclean.shorten_url(match)!=match.group(0) for match in textclean.URL.finditer(raw))
                    quoted=[line.lstrip()[1:].strip() for line in raw.splitlines() if line.lstrip().startswith('>')]
                    prior=[s['source_id'] for s in source if s['thread']==m['thread'] and s['written_at']<m['written_at'] and s['text'].strip() in quoted]
                    checks.append({'source_id':m['source_id'],'rfc822_sha256':hashlib.sha256(row['rfc822'].encode()).hexdigest(),'native_archive_id':a['id'],'native_reader_received_full_body':True,'envelope_author_time_recipients_verified':True,'native_archive_shape_exact':True,'body_chars':meta['body_chars'],'kept_chars':meta['kept_chars'],'native_body_truncated_flag':bool(meta.get('body_truncated')),'quote_history_removed':quotes,'native_url_normalization':urls,'length_limit_reached':len(raw)>=min(proton.BODY_CHARS,proton.ARCHIVE_BODY_CHARS),'quoted_text_duplicates_prior_source_ids':prior})
        commands=server.commands
    for m in source:
        if m['channel']!='imessage':continue
        identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-message:'+m['source_id'])).upper();a=native[('imessage',identifier)]
        if a['text']!=m['text'] or bool(a['from_me'])!=m['from_owner'] or datetime.fromisoformat(a['ts'])!=datetime.fromisoformat(m['written_at']):raise ValueError('Stored native iMessage differs from original')
        if not m['from_owner'] and a['handle']!=identity(m['sender'])['handle']:raise ValueError('Native source speaker changed')
    if any(c['length_limit_reached'] for c in checks):raise ValueError('Length limit reached; inspect before qualification')
    return {'version':'memcal-native-input-cause-audit-v1','state':'pass','source_messages_verified':len(source),'native_imessage_bodies_times_owners_incoming_speakers_verified':len(source)-len(checks),'native_tls_imap_emails_verified':len(checks),'native_email_transport':'Stock Bridge.message over read-only TLS IMAP; no archive writes, dream or model calls','native_memcal_source_revision_verified':True,'harness_transport_losses':[],'native_length_limits_reached':0,'native_quote_removal_count':sum(c['quote_history_removed'] for c in checks),'native_url_normalization_count':sum(c['native_url_normalization'] for c in checks),'native_shortened_body_flags':sum(c['native_body_truncated_flag'] for c in checks),'email_checks':checks,'native_commands':commands,'model_calls':0,'native_store_modified':False,'conclusion':'Complete source transport and stock archive shaping verified. Shortening is native preprocessing, not harness truncation; extraction quality remains a benchmark outcome.'}


def finished_nightly_ids(conn):
    rows = conn.execute("SELECT id,finished_at,error FROM runs WHERE mode='nightly' ORDER BY id").fetchall()
    ids = [row[0] for row in rows]
    if (len(ids) != len(set(ids)) or any(type(row[0]) != int or not row[1] or row[2] for row in rows)):
        raise GatewayLifecycleError('Native nightly Dream run identities are duplicate or unfinished')
    return ids


def memcal_archive_readback(preparation,ids,store):
    """Compare submitted dialogue with actual native archive; never repair it.

    MemCal's email cleaner can deliberately transform text. Preserve those
    differences as product observations, not exact-source or extraction proof.
    """
    import email
    import sqlite3
    from contextlib import closing

    from pacbench.imessage_fixture import identity
    uri = (Path(store)/'memcal.db').resolve().as_uri()+'?mode=ro'
    with closing(sqlite3.connect(uri,uri=True)) as conn:
        actual = {}
        for ch,key,text,ts,owner,handle,thread in conn.execute(
                'SELECT channel,external_id,text,ts,from_me,handle,thread FROM archive'):
            if (ch,key) in actual:
                raise GatewayLifecycleError('Duplicate native archive identity')
            actual[ch,key] = text,ts,owner,handle,thread
    witnesses=[]
    matched=set()
    for identifier in ids:
        source=Path(preparation)/'sources'/identifier;fixture=json.loads((source/'chat.fixture.json').read_text())
        mailbox=json.loads((source/'mailbox.json').read_text()) if (source/'mailbox.json').exists() else {'messages':[]}
        for m in json.loads((source/'messages.json').read_text()):
            if m['channel']=='imessage':
                key = str(uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-message:'+m['source_id'])).upper()
                if key != fixture['messages'][m['source_id']]['guid']:
                    raise GatewayLifecycleError('Source GUID differs from the frozen record')
                expected = shaped = m['text']
                peers = [name for name in fixture['threads'][m['thread']]['participants'] if name != 'Me']
                group = len(peers)>1
                target = ('chat'+uuid.uuid5(uuid.NAMESPACE_URL,'pacbench-source-thread:'+m['thread']).hex
                          if group else identity(peers[0])['handle'])
                handle = (None if group else target) if m['from_owner'] else identity(m['sender'])['handle']
            else:
                from memcal.sources import proton
                row=next(r for r in mailbox['messages'] if r['source']['source_id']==m['source_id'])
                if row['source'] != m:
                    raise GatewayLifecycleError('Mailbox differs from frozen email')
                key = row['message_id'].strip('<>')
                message = email.message_from_string(row['rfc822'])
                from pacbench.email_fixture import OBSERVED_HEADERS
                verify_envelope(message, m, mailbox.get('header_condition') == OBSERVED_HEADERS)
                if message.get('Message-ID','').strip('<>') != key:
                    raise GatewayLifecycleError('Mailbox message identity differs from native wire input')
                raw = proton.extract_text(message)
                if raw.replace('\r\n','\n').strip() != m['text'].strip():
                    raise GatewayLifecycleError('Native reader received different source text')
                subject = proton.header_str(message, 'Subject')
                expected = subject+'\n\n'+m['text']
                shaped = proton._shape_body(raw,subject)[0]
            address = (m['channel'],key)
            if address in matched:
                raise GatewayLifecycleError('Duplicate submitted archive identity')
            matched.add(address)
            found=actual.get(address)
            if (found is None or datetime.fromisoformat(found[1]) != datetime.fromisoformat(m['written_at'])
                    or found[2] != int(m['from_owner']) or found[0] != shaped
                    or m['channel']=='imessage' and (found[3],found[4]) != (handle,'id:'+target)):
                raise GatewayLifecycleError('Native archive identity, text or attribution differs from source')
            text=found[0]
            condition=('exact' if text==expected else 'line-ending-only' if text.replace('\r\n','\n')==expected else 'native-text-transformation')
            witnesses.append({'source_id':m['source_id'],'channel':m['channel'],'condition':condition,
                'submitted_text_sha256':hashlib.sha256(expected.encode()).hexdigest(),'archive_text_sha256':hashlib.sha256(text.encode()).hexdigest()})
    if matched != set(actual):
        raise GatewayLifecycleError('Native archive has unrelated source records')
    return {'protocol':'native-archive-source-comparison-v1','compared':len(witnesses),
        'all_identities_and_times_match':True,'rows':witnesses,'native_text_unchanged':all(w['condition']=='exact' for w in witnesses)}
