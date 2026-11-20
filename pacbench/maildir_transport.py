"""Provision an isolated real Maildir IMAP service from frozen RFC 5322 snapshots."""
from __future__ import annotations

import fcntl
import imaplib
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from .email_fixture import OBSERVED_HEADERS, MailboxServer, mail_address

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
