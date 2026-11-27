"""Read-only access checks for an explicitly provisioned synthetic MemCal account."""
import glob
import hashlib
import os
import pwd
import re
import sqlite3
import ssl
import sys
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import urlsplit

from pacbench.providers import BOTH
from pacbench.providers.memcal.source import manifest

from ...provider_versions import (
    MEMCAL_WEEK_REVISION,
    memcal_week_pin,
    verify_memcal_source,
)


class MemCalSiteError(ValueError):
    """Safe to include in an access report without leaking account details."""


@dataclass(frozen=True)
class MemCalSite:
    """The site operator identifies these synthetic resources independently of MemCal."""
    account: str
    os_home: str
    memcal_home: str
    imessage_backend: str
    imap_host: str
    imap_port: int
    imap_user: str
    bluebubbles_url: str | None = None
    synthetic_chatdb: bool = False


def _homes(site):
    if not isinstance(site, MemCalSite) or not all(isinstance(value, str) and value.strip() for value in
            (site.account, site.os_home, site.memcal_home, site.imap_host, site.imap_user)):
        raise MemCalSiteError('Explicit synthetic OS account, store and IMAP identity are required')
    if type(site.imap_port) is not int or not 1 <= site.imap_port <= 65535:
        raise MemCalSiteError('Invalid synthetic IMAP port')
    home, store = Path(site.os_home), Path(site.memcal_home)
    if (home.resolve() != home or Path.home().resolve() != home or store.resolve() != store
            or not home.is_dir() or not store.is_dir()):
        raise MemCalSiteError('Synthetic OS home or existing MemCal store differs from the declared site')
    if site.imessage_backend == 'chatdb':
        if site.synthetic_chatdb:
            if (home != store.parent.parent / 'source-home' or not home.is_relative_to(store.parent.parent)
                    or home == Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
                    or site.imap_host != '127.0.0.1' or store.name != 'memcal-home'):
                raise MemCalSiteError('Synthetic chat.db requires a fresh isolated source home and loopback IMAP')
        elif sys.platform != 'darwin' or Path(pwd.getpwuid(os.getuid()).pw_dir).resolve() != home:
            raise MemCalSiteError('Direct Messages requires the declared macOS login and its platform-created store')
        if site.bluebubbles_url:
            raise MemCalSiteError('Direct Messages cannot use a BlueBubbles endpoint')
    elif site.imessage_backend == 'bluebubbles':
        if site.synthetic_chatdb:
            raise MemCalSiteError('Synthetic chat.db uses only the stock local reader')
        url = urlsplit(site.bluebubbles_url or '')
        if (url.scheme not in ('https', 'http') or not url.hostname or url.username is not None
                or url.password is not None or url.query or url.fragment):
            raise MemCalSiteError('Explicit synthetic BlueBubbles endpoint without credentials is required')
    else:
        raise MemCalSiteError('Unknown MemCal iMessage backend')
    return home, store


def _pin():
    repo = Path(__file__).resolve().parents[3]
    try:
        _, revision, _ = memcal_week_pin()
        pin = verify_memcal_source(repo)
        package = find_spec('memcal')
    except Exception:
        raise MemCalSiteError('Reviewed MemCal checkout and its importable stock package are required') from None
    if (pin['revision'] != revision or not package or not package.origin
            or Path(package.origin).resolve() != Path(pin['source']) / 'memcal' / '__init__.py'):
        raise MemCalSiteError('MemCal import differs from the selected Week checkout')
    return pin


def _bound_sources(site, connection, config):
    home, store = _homes(site)
    _pin()
    from memcal import identity, sources
    from memcal.config import Config
    from memcal.sources import bluebubbles, imessage, proton

    db_path = store / 'memcal.db'
    databases = {name: path for _, name, path in connection.execute('PRAGMA database_list')}
    if (type(config) is not Config or Path(config.home).resolve() != store or db_path.resolve() != db_path
            or Path(databases['main']).resolve() != db_path):
        raise MemCalSiteError('Active MemCal database differs from the named synthetic store')
    tables = {name for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'archive', 'collections', 'collection_sources', 'spool', 'runs'} <= tables:
        raise MemCalSiteError('Named synthetic database lacks the native MemCal collection schema')
    if (config.imessage_backend != site.imessage_backend
            or (site.imessage_backend == 'bluebubbles' and config.imessage_fallback)):
        raise MemCalSiteError('Selected iMessage backend differs or local fallback permits another source')
    # Contacts and the Messages DB are resolved by the product from OS HOME.
    for pattern in identity.CONTACTS_GLOBS:
        for match in glob.glob(os.path.expanduser(pattern)):
            if not Path(match).resolve().is_relative_to(home):
                raise MemCalSiteError('Contacts path is outside the declared synthetic account')
    if site.imessage_backend == 'chatdb':
        if imessage.CHAT_DB.resolve() != home / 'Library/Messages/chat.db':
            raise MemCalSiteError('Messages path differs from the declared macOS account')
    else:
        if bluebubbles.BlueBubbles(config).url != site.bluebubbles_url.rstrip('/'):
            raise MemCalSiteError('Configured BlueBubbles endpoint differs from the declared synthetic site')
    mailbox = proton.Bridge(config)
    if (mailbox.host, mailbox.port, mailbox.user) != (site.imap_host, site.imap_port, site.imap_user):
        raise MemCalSiteError('Configured IMAP endpoint or account differs from the declared synthetic mailbox')
    readers = {channel: sources.get(channel, config) for channel in BOTH}
    if type(readers['email']) is not proton.ProtonSource or type(readers['imessage']) is not imessage.IMessageSource:
        raise MemCalSiteError('Mixed collection requires both stock MemCal readers')
    return readers, mailbox


@contextmanager
def _opened_mailbox(mailbox):
    context = ssl.create_default_context()
    if mailbox.host in {'localhost', '127.0.0.1', '::1'}:
        # The stock local Bridge deliberately uses a self-signed certificate.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    # Use the pinned native transport without its service-launch-on-refusal path.
    try:
        mailbox._connect(context)
        mailbox._login()
        yield mailbox
    finally:
        mailbox._disconnect()


def _mail_folders(mailbox):
    from memcal.sources import proton
    with _opened_mailbox(mailbox):
        available = set(mailbox.folders())
        result = {}
        for folder in proton.FOLDERS:
            target = folder if folder in available else proton._match_folder(folder, available)
            if not target:
                raise MemCalSiteError('Synthetic IMAP requires both readable Inbox and Sent folders')
            selected, validity = mailbox.select(target)
            if not selected or not validity.isdigit() or int(validity) <= 0:
                raise MemCalSiteError('Synthetic IMAP folder selection or UIDVALIDITY is unverified')
            result[folder] = {'mode': 'readonly', 'uidvalidity': validity}
        return {'security': mailbox.security, 'folders': result}


def _verify_mail_snapshot(mailbox, observed, *, bodies, checked=None):
    """Read both complete UID sets before fetching any approved synthetic mail."""
    from memcal.sources import proton

    from pacbench.workflows.spec import instant

    def select(folder, validity):
        ok, value = mailbox.select(folder)
        if not ok or value != str(validity):
            raise MemCalSiteError('Live IMAP UIDVALIDITY differs from the frozen release')

    with _opened_mailbox(mailbox):
        available = set(mailbox.folders())
        folders = observed['folders']
        if (set(folders) != set(proton.FOLDERS) or len(set(folders.values())) != len(proton.FOLDERS)
                or set(observed['cursor']) != set(folders.values())):
            raise MemCalSiteError('Live IMAP release requires distinct Inbox and Sent cursors')
        inventory = {}
        for role in proton.FOLDERS:
            folder = role if role in available else proton._match_folder(role, available)
            if not folder or folder != folders[role]:
                raise MemCalSiteError('Live IMAP folders differ from the observed release')
            cursor = observed['cursor'][folder]
            select(folder, cursor['uidvalidity'])
            status, rows = mailbox.conn.uid('SEARCH', None, 'ALL')
            if status != 'OK' or not rows or not isinstance(rows[0], bytes):
                raise MemCalSiteError('Live IMAP folder inventory is unavailable')
            uids = [int(uid) for uid in rows[0].split()]
            records = [r for r in observed['records'] if r['folder'] == folder]
            if (len(set(uids)) != len(uids) or sorted(uids) != sorted(r['uid'] for r in records)
                    or max(uids, default=0) != cursor['highest_uid']):
                raise MemCalSiteError('Live IMAP UID inventory differs from the approved prefix')
            inventory[folder] = records
        if bodies:
            for folder, records in inventory.items():
                select(folder, observed['cursor'][folder]['uidvalidity'])
                for record in records:
                    uid = str(record['uid'])
                    key = folder, str(observed['cursor'][folder]['uidvalidity']), uid
                    wire = record['rfc822'].encode()
                    fingerprint = hashlib.sha256(wire).digest(), record['arrived_at']
                    # RFC 3501 section 2.3.1.1 makes body and INTERNALDATE immutable
                    # for the life of this mailbox UIDVALIDITY/UID combination.
                    if checked is not None and checked.get(key) == fingerprint:
                        continue
                    status, rows = mailbox.conn.uid('FETCH', uid, '(INTERNALDATE)')
                    match = (re.search(rb'INTERNALDATE "([^"]+)"', rows[0])
                             if status == 'OK' and rows and isinstance(rows[0], bytes) else None)
                    if not match or datetime.strptime(match[1].decode(), '%d-%b-%Y %H:%M:%S %z') != instant(record['arrived_at']):
                        raise MemCalSiteError('Live IMAP internal delivery date differs from frozen arrival')
                    status, rows = mailbox.conn.uid('FETCH', uid, '(BODY.PEEK[])')
                    if (status != 'OK' or not rows or not isinstance(rows[0], tuple)
                            or rows[0][1] != wire):
                        raise MemCalSiteError('Live IMAP bytes differ from the site observation')
                    if checked is not None:
                        checked[key] = fingerprint


def verify_memcal_mailbox(site, connection, config, observed, *, bodies, checked=None):
    try:
        _, mailbox = _bound_sources(site, connection, config)
        _verify_mail_snapshot(mailbox, observed, bodies=bodies, checked=checked)
    except MemCalSiteError:
        raise
    except Exception:
        raise MemCalSiteError('Cannot verify released mail on the bound synthetic IMAP server') from None


def inspect_memcal_readers(site, connection, config, *, channels=BOTH):
    if len(channels) != len(BOTH) or set(channels) != set(BOTH):
        raise MemCalSiteError('MemCal site preflight requires the full mixed channel scope')
    try:
        readers, mailbox = _bound_sources(site, connection, config)
        ok, _ = readers['imessage'].check(config)
        if not ok:
            raise MemCalSiteError('Native Messages access to the bound synthetic source failed')
        mail = _mail_folders(mailbox)
    except MemCalSiteError:
        raise
    except Exception:
        # Native client errors can contain account names, paths or credential-bearing URLs.
        raise MemCalSiteError('Native MemCal source-access check failed for the declared site') from None
    revision = memcal_week_pin()[1]
    return {'state': 'source-access-verified', 'memcal_revision': revision,
            **(manifest() if revision == MEMCAL_WEEK_REVISION else {}),
            'imessage_backend': site.imessage_backend, 'imap': mail,
            **({'source_condition':'synthetic-chatdb-imap-v1'} if site.synthetic_chatdb else {}),
            'scope': 'Read-only source access. Platform provenance, as-of inventory and processing require separate witnesses.'}


def inspect_memcal_site(site):
    """Check an existing synthetic site. Never discover an account from local defaults."""
    _, store = _homes(site)
    _pin()
    from memcal import config

    env_file = store / '.env'
    if env_file.is_symlink() or any(p.exists() and p.resolve() != env_file for p in
                                    (config.PROJECT_ROOT / '.env', Path.cwd() / '.env')):
        raise MemCalSiteError('Native config would read an env file outside the synthetic store')
    path = store / 'memcal.db'
    if path.is_symlink() or not path.is_file():
        raise MemCalSiteError('Existing synthetic MemCal database is required')
    try:
        bound = config.load(home=store)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
            return inspect_memcal_readers(site, connection, bound)
    except MemCalSiteError:
        raise
    except Exception:
        raise MemCalSiteError('Cannot load the synthetic site config or open its store read-only') from None
