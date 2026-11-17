"""Run a single local gateway; private credentials/ledger live outside Git."""
import argparse
import fcntl
import json
import os
import secrets
from pathlib import Path

from .ledger import Ledger
from .server import Server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8099)
    parser.add_argument('--initialize-new-state',action='store_true',help='Create a genuinely new state directory and ledger')
    args = parser.parse_args()
    upstream_key = os.environ.get('META')
    if not upstream_key:
        parser.error('Managed META binding is required; no credential fallback')
    if args.initialize_new_state:
        try:
            args.state.mkdir(mode=0o700)
        except FileExistsError:
            parser.error('New state directory already exists; refusing to initialize')
    elif not args.state.is_dir() or not (args.state/'client-token').is_file() or not (args.state/'ledger.sqlite3').is_file():
        parser.error('Existing state, client token and ledger are required')
    lock = (args.state/'server.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    token = args.state/'client-token'
    if args.initialize_new_state:
        fd = os.open(token,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as output:
            output.write(secrets.token_urlsafe(40))
    try:
        ledger = Ledger(args.state/'ledger.sqlite3', existing_only=not args.initialize_new_state)
    except (OSError, ValueError, __import__('sqlite3').Error):
        parser.error('Existing gateway ledger is unavailable or invalid')
    client_key = token.read_text().strip()
    if not client_key:
        parser.error('Existing gateway client token is empty')
    ledger.recover_abandoned()
    server = Server(('127.0.0.1',args.port),ledger,client_key,upstream_key)
    address = args.state/'address.json'
    address.write_text(json.dumps({'base':f'http://127.0.0.1:{server.server_port}'})+'\n')
    try:
        server.serve_forever()
    finally:
        server.server_close()
        lock.close()


if __name__ == '__main__':
    main()
