"""Per-request reservations for direct HTTPS; no server, forwarding or network lock.

Native clients keep their inference bodies and streaming behavior. Only routing,
price ceilings and stream usage reporting are added at their transport boundary.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .openrouter_route import MODEL, ENDPOINT, PROVIDER, with_route
from .openrouter_task import STATE, require_authorization

VERSION = 'direct-client-reservations-v1'
MARKER = 'pbdirect-'


def money(value):
    if isinstance(value, bool):
        raise ValueError('Invalid money')
    try:
        result = Decimal(str(value))
    except (ValueError, InvalidOperation):
        raise ValueError('Invalid money') from None
    if not result.is_finite() or result < 0:
        raise ValueError('Invalid money')
    return result


def now():
    # Honcho's isolated wall clock must not date spending in the fictional past.
    if os.environ.get('PACBENCH_DIRECT_CLOCK'):
        epoch, mono = map(float, os.environ['PACBENCH_DIRECT_CLOCK'].split(','))
        stamp = epoch + time.monotonic() - mono
    else:
        stamp = time.time()
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


class DirectBudget:
    def __init__(self, path=STATE/'ledger.json', *, nonblocking=False):
        self.path=Path(path)
        self.nonblocking=nonblocking
        with self.locked() as state:
            self.config=state['config']
        if (self.config.get('model')!=MODEL or self.config.get('endpoint')!=ENDPOINT or
                money(self.config.get('cap'))!=Decimal('20')):
            raise RuntimeError('Task accounting configuration differs')

    @contextmanager
    def locked(self):
        # Never create, replace or reset a missing task ledger.
        with self.path.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX | (fcntl.LOCK_NB if self.nonblocking else 0))
            state=json.load(f)
            if state.get('version')!=1 or not isinstance(state.get('calls'),list):
                raise RuntimeError('Invalid task ledger')
            if hasattr(self,'config') and state['config']!=self.config:
                raise RuntimeError('Task configuration changed')
            yield state
            f.seek(0);json.dump(state,f,separators=(',',':'));f.truncate();f.flush();os.fsync(f.fileno())

    def snapshot(self):
        with self.locked() as state:
            return [{**c,'accounted_upper_bound_usd':float(money(c['accounted']))} for c in state['calls']]

    def authorize_retry(self,identifier,*,authorization):
        """Record an explicit, single-use operator retry without releasing a hold.

        This is never called by SDK retry loops. The original response must have
        ended, and its exact project/request identity remains authoritative.
        """
        if not isinstance(authorization,str) or not authorization.strip():
            raise ValueError('Explicit retry authorization required')
        with self.locked() as state:
            row=next(c for c in state['calls'] if c['id']==identifier)
            if (row.get('transport')!=VERSION or row.get('state')!='uncertain' or
                    row.get('http_status')!=200 or not row.get('finished') or
                    not row.get('request_sha256') or not row.get('upstream_request_id')):
                raise ValueError('Only an ended, identified uncertain response can receive a retry authorization')
            if row.get('authorized_retry'):raise ValueError('Attempt already has a retry authorization')
            grant={'id':str(uuid.uuid4()),'authorized_at':now(),'authorization':authorization,
                   'project':row['project'],'request_sha256':row['request_sha256'],'used_by':None}
            row['authorized_retry']=grant
            return dict(grant)

    def abandon_worker_request(self, identifier, *, worker_pids, evidence_sha256, authorization):
        """Permit one explicit retry after local workers die; preserve liability.

        This records local abandonment only. Remote completion/cost remain
        unknown and the original full reservation continues to count forever
        unless authoritative usage later settles it.
        """
        if (not worker_pids or any(type(p) is not int or p<=1 for p in worker_pids)
                or not re.fullmatch(r'[0-9a-f]{64}',evidence_sha256)
                or not isinstance(authorization,str) or not authorization.strip()):
            raise ValueError('Dead-worker evidence and explicit authorization required')
        for pid in worker_pids:
            try:
                stat=(Path('/proc')/str(pid)/'stat').read_text()
            except FileNotFoundError:
                continue
            # Linux zombie/exited workers cannot still own a local request.
            if stat.rsplit(')',1)[1].split()[0] not in {'Z','X'}:
                raise ValueError('Previous worker is still alive or PID was reused')
        with self.locked() as state:
            row=next(c for c in state['calls'] if c['id']==identifier)
            if (row.get('transport')!=VERSION or row.get('state')!='reserved'
                    or row.get('finished') or not row.get('request_sha256')
                    or row.get('authorized_retry') or money(row['accounted'])!=money(row['reserved'])):
                raise ValueError('Only an unresolved full reservation can be abandoned')
            row.update(state='abandoned_unknown',error_label='local_worker_ended_remote_outcome_unknown',
                abandonment={'at':now(),'worker_pids':worker_pids,'evidence_sha256':evidence_sha256,
                    'remote_outcome':'unknown','original_hold_preserved':True})
            row['authorized_retry']={'id':str(uuid.uuid4()),'authorized_at':now(),
                'authorization':authorization,'project':row['project'],
                'request_sha256':row['request_sha256'],'used_by':None}
            return dict(row)

    def reconcile_generation(self, identifier, generation, model_record, *, receipt_sha256):
        """Settle one identified ended response from authoritative generation data.

        The caller fetches and privately preserves the OpenRouter receipt. Never
        estimate missing costs/tokens or release an unidentified historical hold.
        """
        g=generation
        if (model_record.get('id')!=MODEL or
                g.get('model') not in {MODEL,model_record.get('canonical_slug')} or
                not g.get('model') or g.get('provider_name')!='DeepInfra' or
                g.get('service_tier')!='flex' or g.get('is_byok') is not False or
                not (g.get('cancelled') is True or g.get('finish_reason')) or
                not re.fullmatch(r'[0-9a-f]{64}',receipt_sha256)):
            raise ValueError('Unverified generation identity or terminal state')
        inp=g.get('native_tokens_prompt');out=g.get('native_tokens_completion')
        cost=money(g.get('total_cost'))
        if type(inp) is not int or type(out) is not int or min(inp,out)<0 or cost!=money(g.get('usage')):
            raise ValueError('Incomplete generation usage')
        with self.locked() as state:
            row=next(c for c in state['calls'] if c['id']==identifier)
            if (row.get('state')!='uncertain' or row.get('http_status')!=200 or
                    not row.get('finished') or not row.get('upstream_request_id') or
                    row['upstream_request_id']!=g.get('id')):
                raise ValueError('Generation does not identify an ended uncertain request')
            if cost>money(row['reserved']):
                row.update(accounted=str(cost),reported_liability_exceeds_bound=True,
                    error_label='generation_liability_exceeds_bound',
                    reconciliation={'source':'OpenRouter GET /api/v1/generation',
                        'receipt_sha256':receipt_sha256,'at':now(),'cost_source':'total_cost'})
                return dict(row)  # Admission remains globally stopped.
            if inp>self.config['context_tokens'] or out>row['completion_bound_tokens']:
                raise ValueError('Generation exceeds token bounds; retain hold')
            previous={k:row.get(k) for k in ('state','accounted','error_label','input_tokens','output_tokens')}
            row.update(state='settled',accounted=str(cost),input_tokens=inp,output_tokens=out,error_label=None,
                reconciliation={'source':'OpenRouter GET /api/v1/generation','at':now(),
                    'receipt_sha256':receipt_sha256,'original_accounting':previous,
                    'cancelled':g.get('cancelled'),'canonical_model':g['model'],
                    'cost_source':'total_cost','token_source':'native_tokens_prompt/native_tokens_completion'})
            return dict(row)

    def reserve(self,project,body):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',project):
            raise ValueError('Invalid accounting project')
        if not isinstance(body,dict) or body.get('model')!=MODEL:
            raise ValueError('Exact model required')
        if body.get('n',1) not in (None,1) or body.get('modalities') not in (None,['text']):
            raise ValueError('Unsupported paid feature')
        if any(k in body for k in ('audio','prediction','service_tier','web_search_options','max_output_tokens')):
            raise ValueError('Unsupported paid feature')
        if any(not isinstance(t,dict) or t.get('type')!='function' for t in body.get('tools',[])):
            raise ValueError('Only native function tools supported')
        for msg in body.get('messages',[]):
            content=msg.get('content') if isinstance(msg,dict) else False
            if content is not None and not isinstance(content,str) and (
                    not isinstance(content,list) or any(not isinstance(x,dict) or x.get('type')!='text' or
                        not isinstance(x.get('text'),str) for x in content)):
                raise ValueError('Only text messages supported')
        maximum=body.get('max_completion_tokens',body.get('max_tokens',self.config['max_completion_tokens']))
        if ('max_completion_tokens' in body and 'max_tokens' in body or type(maximum) is not int or
                not 0<maximum<=self.config['max_completion_tokens']):
            raise ValueError('Completion bound invalid')
        request=dict(body)
        if 'provider' in request:
            supplied=request.pop('provider')
            if not isinstance(supplied,dict) or any(k not in PROVIDER or PROVIDER[k]!=v for k,v in supplied.items()):
                raise ValueError('Conflicting provider settings')
        request=with_route(request)
        request['provider']['max_price']={'prompt':float(self.config['prompt_per_million']),
            'completion':float(self.config['completion_per_million']),'request':0}
        if request.get('stream'):
            request['stream_options']={**request.get('stream_options',{}),'include_usage':True}
        fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        bound=(Decimal(self.config['context_tokens'])*money(self.config['prompt_per_million'])+
            Decimal(maximum)*money(self.config['completion_per_million']))/Decimal(1000000)
        with self.locked() as state:
            calls=state['calls']
            admission_stop=os.environ.get('PACBENCH_DIRECT_STOP_ON_429')
            if admission_stop and Path(admission_stop).exists():
                raise RuntimeError('Measured import admission stopped after rate limit or cancellation')
            if state.get('handoff',{}).get('frozen') is True:
                raise RuntimeError('Task ledger frozen for session transfer; source spending is disabled')
            if any(c.get('reported_liability_exceeds_bound') for c in calls):
                raise RuntimeError('Reported task liability exceeded a reserved bound; paid admission stopped')
            if any(c.get('http_status') in (401,403) for c in calls):
                raise RuntimeError('Task authentication blocked')
            own=[c for c in calls if c['project']==project]
            if sum(c.get('http_status') in (400,422) for c in own)>=3:
                raise RuntimeError('Provider repeated request rejection')
            if sum(c.get('finished') is not None and c.get('http_status')==200 and
                   c.get('state')=='uncertain' and
                   (c.get('input_tokens') is None or c.get('output_tokens') is None) for c in own)>=3:
                raise RuntimeError('Three completed provider responses lack usable token usage; project stopped')
            uncertain=[c for c in own if c.get('transport')==VERSION and c['state'] in {'uncertain','abandoned_unknown'} and
                   c.get('http_status') not in (400,422,429) and
                   (not c.get('request_sha256') or c['request_sha256']==fingerprint)]
            retry_of=None
            if uncertain:
                grant=uncertain[0].get('authorized_retry',{}) if len(uncertain)==1 else {}
                if (grant.get('used_by') is None and grant.get('id') and
                        grant.get('request_sha256')==fingerprint and grant.get('project')==project):
                    retry_of=uncertain[0]
                else:
                    raise RuntimeError('Uncertain request outcome; no automatic replay')
            if any(c.get('request_sha256')==fingerprint and c['state']=='reserved' for c in own):
                raise RuntimeError('Identical request already in flight')
            if sum(money(c['accounted']) for c in calls)+bound>money(self.config['cap']):
                raise RuntimeError('Task spending cap reached')
            row={'id':str(uuid.uuid4()),'project':project,'model':MODEL,'started':now(),'finished':None,
                'reserved':str(bound),'accounted':str(bound),'state':'reserved','transport':VERSION,
                'provider_request':request['provider'],'completion_bound_tokens':maximum,
                'request_sha256':fingerprint,'input_tokens':None,'output_tokens':None,
                'http_status':None,'error_label':None,
                'native_request_parameter_keys':sorted(body),
                'native_request_parameters':{k:body[k] for k in ('reasoning','reasoning_effort','max_tokens','max_completion_tokens','temperature','top_p','seed') if k in body},
                'parameter_observation_scope':'Native input before route/price enforcement; absence is not a provider default. completion_bound_tokens is an accounting bound, not an injected output limit.'}
            if retry_of is not None:
                grant=retry_of['authorized_retry'];grant['used_by']=row['id']
                row.update(explicit_retry_of=retry_of['id'],retry_authorization_id=grant['id'])
            calls.append(row)
        return {'id':row['id'],'request':request}

    def settle(self,identifier,status,data=b''):
        # Called after reading/closing a response, never while holding a network lock.
        with self.locked() as state:
            row=next(c for c in state['calls'] if c['id']==identifier)
            if row['state']=='settled':
                return
            row.update(finished=now(),http_status=status or None,state='uncertain',
                       error_label='response_usage_unavailable' if status==200 else 'upstream_request_failed')
            admission_stop=os.environ.get('PACBENCH_DIRECT_STOP_ON_429')
            if status==429 and admission_stop:
                target=Path(admission_stop)
                try:
                    with target.open('x') as receipt:
                        json.dump({'reason':'upstream_rate_limit','request_id':identifier,'at':now()},receipt)
                except FileExistsError:
                    pass
            try:
                if isinstance(data,str):data=data.encode()
                text=data.decode()
                if text.lstrip().startswith('{'):
                    events=[json.loads(text)]
                else:
                    events=[json.loads(line[5:].strip()) for line in text.splitlines()
                            if line.startswith('data:') and line[5:].strip()!='[DONE]']
                for event in events:
                    for k,v in (('id','upstream_request_id'),('model','served_model'),('provider','served_provider')):
                        if isinstance(event.get(k),str):row[v]=event[k][:256]
                    for choice in event.get('choices',[]):
                        if isinstance(choice.get('finish_reason'),str):row['finish_reason']=choice['finish_reason'][:64]
                if any(e.get('model') is not None and e['model']!=MODEL for e in events):
                    row['error_label']='model_mismatch';return
                payload=next(e for e in reversed(events) if isinstance(e.get('usage'),dict))
                usage=payload['usage']
                from .usage import TOKEN_DETAILS
                detail={}
                for name,value in usage.items():
                    if name in TOKEN_DETAILS and type(value) is int and value>=0:detail[name]=value
                    if name in ('prompt_tokens_details','completion_tokens_details','input_tokens_details','output_tokens_details') and isinstance(value,dict):
                        detail.update({name+'.'+k:v for k,v in value.items() if k in TOKEN_DETAILS and type(v) is int and v>=0})
                row['usage_detail']=detail
                cost=money(usage['cost']);inp=usage['prompt_tokens'];out=usage['completion_tokens']
                if (status==200 and type(inp) is int and type(out) is int and
                        0<=inp<=self.config['context_tokens'] and 0<=out<=row['completion_bound_tokens'] and
                        cost<=money(row['reserved'])):
                    row.update(accounted=str(cost),state='settled',input_tokens=inp,output_tokens=out,error_label=None)
            except (ValueError,KeyError,TypeError,UnicodeError,StopIteration):
                pass


def live_budget(*, nonblocking=False):
    budget=DirectBudget(nonblocking=nonblocking)
    require_authorization(budget)
    return budget


def project_from_authorization(header):
    if not isinstance(header,str) or not header.startswith('Bearer '+MARKER):return None
    project=header[len('Bearer '+MARKER):]
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',project):raise ValueError('Invalid direct marker')
    return project


def main():
    # Runtime fetch hooks use stdin/stdout locally, not a network service.
    value=json.load(sys.stdin)
    budget=live_budget()
    if sys.argv[1]=='reserve':
        print(json.dumps(budget.reserve(value['project'],value['request'])))
    elif sys.argv[1]=='settle':
        budget.settle(value['id'],value['status'],value.get('data',''))
        print('{}')
    else:raise ValueError('Unknown accounting operation')


if __name__=='__main__':main()
