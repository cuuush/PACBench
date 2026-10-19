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
    args = parser.parse_args()
    upstream_key = os.environ.get('META')
    if not upstream_key:
        parser.error('Managed META binding is required; no credential fallback')
    args.state.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock = (args.state/'server.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    token = args.state/'client-token'
    if not token.exists():
        fd = os.open(token,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as output:
            output.write(secrets.token_urlsafe(40))
    ledger = Ledger(args.state/'ledger.sqlite3')
    ledger.recover_abandoned()
    server = Server(('127.0.0.1',args.port),ledger,token.read_text().strip(),upstream_key)
    address = args.state/'address.json'
    address.write_text(json.dumps({'base':f'http://127.0.0.1:{server.server_port}'})+'\n')
    try:
        server.serve_forever()
    finally:
        server.server_close()
        lock.close()


if __name__ == '__main__':
    main()
