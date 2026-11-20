"""Site preflight controls. Local protocol fixtures cannot establish platform provenance."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pacbench.email_fixture import MailboxServer
from pacbench.workflow_delivery import MemCalReaders
from pacbench import workflow_memcal as module


def site(tmp_path, monkeypatch):
    home = tmp_path / 'os-home'
    store = tmp_path / 'memcal-home'
    home.mkdir()
    store.mkdir()
    monkeypatch.setenv('HOME', str(home))
    return module.MemCalSite('synthetic-site', str(home), str(store), 'bluebubbles',
                             '127.0.0.1', 1000, 'fixture@example.test', 'http://127.0.0.1:1234')


@pytest.mark.parametrize('fault', ['ambient-home', 'missing-user', 'relative-store', 'backend', 'credential-in-url'])
def test_rejects_unbound_site_before_import_or_store_access(tmp_path, monkeypatch, fault):
    binding = site(tmp_path, monkeypatch)
    changes = {'ambient-home': {'os_home': str(tmp_path)}, 'missing-user': {'imap_user': ''},
               'relative-store': {'memcal_home': '.'}, 'backend': {'imessage_backend': 'auto'},
               'credential-in-url': {'bluebubbles_url': 'https://secret@localhost'}}
    binding = replace(binding, **changes[fault])
    monkeypatch.setattr(module, '_pin', lambda: pytest.fail('touched provider'))
    with pytest.raises(module.MemCalSiteError):
        module.inspect_memcal_site(binding)
    assert not (Path(binding.memcal_home) / 'memcal.db').exists()


def test_collection_requires_preflight_before_import_contacts_or_reader_access():
    readers = MemCalReaders(None, None)
    with pytest.raises(ValueError, match='preflight'):
        readers.collect(('email', 'imessage'))
    with pytest.raises(ValueError, match='matching'):
        readers.preflight('account', ('email', 'imessage'))


def test_native_access_only_checks_pinned_readers_with_isolated_loopback_services(tmp_path, monkeypatch):
    from pacbench.provider_versions import memcal_source
    checkout = memcal_source(Path(__file__).resolve().parents[1])
    if not checkout.is_dir():
        pytest.skip('Exact reviewed MemCal checkout is unavailable')
    monkeypatch.syspath_prepend(str(checkout))
    binding = site(tmp_path, monkeypatch)
    # Import only after binding HOME. The stock reader freezes CHAT_DB at import.
    from memcal import db

    db_path = Path(binding.memcal_home) / 'memcal.db'
    db.open_db(db_path).close()
    with sqlite3.connect(db_path) as conn:
        before = conn.execute('SELECT COUNT(*) FROM collections').fetchone()
    mailbox = tmp_path / 'mailbox.json'
    mailbox.write_text('{"messages": []}')
    probes = []

    class BlueBubblesPing(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split('?')[0]
            probes.append(path)
            self.send_response(200 if path == '/api/v1/ping' else 404)
            self.end_headers()
            self.wfile.write(b'{"status":200}')

        def log_message(self, *args):
            pass

    with HTTPServer(('127.0.0.1', 0), BlueBubblesPing) as http, MailboxServer(mailbox) as imap:
        worker = threading.Thread(target=http.serve_forever, daemon=True)
        worker.start()
        try:
            binding = replace(binding, imap_port=imap.port, imap_user=imap.user,
                              bluebubbles_url=f'http://127.0.0.1:{http.server_port}')
            env = Path(binding.memcal_home) / '.env'
            settings = ['MEMCAL_IMESSAGE_BACKEND=bluebubbles', 'MEMCAL_IMESSAGE_FALLBACK=0',
                        'BLUEBUBBLES_URL=' + binding.bluebubbles_url, 'BLUEBUBBLES_PASSWORD=fixture',
                        'PROTON_BRIDGE_HOST=127.0.0.1', f'PROTON_BRIDGE_PORT={imap.port}',
                        'PROTON_BRIDGE_USER=' + imap.user, 'PROTON_BRIDGE_PASSWORD=' + imap.password]
            env.write_text('\n'.join(settings))
            with pytest.raises(module.MemCalSiteError, match='account differs'):
                module.inspect_memcal_site(replace(binding, imap_user='another@example.test'))
            assert not probes and not imap.commands
            env.write_text('\n'.join([*settings, 'MEMCAL_IMESSAGE_FALLBACK=1']))
            with pytest.raises(module.MemCalSiteError, match='fallback'):
                module.inspect_memcal_site(binding)
            assert not probes and not imap.commands
            env.write_text('\n'.join(settings))
            report = module.inspect_memcal_site(binding)
            assert report['state'] == 'source-access-verified'
            assert report['imap'] == {'security': 'SSL', 'folders': {
                key: {'mode': 'readonly', 'uidvalidity': '1'} for key in ('Sent', 'INBOX')}}
            assert probes == ['/api/v1/ping']
            assert {r['command'] for r in imap.commands} == {
                'CAPABILITY', 'LOGIN', 'LIST', 'EXAMINE', 'STATUS', 'LOGOUT'}
            assert imap.user not in json.dumps(report) and imap.password not in json.dumps(report)
        finally:
            http.shutdown()
            worker.join()
    with sqlite3.connect(db_path) as conn:
        assert before == conn.execute('SELECT COUNT(*) FROM collections').fetchone()


@pytest.mark.parametrize('fault', ['', 'future', 'bytes', 'arrival', 'uidvalidity'])
def test_both_uid_inventories_precede_content_reads(tmp_path, monkeypatch, fault):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from pacbench.provider_versions import memcal_source
    checkout = memcal_source(Path(__file__).resolve().parents[1])
    if not checkout.is_dir():
        pytest.skip('Exact reviewed MemCal checkout is unavailable')
    monkeypatch.syspath_prepend(str(checkout))
    selected = []
    fetches = []
    expected = {'folder':'Sent', 'uid':3, 'arrived_at':'2026-10-08T19:00:00+00:00', 'rfc822':'expected'}
    def select(folder):
        selected.append(folder)
        return True, '99' if fault == 'uidvalidity' else '1'
    def uid(action, n, spec):
        if action == 'SEARCH':
            return 'OK', [b'3' if selected[-1] == 'Sent' else b'9' if fault == 'future' else b'']
        fetches.append(spec)
        assert set(selected) == {'INBOX', 'Sent'}
        if spec == '(INTERNALDATE)':
            day = b'09' if fault == 'arrival' else b'08'
            return 'OK', [b'1 (INTERNALDATE "' + day + b'-Oct-2026 19:00:00 +0000")']
        return 'OK', [(b'1 (BODY[] {8}', b'other' if fault == 'bytes' else b'expected'), b')']
    mailbox = SimpleNamespace(folders=lambda:['INBOX','Sent'], select=select, conn=SimpleNamespace(uid=uid))
    observed = {'folders': {f:f for f in ('INBOX','Sent')}, 'records':[expected], 'cursor':
                {f:{'uidvalidity':1,'highest_uid':3 if f == 'Sent' else 0} for f in ('INBOX','Sent')}}
    monkeypatch.setattr(module, '_opened_mailbox', lambda m:nullcontext(m))
    if fault:
        with pytest.raises(module.MemCalSiteError):
            module._verify_mail_snapshot(mailbox, observed, bodies=True)
        if fault in ('future','uidvalidity'):
            assert fetches == []
    else:
        module._verify_mail_snapshot(mailbox, observed, bodies=False)
        assert fetches == []
        checked = {}
        module._verify_mail_snapshot(mailbox, observed, bodies=True, checked=checked)
        module._verify_mail_snapshot(mailbox, observed, bodies=True, checked=checked)
        assert fetches == ['(INTERNALDATE)', '(BODY.PEEK[])']
