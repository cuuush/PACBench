"""Move original task accounting as private JSON data, never credentials or code."""
from __future__ import annotations
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import uuid

from .direct_accounting import DirectBudget, now
from .openrouter_task import STATE

VERSION='private-accounting-transfer-v1'


def sha(data):return hashlib.sha256(data).hexdigest()


def export_state(destination,thread,*,state_dir=STATE):
    """Freeze source admission, then export all calls and unresolved bounds intact."""
    destination=Path(destination);state_dir=Path(state_dir)
    if destination.exists():raise ValueError('Preserve the existing export')
    if not re.fullmatch(r'[0-9a-f-]{36}',thread):raise ValueError('Exact destination thread ID required')
    authorization=json.loads((state_dir/'authorization.json').read_text())
    if authorization.get('approved_total_usd')!='20':raise ValueError('Original task authorization required')
    budget=DirectBudget(state_dir/'ledger.json')
    with budget.locked() as state:
        if state.get('handoff'):raise ValueError('A transfer already owns this ledger')
        state['handoff']={'version':VERSION,'id':str(uuid.uuid4()),'destination_thread':thread,
                          'frozen':True,'frozen_at':now()}
    files={name:(state_dir/name).read_bytes() for name in ('ledger.json','authorization.json')}
    for p in sorted((state_dir/'reconciliations').rglob('*.json')):
        if p.is_symlink():raise ValueError('Accounting evidence must not be linked')
        files[str(p.relative_to(state_dir))]=p.read_bytes()
    for data in files.values():
        json.loads(data)
        for name in ('OPENROUTER_API_KEY','OPENAI_API_KEY','PACBENCH_DIRECT_UPSTREAM_KEY'):
            secret=os.environ.get(name,'')
            if secret and secret.encode() in data:raise ValueError('Credential found; export refused and source stays frozen')
    state=json.loads(files['ledger.json']);totals=Counter()
    for row in state['calls']:totals[row['state']]+=Decimal(row['accounted'])
    manifest={'version':VERSION,'destination_thread':thread,'handoff_id':state['handoff']['id'],
        'files':{name:{'sha256':sha(data),'bytes':len(data)} for name,data in files.items()},
        'call_count':len(state['calls']),'accounted_by_state':{k:str(v) for k,v in totals.items()},
        'scope':'Original ledger, authorization and financial receipts only. No code, environment files, native stores or credentials. Source admission frozen; unresolved costs remain unresolved.'}
    files['manifest.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    destination.parent.mkdir(parents=True,exist_ok=True)
    descriptor=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'wb') as output,tarfile.open(fileobj=output,mode='w:gz') as archive:
        for name,data in files.items():
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o600
            archive.addfile(item,io.BytesIO(data))
    return {'archive':str(destination),'sha256':sha(destination.read_bytes()),**manifest}


def restore_state(source,expected_sha,thread,*,destination=STATE,activate=False):
    """Restore exact original bytes first; activation changes only ownership metadata."""
    source=Path(source);destination=Path(destination)
    if sha(source.read_bytes())!=expected_sha:raise ValueError('Transfer archive hash differs')
    if destination.exists():raise ValueError('Never overwrite existing task accounting')
    files={}
    with tarfile.open(source,'r:gz') as archive:
        for member in archive:
            path=Path(member.name)
            if (not member.isfile() or path.is_absolute() or '..' in path.parts or
                    path.suffix!='.json' or member.name in files or member.size>16*1024*1024):
                raise ValueError('Unsafe accounting archive member')
            files[member.name]=archive.extractfile(member).read()
    manifest=json.loads(files.pop('manifest.json'))
    if manifest.get('version')!=VERSION or manifest.get('destination_thread')!=thread:
        raise ValueError('Transfer destination identity differs')
    if set(files)!=set(manifest['files']):raise ValueError('Transfer inventory differs')
    for name,data in files.items():
        if sha(data)!=manifest['files'][name]['sha256'] or len(data)!=manifest['files'][name]['bytes']:
            raise ValueError('Transfer file hash differs')
    ledger=json.loads(files['ledger.json']);auth=json.loads(files['authorization.json'])
    handoff=ledger['handoff']
    if (handoff.get('frozen') is not True or handoff.get('id')!=manifest['handoff_id'] or
            handoff.get('destination_thread')!=thread or len(ledger['calls'])!=manifest['call_count'] or
            auth.get('approved_total_usd')!='20' or Decimal(ledger['config']['cap'])!=Decimal('20')):
        raise ValueError('Original frozen accounting identity differs')
    if activate:
        handoff.update(frozen=False,activated_at=now(),active_thread=thread,
                       original_snapshot_sha256=manifest['files']['ledger.json']['sha256'])
        files['ledger.json']=(json.dumps(ledger,separators=(',',':'))).encode()
    destination.mkdir(parents=True,exist_ok=False,mode=0o700)
    for name,data in files.items():
        target=destination/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data)
    return {'state':'restored-active' if activate else 'restored-frozen','calls':len(ledger['calls']),
        'original_ledger_sha256':manifest['files']['ledger.json']['sha256'],
        'accounted_by_state':manifest['accounted_by_state'],'destination_thread':thread}


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    export=sub.add_parser('export');export.add_argument('output',type=Path);export.add_argument('--destination-thread',required=True)
    restore=sub.add_parser('restore');restore.add_argument('archive',type=Path);restore.add_argument('--sha256',required=True)
    restore.add_argument('--destination-thread',required=True);restore.add_argument('--activate',action='store_true')
    args=parser.parse_args()
    result=(export_state(args.output,args.destination_thread) if args.command=='export' else
            restore_state(args.archive,args.sha256,args.destination_thread,activate=args.activate))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
