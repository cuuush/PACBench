"""Reconcile an identified uncertain request using free OpenRouter read APIs."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from .direct_accounting import live_budget, now, observed_generation_id
from .openrouter_route import MODEL
from .openrouter_task import STATE


def reconcile(identifier):
    budget=live_budget()
    row=next(c for c in budget.snapshot() if c['id']==identifier)
    if row.get('state')!='uncertain':
        raise ValueError('An identified uncertain request is required')
    generation_id,_=observed_generation_id(row)
    request=Request('https://openrouter.ai/api/v1/generation?'+urlencode({'id':generation_id}),
        headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY']})
    with urlopen(request,timeout=30) as response:generation=json.load(response)['data']
    with urlopen('https://openrouter.ai/api/v1/models',timeout=30) as response:
        model=next(m for m in json.load(response)['data'] if m['id']==MODEL)
    receipt=json.dumps({'fetched_at':now(),'generation':generation,'model_binding':model},sort_keys=True).encode()
    digest=hashlib.sha256(receipt).hexdigest()
    folder=STATE/'reconciliations';folder.mkdir(exist_ok=True)
    path=folder/(digest+'.json')
    with path.open('xb') as stream:
        os.chmod(path,0o600);stream.write(receipt+b'\n')
    # Hash exact bytes stored privately, including their terminating newline.
    return budget.reconcile_generation(identifier,generation,model,
        receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('request_id',help='Original ledger request ID, not a generation ID')
    args=parser.parse_args();row=reconcile(args.request_id)
    print(json.dumps({k:row[k] for k in ('id','state','accounted','input_tokens','output_tokens')}))


if __name__=='__main__':main()
