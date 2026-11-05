"""Exact shared prompt text for portable evidence; no compressed or lost text."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

STORE = 'shared-text.json'
MARKER = '$pacbench_text'


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@lru_cache(maxsize=8)
def _store(path, modified, size):
    value=json.loads(Path(path).read_text())
    if value.get('version')!=1 or not isinstance(value.get('chunks'),dict):
        raise ValueError('Unsupported shared evidence store')
    return value['chunks']


def read_json(path):
    path=Path(path)
    value=json.loads(path.read_text(encoding='utf-8'))
    chunks=None
    def expand(item):
        nonlocal chunks
        if isinstance(item,list):return [expand(v) for v in item]
        if not isinstance(item,dict):return item
        if MARKER not in item:return {k:expand(v) for k,v in item.items()}
        if set(item)!={MARKER,'sha256'} or not isinstance(item[MARKER],list):
            raise ValueError('Malformed shared evidence reference')
        if chunks is None:
            store=next((parent/STORE for parent in path.parents if (parent/STORE).exists()),None)
            if store is None or store.is_symlink():raise ValueError('Missing or unsafe shared evidence store')
            stat=store.stat();chunks=_store(str(store.resolve()),stat.st_mtime_ns,stat.st_size)
        pieces=[]
        for key in item[MARKER]:
            if not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key):
                raise ValueError('Invalid shared evidence key')
            text=chunks.get(key)
            if not isinstance(text,str) or sha(text)!=key:
                raise ValueError('Shared evidence chunk hash mismatch')
            pieces.append(text)
        text=''.join(pieces)
        if sha(text)!=item['sha256']:raise ValueError('Shared evidence text hash mismatch')
        return text
    return expand(value)


def share_text(value,chunks):
    """Deduplicate stable line groups, including histories with different prefixes."""
    if isinstance(value,list):return [share_text(v,chunks) for v in value]
    if isinstance(value,dict):return {k:share_text(v,chunks) for k,v in value.items()}
    if not isinstance(value,str) or len(value)<4096:return value
    pieces=[];buffer=[];size=0
    def emit(text):
        key=sha(text);chunks[key]=text;pieces.append(key)
    for line in value.splitlines(keepends=True):
        if len(line)>8192:
            if buffer:emit(''.join(buffer));buffer=[];size=0
            for start in range(0,len(line),8192):emit(line[start:start+8192])
            continue
        buffer.append(line);size+=len(line)
        if size>=8192 or hashlib.sha256(line.encode()).digest()[0]%16==0:
            emit(''.join(buffer));buffer=[];size=0
    if buffer:emit(''.join(buffer))
    return {MARKER:pieces,'sha256':sha(value)}


def contract_paths(root):
    return [p for p in [root/'contract.json',*(root/'evaluations').glob('*/contract.json')] if p.exists()]


def compact_contracts(root):
    """Keep expanded contract hashes unchanged while sharing exact review text."""
    from .followups import save
    root=Path(root)
    chunks=read_json(root/STORE)['chunks'] if (root/STORE).exists() else {}
    pending={path:share_text(read_json(path),chunks) for path in contract_paths(root)}
    save(root/STORE,{'version':1,'chunks':chunks})
    for path,value in pending.items():save(path,value)


def compact_preparation(root):
    """Keep referenced receipts and store repeated exact prompt blocks once."""
    from .followups import save
    from .preparation_audit import audit_prepared
    root=Path(root)
    if audit_prepared(root)['state']!='pass':raise ValueError('Refuse to compact invalid preparation')
    before=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    scenes=read_json(root/'scenes.json')['scenes']
    keep={Path(p).name for s in scenes for p in s['record'].get('judgment_paths',[])}
    for p in (root/'scene-verifications').glob('*.json'):
        if p.name not in keep:p.unlink()
    models={p.name:p for p in (root/'model-calls').glob('*.json')}
    references=set()
    def collect(value):
        if isinstance(value,dict):
            for v in value.values():collect(v)
        elif isinstance(value,list):
            for v in value:collect(v)
        elif isinstance(value,str):
            references.update(set(re.findall(r'[a-f0-9]{64}(?:-\d+)?\.json',value)) & models.keys())
    for path in root.rglob('*.json'):
        if path.parent==root/'model-calls' or path.name in {'week.json',STORE}:continue
        collect(read_json(path))
    for name,path in models.items():
        if name not in references:path.unlink()
    targets=[*(root/'model-calls').glob('*.json'),*(root/'scene-verifications').glob('*.json'),*contract_paths(root)]
    chunks={};pending={}
    for path in targets:
        value=read_json(path)
        if path.parent.name=='model-calls':
            if 'request' in value:value['request']=share_text(value['request'],chunks)
        elif path.name=='contract.json':value=share_text(value,chunks)
        elif 'judge_prompt' in value:value['judge_prompt']=share_text(value['judge_prompt'],chunks)
        pending[path]=value
    # Union first makes an interrupted update readable with either record form.
    previous=read_json(root/STORE)['chunks'] if (root/STORE).exists() else {}
    save(root/STORE,{'version':1,'chunks':{**previous,**chunks}})
    for path,value in pending.items():save(path,value)
    save(root/STORE,{'version':1,'chunks':chunks})
    checked=audit_prepared(root)
    if checked['state']!='pass':raise ValueError('Compacted preparation failed audit: '+str(checked['errors']))
    after=sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    return {'bytes_before':before,'bytes_after':after,'model_receipts':len(references),
            'shared_chunks':len(chunks),'audit':checked}
