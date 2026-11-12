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
    def __init__(self, path=STATE/'ledger.json'):
        self.path=Path(path)
        with self.locked() as state:
            self.config=state['config']
        if (self.config.get('model')!=MODEL or self.config.get('endpoint')!=ENDPOINT or
                money(self.config.get('cap'))!=Decimal('20')):
            raise RuntimeError('Task accounting configuration differs')

    @contextmanager
    def locked(self):
        # Never create, replace or reset a missing task ledger.
        with self.path.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
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
            if any(c.get('http_status') in (401,403) for c in calls):
                raise RuntimeError('Task authentication blocked')
            own=[c for c in calls if c['project']==project]
            if sum(c.get('http_status') in (400,422) for c in own)>=3:
                raise RuntimeError('Provider repeated request rejection')
            if sum(c.get('finished') is not None and c.get('http_status')==200 and
                   c.get('state')=='uncertain' and
                   (c.get('input_tokens') is None or c.get('output_tokens') is None) for c in own)>=3:
                raise RuntimeError('Three completed provider responses lack usable token usage; project stopped')
            if any(c.get('transport')==VERSION and c['state']=='uncertain' and
                   c.get('http_status') not in (400,422,429) and
                   (not c.get('request_sha256') or c['request_sha256']==fingerprint) for c in own):
                raise RuntimeError('Uncertain request outcome; no automatic replay')
            if any(c.get('request_sha256')==fingerprint and c['state']=='reserved' for c in own):
                raise RuntimeError('Identical request already in flight')
            if sum(money(c['accounted']) for c in calls)+bound>money(self.config['cap']):
                raise RuntimeError('Task spending cap reached')
            row={'id':str(uuid.uuid4()),'project':project,'model':MODEL,'started':now(),'finished':None,
                'reserved':str(bound),'accounted':str(bound),'state':'reserved','transport':VERSION,
                'provider_request':request['provider'],'completion_bound_tokens':maximum,
                'request_sha256':fingerprint,'input_tokens':None,'output_tokens':None,
                'http_status':None,'error_label':None}
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
                usage=payload['usage'];cost=money(usage['cost']);inp=usage['prompt_tokens'];out=usage['completion_tokens']
                if (status==200 and type(inp) is int and type(out) is int and
                        0<=inp<=self.config['context_tokens'] and 0<=out<=row['completion_bound_tokens'] and
                        cost<=money(row['reserved'])):
                    row.update(accounted=str(cost),state='settled',input_tokens=inp,output_tokens=out,error_label=None)
            except (ValueError,KeyError,TypeError,UnicodeError,StopIteration):
                pass


def live_budget():
    budget=DirectBudget()
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
