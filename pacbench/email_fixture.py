"""Reusable RFC 5322 mailbox and read-only TLS IMAP fixture, without provider APIs.

Synthetic Inbox/Sent mail is served over the same protocol as a local mail bridge.
No expected facts, extraction or archive writes participate in this transport.
"""
from __future__ import annotations

import json
import re
import secrets
import shlex
import socketserver
import ssl
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, formataddr
from pathlib import Path

from .imessage_fixture import identity


def mail_address(name):
    return identity(name)['uid'].lower() + '@mail.example.test'


def append_emails(path, messages):
    """Persist stable UIDs, Message-IDs and reply headers across daily snapshots."""
    path = Path(path)
    mailbox = json.loads(path.read_text()) if path.exists() else {'version': 1, 'messages': []}
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
                or not subject or not isinstance(source['from_owner'], bool)):
            raise ValueError('Email needs declared recipients, subject and ownership')
        earlier = [row for row in mailbox['messages'] if row['source']['thread'] == source['thread']]
        own_id = '<' + str(uuid.uuid5(uuid.NAMESPACE_URL, source['source_id'])) + '@mail.example.test>'
        msg = EmailMessage(policy=SMTP)
        msg['From'] = formataddr((source['sender'], mail_address(source['sender'])))
        msg['To'] = ', '.join(formataddr((p, mail_address(p))) for p in roster if p != source['sender'])
        msg['Date'] = format_datetime(datetime.fromisoformat(source['written_at']))
        msg['Message-ID'] = own_id
        msg['Subject'] = ('Re: ' if earlier else '') + subject
        if earlier:
            msg['In-Reply-To'] = earlier[-1]['message_id']
            msg['References'] = ' '.join(row['message_id'] for row in earlier)
        msg.set_content(source['text'])
        folder = 'Sent' if source['from_owner'] else 'INBOX'
        row = {'source': source, 'folder': folder,
               'uid': 1 + sum(r['folder'] == folder for r in mailbox['messages']),
               'message_id': own_id, 'rfc822': msg.as_bytes().decode('utf-8')}
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
                    criteria = ' '.join(args[1:])
                    match = re.search(r'UID (\d+):\*', criteria, re.I)
                    if match:
                        rows = [r for r in rows if r['uid'] >= int(match[1])]
                    since = re.search(r'SINCE (\d{2}-[A-Za-z]{3}-\d{4})', criteria, re.I)
                    if since:
                        floor = datetime.strptime(since[1], '%d-%b-%Y').date()
                        rows = [r for r in rows if datetime.fromisoformat(r['source']['written_at']).date() >= floor]
                    ids = ' '.join(str(r['uid']) for r in rows)
                    self.wfile.write(f'* SEARCH {ids}\r\n'.encode())
                elif subcommand == 'FETCH':
                    row = next((r for r in rows if r['uid'] == int(args[1])), None)
                    if row:
                        data = row['rfc822'].encode('utf-8')
                        header_only = 'HEADER.FIELDS' in ' '.join(args[2:]).upper()
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
