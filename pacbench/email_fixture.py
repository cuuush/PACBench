"""Reusable RFC 5322 mailbox and read-only TLS IMAP fixture, without provider APIs.

Synthetic Inbox/Sent mail is served over the same protocol as a local mail bridge.
No expected facts, extraction or archive writes participate in this transport.
"""
from __future__ import annotations

import fcntl
import imaplib
import json
import os
import re
import secrets
import shlex
import socket
import socketserver
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, formataddr
from pathlib import Path
from types import SimpleNamespace

from .imessage_fixture import identity


def mail_address(name):
    return identity(name)['uid'].lower() + '@mail.example.test'


OBSERVED_HEADERS = 'observed-email-headers-v2'


def append_emails(path, messages, *, observed_headers_only=False):
    """Persist stable UIDs, Message-IDs and reply headers across daily snapshots."""
    path = Path(path)
    mailbox = json.loads(path.read_text()) if path.exists() else {'version': 1, 'messages': []}
    if path.exists() and (mailbox.get('header_condition') == OBSERVED_HEADERS) != observed_headers_only:
        raise ValueError('Email header condition differs from existing snapshot')
    if observed_headers_only:
        mailbox['header_condition'] = OBSERVED_HEADERS
    known = {row['source']['source_id']: row for row in mailbox['messages']}
    for source in sorted(messages, key=lambda m: (m['written_at'], m['source_id'])):
        if source['channel'] != 'email':
            raise ValueError('Email fixture requires email source messages')
        if source['source_id'] in known:
            if known[source['source_id']]['source'] != source:
                raise ValueError('Conflicting email replay')
            continue
        metadata = source.get('structured') or {}
        roster = metadata.get('participants', [])
        subject = metadata.get('email_subject')
        if ('Me' not in roster or source['sender'] not in roster or len(roster) < 2
                or len(set(roster)) != len(roster) or not subject
                or not isinstance(source['from_owner'], bool)
                or source['from_owner'] != (source['sender']=='Me')):
            raise ValueError('Email needs declared recipients, subject and ownership')
        earlier = [row for row in mailbox['messages'] if row['source']['thread'] == source['thread']]
        own_id = '<' + str(uuid.uuid5(uuid.NAMESPACE_URL, source['source_id'])) + '@mail.example.test>'
        headers = metadata.get('email_headers', {}) if observed_headers_only else {}
        if (not isinstance(headers,dict) or set(headers) - {'To','Cc','Message-ID','In-Reply-To','References'}
                or any(not isinstance(v,str) or '\r' in v or '\n' in v for v in headers.values())):
            raise ValueError('Invalid observed email headers')
        own_id = headers.get('Message-ID', own_id)
        if any(row['message_id'] == own_id for row in mailbox['messages']):
            raise ValueError('Duplicate source Message-ID')
        msg = EmailMessage(policy=SMTP)
        msg['From'] = formataddr((source['sender'], mail_address(source['sender'])))
        if not observed_headers_only:
            msg['To'] = ', '.join(formataddr((p, mail_address(p))) for p in roster if p != source['sender'])
        msg['Date'] = format_datetime(datetime.fromisoformat(source['written_at']))
        msg['Message-ID'] = own_id
        msg['Subject'] = subject if observed_headers_only else ('Re: ' if earlier else '') + subject
        if observed_headers_only:
            for name, value in headers.items():
                if name != 'Message-ID':
                    msg[name] = value
        elif earlier:
            msg['In-Reply-To'] = earlier[-1]['message_id']
            msg['References'] = ' '.join(row['message_id'] for row in earlier)
        msg.set_content(source['text'])
        folder = 'Sent' if source['from_owner'] else 'INBOX'
        row = {'source': source, 'folder': folder,
               'uid': 1 + sum(r['folder'] == folder for r in mailbox['messages']),
               'message_id': own_id, 'rfc822': msg.as_bytes().decode('utf-8')}
        if observed_headers_only:
            row['transport_assigned_headers'] = ['From'] + ([] if 'Message-ID' in headers else ['Message-ID'])
        mailbox['messages'].append(row)
        known[source['source_id']] = row
    path.write_text(json.dumps(mailbox, indent=2, ensure_ascii=False) + '\n')
    return {'format': 'RFC5322 over read-only TLS IMAP', 'message_count': len(mailbox['messages']),
            'limitations': ['Synthetic mailbox; not a running Proton Bridge',
                            'Text-only messages; no attachments, delivery or read receipts']}


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b'* OK [CAPABILITY IMAP4rev1] Synthetic mailbox ready\r\n')
        authenticated = False
        folder = None
        for raw in self.rfile:
            parts = shlex.split(raw.decode('utf-8').strip())
            if len(parts) < 2:
                break
            tag, command, *args = parts
            command = command.upper()
            status = 'OK'
            if command == 'CAPABILITY':
                self.wfile.write(b'* CAPABILITY IMAP4rev1\r\n')
            elif command == 'LOGIN':
                authenticated = args == [self.server.user, self.server.password]
                status = 'OK' if authenticated else 'NO'
            elif command == 'LOGOUT':
                self.wfile.write(b'* BYE Closing mailbox\r\n')
            elif not authenticated:
                status = 'NO'
            elif command == 'LIST':
                self.wfile.write(b'* LIST () "/" "Sent"\r\n* LIST () "/" "INBOX"\r\n')
            elif command in ('SELECT', 'EXAMINE'):
                folder = args[0]
                if folder not in ('Sent', 'INBOX'):
                    status = 'NO'
                else:
                    count = sum(r['folder'] == folder for r in self.server.messages)
                    self.wfile.write(f'* {count} EXISTS\r\n* OK [UIDVALIDITY 1]\r\n'.encode())
            elif command == 'STATUS':
                self.wfile.write(f'* STATUS "{args[0]}" (UIDVALIDITY 1)\r\n'.encode())
            elif command == 'UID' and folder:
                subcommand = args[0].upper()
                rows = [r for r in self.server.messages if r['folder'] == folder]
                if subcommand == 'SEARCH':
                    criteria = ' '.join(args[1:]).strip('()')
                    match = re.fullmatch(r'UID (\d+):\*(?: SINCE \d{2}-[A-Za-z]{3}-\d{4})?', criteria, re.I)
                    by_id = re.fullmatch(r'HEADER MESSAGE-ID (<[^<>]+>)', criteria, re.I)
                    if not match and not by_id and criteria.upper()!='ALL':
                        self.wfile.write(f'{tag} BAD Unsupported search criteria\r\n'.encode())
                        self.server.commands.append({'command':command,'operation':'SEARCH','folder':folder,'status':'BAD'})
                        continue
                    if match:
                        rows = [r for r in rows if r['uid'] >= int(match[1])]
                    if by_id:
                        rows = [r for r in rows if r['message_id'] == by_id[1]]
                    since = re.search(r'SINCE (\d{2}-[A-Za-z]{3}-\d{4})', criteria, re.I)
                    if since:
                        floor = datetime.strptime(since[1], '%d-%b-%Y').date()
                        rows = [r for r in rows if datetime.fromisoformat(r['source']['written_at']).date() >= floor]
                    ids = ' '.join(str(r['uid']) for r in rows)
                    self.wfile.write(f'* SEARCH {ids}\r\n'.encode())
                elif subcommand == 'FETCH':
                    row = next((r for r in rows if r['uid'] == int(args[1])), None)
                    if row:
                        fields = ' '.join(args[2:]).upper()
                        if fields == '(INTERNALDATE)':
                            source = row['source']
                            stamp = datetime.fromisoformat(source.get('arrived_at') or source['written_at'])
                            value = stamp.strftime('%d-%b-%Y %H:%M:%S %z')
                            self.wfile.write(f'* {row["uid"]} FETCH (UID {row["uid"]} INTERNALDATE "{value}")\r\n'.encode())
                        else:
                            data = row['rfc822'].encode('utf-8')
                            header_only = 'HEADER.FIELDS' in fields
                            if header_only:
                                data = data.split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
                            body = 'BODY[HEADER]' if header_only else 'BODY[]'
                            self.wfile.write(f'* {row["uid"]} FETCH (UID {row["uid"]} {body} {{{len(data)}}}\r\n'.encode())
                            self.wfile.write(data + b')\r\n')
                else:
                    status = 'BAD'
            else:
                status = 'BAD'
            self.server.commands.append({'command': command, 'operation': args[0] if command == 'UID' else None,
                                         'folder': folder, 'status': status})
            self.wfile.write(f'{tag} {status} {command} completed\r\n'.encode())
            if command == 'LOGOUT':
                break


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    def get_request(self):
        sock, address = super().get_request()
        try:
            # IMAP literals and their tagged completion are separate small TLS writes.
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return self.context.wrap_socket(sock, server_side=True), address
        except Exception:
            sock.close()
            raise


class MailboxServer:
    """Ephemeral localhost SSL IMAP endpoint; credentials never enter public logs."""
    def __init__(self, path):
        self.messages = json.loads(Path(path).read_text())['messages']
        self.commands = []
        self.user = mail_address('Me')
        self.password = secrets.token_urlsafe(24)

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pacbench-imap-')
        root = Path(self.tmp.name)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(root/'key.pem'), '-out', str(root/'cert.pem'),
                        '-days', '1', '-subj', '/CN=localhost'],
                       check=True, capture_output=True)
        self.server = _Server(('127.0.0.1', 0), _Handler)
        self.server.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.server.context.load_cert_chain(root/'cert.pem', root/'key.pem')
        self.server.messages = self.messages
        self.server.commands = self.commands
        self.server.user, self.server.password = self.user, self.password
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()


PYMAP_VERSION = '0.36.7'


def _ok(result):
    status, data = result
    if status != 'OK':
        raise RuntimeError(f'Maildir IMAP request failed ({status})')
    return data


def populate(client, messages, *, verify_only=False):
    """Require an exact historical prefix, then APPEND and fetch back this snapshot."""
    imported = 0
    folders = {}
    cursors = {}
    for folder in ('INBOX', 'Sent'):
        expected = [m for m in messages if m['folder'] == folder]
        if not any(_ok(client.list('""', folder))):
            if verify_only:
                raise ValueError('Audited mailbox folder is missing')
            _ok(client.create(folder))
        _ok(client.select(folder, readonly=True))
        existing = _ok(client.uid('SEARCH', None, 'ALL'))[0].split()
        if len(existing) > len(expected):
            raise ValueError('IMAP mailbox already contains later or unrelated mail')
        if verify_only and len(existing) != len(expected):
            raise ValueError('Audited mailbox does not contain the complete snapshot')
        for i, uid in enumerate(existing):
            _verify_message(client, uid, expected[i])
        for row in expected[len(existing):]:
            stamp = datetime.fromisoformat(row['source'].get('arrived_at') or row['source']['written_at'])
            _ok(client.append(folder, None, stamp, row['rfc822'].encode()))
            imported += 1
        _ok(client.select(folder, readonly=True))
        uids = _ok(client.uid('SEARCH', None, 'ALL'))[0].split()
        if len(uids) != len(expected):
            raise ValueError('IMAP mailbox snapshot count differs')
        for uid, row in zip(uids[len(existing):], expected[len(existing):]):
            _verify_message(client, uid, row)
        folders[folder] = len(uids)
        validity = client.response('UIDVALIDITY')[1]
        if not validity or not validity[0] or not validity[0].isdigit():
            raise ValueError('Maildir server did not expose folder UIDVALIDITY')
        cursors[folder] = {'uidvalidity':int(validity[0]), 'highest_uid':int(uids[-1]) if uids else 0}
    return {'appended': imported, 'verified_by_folder': folders, 'server_cursors':cursors, 'readback_comparison': 'RFC5322 header folding and CRLF/LF equivalence'}


def _verify_message(client, uid, expected):
    date = _ok(client.uid('FETCH', uid, '(INTERNALDATE)'))
    match = re.search(rb'INTERNALDATE "([^"]+)"', date[0]) if date and isinstance(date[0],bytes) else None
    source_at = datetime.fromisoformat(expected['source'].get('arrived_at') or expected['source']['written_at'])
    if not match or datetime.strptime(match[1].decode(), '%d-%b-%Y %H:%M:%S %z') != source_at:
        raise ValueError('IMAP internal delivery date differs from source arrival')
    data = _ok(client.uid('FETCH', uid, '(BODY.PEEK[])'))
    if not data or not isinstance(data[0], tuple):
        raise ValueError('IMAP message fetch did not return its bytes')
    def canonical(raw):
        headers, separator, body = raw.replace(b'\r\n', b'\n').partition(b'\n\n')
        if not separator:
            raise ValueError('Invalid RFC 5322 message')
        # The on-disk Maildir library refolds headers when serializing them.
        return re.sub(rb'\n[ \t]+', b' ', headers), body
    if canonical(data[0][1]) != canonical(expected['rfc822'].encode()):
        raise ValueError('Stored IMAP headers or body differ from frozen mail')


@contextmanager
def maildir_server(snapshot, state, python, *, verify_only=False):
    """Keep mail and UIDs across nights. The server only binds loopback."""
    state = Path(state).absolute()
    marker = state/'.pacbench-maildir-owner'
    if state != state.resolve():
        raise ValueError('Synthetic mailbox storage must be a run-owned directory')
    if verify_only and not state.is_dir():
        raise ValueError('Audited mailbox is missing')
    try:
        state.mkdir(mode=0o700)
    except FileExistsError:
        if not marker.is_file() or marker.is_symlink() or marker.read_text() != 'PACBench synthetic Maildir v1\n':
            raise ValueError('Refusing to use an existing unowned mailbox')
    else:
        marker.write_text('PACBench synthetic Maildir v1\n')
    with marker.open('r') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any(p.is_symlink() for p in state.rglob('*')):
            raise ValueError('Unexpected symlink in private Maildir state')
        with _start_server(snapshot, state, python, verify_only=verify_only) as server:
            yield server


@contextmanager
def _start_server(snapshot, state, python, *, verify_only):
    messages = json.loads(Path(snapshot).read_text())['messages']
    user = mail_address('Me')
    password = secrets.token_urlsafe(32)
    # pymap natively supports passwd/shadow-format account files under its
    # Maildir root. Generate the salted hash with its own installed library.
    with tempfile.TemporaryDirectory(prefix='imap-', dir=state) as tmp:
        work = Path(tmp)
        env = {'HOME': tmp, 'PATH': os.defpath, 'TMPDIR': tmp, 'LANG': 'C.UTF-8'}
        version = subprocess.check_output([str(python), '-c',
            'from importlib.metadata import version; print(version("pymap"))'], env=env, text=True).strip()
        if version != PYMAP_VERSION:
            raise ValueError(f'Expected pymap {PYMAP_VERSION}, found {version}')
        hashed = subprocess.check_output([str(python), '-c',
            'import sys; from pysasl.hashing import BuiltinHash; print(BuiltinHash().hash(sys.stdin.read()))'],
            input=password, text=True, env=env).strip()
        (state/'pymap-etc-passwd').write_text(f'{user}:x::::mail:\n')
        (state/'pymap-etc-shadow').write_text(f'{user}:{hashed}:::::::\n')
        cert, key = work/'cert.pem', work/'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1',
                        '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True, env=env)
        context = ssl.create_default_context(cafile=str(cert))
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        process = subprocess.Popen([str(python), '-c', 'from pymap.main import main; main()', '--host', '127.0.0.1',
                                    '--port', str(port), '--cert', str(cert), '--key', str(key),
                                    'maildir', str(state.resolve())], cwd=work, env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while process.poll() is None:
                try:
                    client = imaplib.IMAP4('127.0.0.1', port, timeout=3)
                    break
                except ConnectionRefusedError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Maildir IMAP did not start')
                    time.sleep(.05)
            else:
                raise RuntimeError(f'Maildir IMAP server exited ({process.returncode})')
            with client:
                _ok(client.starttls(context))
                _ok(client.login(user, password))
                receipt = populate(client, messages, verify_only=verify_only)
            # CPython's public audit hook observes only native-client command
            # names, never credentials, source literals, or command arguments.
            commands = []
            active = True
            def observe(event, args):
                if active and event == 'imaplib.send':
                    connection, data = args
                    if connection.host == '127.0.0.1' and connection.port == port:
                        if re.fullmatch(rb'[A-Z]+\d+ STARTTLS\r\n', data):
                            commands.append({'command':'STARTTLS', 'operation':'STARTTLS'})
                        match = re.fullmatch(rb'[A-Z]+\d+ UID (FETCH|SEARCH) [^\r\n]+\r\n', data)
                        if match:
                            commands.append({'command':'UID', 'operation':match[1].decode()})
            sys.addaudithook(observe)
            try:
                yield SimpleNamespace(user=user, password=password, port=port, commands=commands,
                                      deployment={'server':'pymap', 'version':version,
                                                  'security':'STARTTLS', **receipt})
            finally:
                active = False
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def email_server(snapshot, root, *, verify_only=False):
    python = os.environ.get('PACBENCH_IMAP_SERVER_PYTHON')
    if python:
        return maildir_server(snapshot, Path(root)/'synthetic-maildir', python, verify_only=verify_only)
    if json.loads(Path(snapshot).read_text()).get('header_condition') == OBSERVED_HEADERS:
        raise ValueError('V2 email collection requires the isolated IMAP server runtime')
    return MailboxServer(snapshot)
