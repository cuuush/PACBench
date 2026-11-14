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

from .openrouter_route import MODEL, ENDPOINT, PROVIDER, PROFILE, PROFILE_NAME, with_route, PROFILES
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


def stop_measured_admission(reason, identifier=None):
    """Notify the run supervisor without changing reservations or native params."""
    value=os.environ.get('PACBENCH_DIRECT_STOP_ON_429') or os.environ.get('PACBENCH_STOP_FILE')
    if not value:return
    try:
        with Path(value).open('x') as receipt:
            json.dump({'reason':reason,'request_id':identifier,'at':now()},receipt)
    except FileExistsError:
        pass


def request_cost_bound(config, maximum, *, shared_context=False):
    context=Decimal(config['context_tokens'])
    prompt=money(config['prompt_per_million']);completion=money(config['completion_per_million'])
    if shared_context:
        # Official endpoint contract: input and output share the context window.
        # Maximize I*p+O*c subject to I+O<=context and O<=maximum.
        numerator=context*prompt+min(Decimal(maximum),context)*max(Decimal(0),completion-prompt)
    else:numerator=context*prompt+Decimal(maximum)*completion
    return numerator/Decimal(1000000)


def observed_generation_id(row):
    """Use an actual response ID, never manufacture one from timestamps."""
    body=row.get('upstream_request_id')
    header=row.get('response_diagnostic_headers',{}).get('x-generation-id')
    if body and header and body!=header:
        raise ValueError('Response generation identifiers disagree')
    if body:return body,'response-body:id'
    if isinstance(header,str) and re.fullmatch(r'gen-[A-Za-z0-9_-]+',header):
        return header,'response-header:x-generation-id'
    raise ValueError('No observed generation identifier')


def response_validation(events, body_complete):
    """Semantic envelope evidence without storing assistant or reasoning text."""
    pieces=[];reasons=[];errors=[];tools=0
    for event in events:
        error=event.get('error')
        if error is not None:
            errors.append({'code':error.get('code'),'message':str(error.get('message',''))[:512]}
                if isinstance(error,dict) else {'message':str(error)[:512]})
        for choice in event.get('choices') or []:
            if choice.get('finish_reason') is not None:reasons.append(choice['finish_reason'])
            message=choice.get('message') or choice.get('delta') or {}
            content=message.get('content')
            if isinstance(content,str):pieces.append(content)
            tools+=len(message.get('tool_calls') or [])
    content=''.join(pieces)
    return {'body_complete':bool(body_complete),'events_observed':len(events),
        'envelope_errors':errors,'assistant_text_characters':len(content),
        'assistant_nonwhitespace_characters':len(content.strip()),
        'assistant_text_sha256':hashlib.sha256(content.encode()).hexdigest(),
        'tool_call_fragments':tools,'finish_reasons':reasons,
        'scope':'Content lengths and fingerprint only; reasoning text is not captured.'}


def preserve_unusable_response(ledger, identifier, data, complete):
    folder=Path(ledger).parent/'response-errors';folder.mkdir(mode=0o700,exist_ok=True)
    path=folder/(identifier+'.response-body.bin')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    except FileExistsError:
        if path.read_bytes()!=data:raise ValueError('Error evidence already differs')
    return {'relative_path':str(path.relative_to(Path(ledger).parent)),
            'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'body_complete':bool(complete),
            'scope':'Exact private failed or unusable response body; not proof of zero cost or permission to replay.'}


class DirectBudget:
    def __init__(self, path=STATE/'ledger.json', *, nonblocking=False, profile=None):
        self.profile_name=profile or PROFILE_NAME
        self.profile=PROFILES[self.profile_name]
        self.model=self.profile['model'];self.endpoint=self.profile['endpoint']
        self.provider={'only':[self.endpoint],'order':[self.endpoint],'allow_fallbacks':False,'require_parameters':True}
        self.path=Path(path)
        self.nonblocking=nonblocking
        with self.locked() as state:
            self.config=self.selected_config(state)
        if (self.config.get('model')!=self.model or self.config.get('endpoint')!=self.endpoint or
                money(self.config.get('cap'))!=Decimal('20')):
            raise RuntimeError('Task accounting configuration differs')

    def selected_config(self,state):
        if self.profile_name=='deepseek-flex':return state['config']
        config=state.get('route_configs',{}).get(self.profile_name)
        if not config or money(state['config']['cap'])!=Decimal('20'):
            raise RuntimeError('Explicit route accounting configuration is unavailable')
        return config

    def check_row_profile(self,row):
        if (row.get('route_profile',self.profile_name)!=self.profile_name or
                row.get('model',self.model)!=self.model or row.get('endpoint',self.endpoint)!=self.endpoint):
            raise ValueError('Accounting row/profile conflict')

    @contextmanager
    def locked(self):
        # Never create, replace or reset a missing task ledger.
        with self.path.open('r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX | (fcntl.LOCK_NB if self.nonblocking else 0))
            state=json.load(f)
            if state.get('version')!=1 or not isinstance(state.get('calls'),list):
                raise RuntimeError('Invalid task ledger')
            if hasattr(self,'config') and self.selected_config(state)!=self.config:
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
            self.check_row_profile(row)
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
            self.check_row_profile(row)
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
        if (model_record.get('id')!=self.model or
                g.get('model') not in {self.model,model_record.get('canonical_slug')} or
                not g.get('model') or g.get('provider_name')!=self.profile['provider_name'] or
                g.get('service_tier')!=self.profile['service_tier'] or g.get('is_byok') is not False or
                not (g.get('cancelled') is True or g.get('finish_reason')) or
                not re.fullmatch(r'[0-9a-f]{64}',receipt_sha256)):
            raise ValueError('Unverified generation identity or terminal state')
        inp=g.get('native_tokens_prompt');out=g.get('native_tokens_completion')
        cost=money(g.get('total_cost'))
        if type(inp) is not int or type(out) is not int or min(inp,out)<0 or cost!=money(g.get('usage')):
            raise ValueError('Incomplete generation usage')
        with self.locked() as state:
            row=next(c for c in state['calls'] if c['id']==identifier)
            self.check_row_profile(row)
            observed_id,id_source=observed_generation_id(row)
            if (row.get('state')!='uncertain' or row.get('http_status')!=200 or
                    not row.get('finished') or observed_id!=g.get('id')):
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
                upstream_request_id=observed_id,
                reconciliation={'source':'OpenRouter GET /api/v1/generation','at':now(),
                    'receipt_sha256':receipt_sha256,'original_accounting':previous,
                    'generation_identifier_source':id_source,
                    'cancelled':g.get('cancelled'),'canonical_model':g['model'],
                    'cost_source':'total_cost','token_source':'native_tokens_prompt/native_tokens_completion'})
            return dict(row)

    def release_routing_rejection(self,identifier,*,evidence_sha256):
        """Release only an authenticated complete OpenRouter pre-dispatch rejection.

        This is not a provider-reported zero charge. Original holds and the full
        release evidence remain in the row and append-only release event list.
        """
        with self.locked() as state:
            row=next(r for r in state['calls'] if r['id']==identifier)
            self.check_row_profile(row)
            if row['state']=='rejected_no_dispatch':
                if row['no_dispatch_release']['evidence_sha256']!=evidence_sha256:raise ValueError('Release evidence changed')
                return dict(row['no_dispatch_release'])
            proof=row.get('private_error_body',{})
            if (row['state']!='uncertain' or row.get('http_status')!=404 or not row.get('finished') or
                not proof.get('body_complete') or proof.get('sha256')!=evidence_sha256 or
                row.get('upstream_request_id') or row.get('authorized_retry') or
                not row.get('request_sha256') or row.get('transport')!=VERSION):
                raise ValueError('No uniquely bound terminal routing rejection')
            path=(self.path.parent/proof['relative_path']).resolve()
            if not path.is_relative_to(self.path.parent.resolve()):raise ValueError('Evidence path escaped task state')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=evidence_sha256:raise ValueError('Routing evidence hash differs')
            envelope=json.loads(raw);error=envelope.get('error',{});metadata=error.get('metadata',{})
            if (envelope.get('id') or envelope.get('usage') or error.get('code')!=404 or
                error.get('message')!='No endpoints found that can handle the requested parameters. To learn more about provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection' or
                metadata.get('failed_routing_step')!='Filter by Parameters' or
                not metadata.get('routing_funnel') or metadata.get('provider_name') or metadata.get('raw')):
                raise ValueError('Rejection is not proven pre-dispatch')
            before=sum(money(r['accounted']) for r in state['calls']);held=money(row['accounted'])
            event={'id':str(uuid.uuid4()),'reservation_id':identifier,'at':now(),
                'reason':'OpenRouter parameter filter rejected before model dispatch',
                'evidence_sha256':evidence_sha256,'released_bound_usd':str(held),
                'original_state':row['state'],'original_accounted_usd':row['accounted'],
                'exposure_before_usd':str(before),'exposure_after_usd':str(before-held),
                'provider_reported_cost_usd':None,'classification':'rejected_no_dispatch'}
            row.update(state='rejected_no_dispatch',accounted='0',no_dispatch_release=event)
            state.setdefault('reservation_releases',[]).append(event)
            return dict(event)

    def reserve(self,project,body):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,63}',project):
            raise ValueError('Invalid accounting project')
        from .role_routes import split_enabled, profile_for_project
        if split_enabled() and profile_for_project(project)!=self.profile_name:
            raise ValueError('Accounting capability/profile conflict')
        if self.config.get('request_scope')=='import-and-connectivity-probes' and not (project.endswith('-memory') or project.startswith('pacbench-route-probe-')):
            raise RuntimeError('Authorized route is import-only; benchmark and judge calls remain paused')
        if self.config.get('request_scope')=='scored-week-roles' and not (project.endswith('-memory') or project.endswith('-driver') or project=='pacbench-judge'):
            raise RuntimeError('Authorized scored route requires a declared role')
        if not isinstance(body,dict) or body.get('model')!=self.model:
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
        native_body=body
        compatibility=os.environ.get('PACBENCH_LUNA_COMPATIBILITY')
        omitted=[]
        if compatibility:
            if compatibility!='temperature-omitted-default-v1' or self.profile_name!='luna-bedrock-global':
                raise ValueError('Unknown explicit Luna compatibility condition')
            if 'temperature' in body:
                omitted=['temperature'];body={k:v for k,v in body.items() if k!='temperature'}
        limit=os.environ.get('PACBENCH_EFFECTIVE_OUTPUT_TOKENS')
        if limit:
            if self.profile_name!='luna-bedrock-global' or not limit.isdigit() or not 1<=int(limit)<=self.config['max_completion_tokens']:
                raise ValueError('Invalid explicit output ceiling')
            if 'max_tokens' in body and 'max_completion_tokens' in body:raise ValueError('Conflicting native output caps')
            key='max_tokens' if 'max_tokens' in body else 'max_completion_tokens'
            native=body.get(key,self.config['max_completion_tokens'])
            if type(native) is not int or native<=0:raise ValueError('Invalid native output cap')
            body={**body,key:min(native,int(limit))}
        maximum=body.get('max_completion_tokens',body.get('max_tokens',self.config['max_completion_tokens']))
        if ('max_completion_tokens' in body and 'max_tokens' in body or type(maximum) is not int or
                not 0<maximum<=self.config['max_completion_tokens']):
            raise ValueError('Completion bound invalid')
        request=dict(body)
        if 'provider' in request:
            supplied=request.pop('provider')
            if not isinstance(supplied,dict) or any(k not in self.provider or self.provider[k]!=v for k,v in supplied.items()):
                raise ValueError('Conflicting provider settings')
        request=with_route(request,profile=self.profile_name)
        request['provider']['max_price']={'prompt':float(self.config['prompt_per_million']),
            'completion':float(self.config['completion_per_million']),'request':0}
        if request.get('stream'):
            request['stream_options']={**request.get('stream_options',{}),'include_usage':True}
        fingerprint=hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        policy=os.environ.get('PACBENCH_RESERVATION_POLICY',
            'shared-context-v1' if split_enabled() and self.profile_name=='luna-bedrock-global' else 'independent-input-output-v1')
        if policy not in ('independent-input-output-v1','shared-context-v1','openai-cache-total-shared-v1'):
            raise ValueError('Unknown reservation policy')
        if policy=='shared-context-v1' and self.profile_name!='luna-bedrock-global':
            from .role_routes import split_enabled
            if split_enabled():policy='independent-input-output-v1'
        if policy=='shared-context-v1' and self.profile_name!='luna-bedrock-global':
            raise ValueError('Shared-context refinement is reviewed only for Luna Bedrock')
        bound_config=self.config
        if policy=='openai-cache-total-shared-v1':
            if self.profile_name!='luna-bedrock-global' or self.config['context_tokens']!=1050000 or money(self.config['completion_per_million'])>Decimal('.75'):
                raise ValueError('Cache-total policy requires reviewed Luna endpoint bounds')
            def check_cache(value):
                if isinstance(value,dict):
                    if 'ttl' in value and value['ttl'] not in (None,'30m'):raise ValueError('Unsupported cache TTL for default-write reservation')
                    if value.get('prompt_cache_retention') not in (None,'in_memory'):raise ValueError('Unsupported extended cache retention')
                    for v in value.values():check_cache(v)
                elif isinstance(value,list):
                    for v in value:check_cache(v)
            check_cache(body)
            bound_config={**self.config,'prompt_per_million':'.25'}
            request['provider']['max_price']['prompt']=.2
        bound=request_cost_bound(bound_config,maximum,shared_context=policy in ('shared-context-v1','openai-cache-total-shared-v1'))
        cohort=os.environ.get('PACBENCH_COHORT_ID')
        cohort_limit=os.environ.get('PACBENCH_COHORT_LIMIT_USD')
        if bool(cohort)!=bool(cohort_limit):raise ValueError('Cohort ID and liability limit must be paired')
        if cohort and (not re.fullmatch(r'[a-f0-9]{32}',cohort) or not Decimal(0)<money(cohort_limit)<=Decimal(20)):
            raise ValueError('Invalid cohort liability envelope')
        with self.locked() as state:
            calls=state['calls']
            admission_stop=os.environ.get('PACBENCH_DIRECT_STOP_ON_429') or os.environ.get('PACBENCH_STOP_FILE')
            if admission_stop and Path(admission_stop).exists():
                raise RuntimeError('Measured import admission stopped after rate limit or cancellation')
            if state.get('handoff',{}).get('frozen') is True:
                raise RuntimeError('Task ledger frozen for session transfer; source spending is disabled')
            if any(c.get('reported_liability_exceeds_bound') for c in calls):
                stop_measured_admission('reported_liability_exceeds_bound')
                raise RuntimeError('Reported task liability exceeded a reserved bound; paid admission stopped')
            if any(c.get('http_status') in (401,403) for c in calls):
                stop_measured_admission('upstream_authentication_blocked')
                raise RuntimeError('Task authentication blocked')
            own=[c for c in calls if c['project']==project]
            if sum(c.get('http_status') in (400,422) for c in own)>=3:
                raise RuntimeError('Provider repeated request rejection')
            if sum(c.get('finished') is not None and c.get('http_status')==200 and
                   c.get('state')=='uncertain' and
                   (c.get('input_tokens') is None or c.get('output_tokens') is None) for c in own)>=3:
                raise RuntimeError('Three completed provider responses lack usable token usage; project stopped')
            if any(c.get('finish_reason')=='length' for c in own):
                raise RuntimeError('Prior native output was truncated; explicit recovery required')
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
                stop_measured_admission('task_reservation_cap_reached')
                raise RuntimeError('Task spending cap reached')
            row={'id':str(uuid.uuid4()),'project':project,'model':self.model,'started':now(),'finished':None,
                'reserved':str(bound),'accounted':str(bound),'state':'reserved','transport':VERSION,
                'provider_request':request['provider'],'completion_bound_tokens':maximum,
                'request_sha256':fingerprint,'input_tokens':None,'output_tokens':None,
                'http_status':None,'error_label':None,
                'route_profile':self.profile_name,'endpoint':self.endpoint,'pricing_snapshot':dict(self.config),
                'reservation_policy':policy,
                'effective_output_ceiling_condition':int(limit) if limit else None,
                'effective_output_maximum':maximum,
                'compatibility_condition':compatibility,'omitted_unsupported_parameters':omitted,
                'effective_request_sha256':fingerprint,
                'native_request_parameter_keys':sorted(native_body),
                'native_request_parameters':{k:native_body[k] for k in ('reasoning','reasoning_effort','max_tokens','max_completion_tokens','temperature','top_p','seed') if k in native_body},
                'parameter_observation_scope':'Native input before route/price enforcement; absence is not a provider default. completion_bound_tokens is an accounting bound, not an injected output limit.'}
            if retry_of is not None:
                grant=retry_of['authorized_retry'];grant['used_by']=row['id']
                row.update(explicit_retry_of=retry_of['id'],retry_authorization_id=grant['id'])
            if split_enabled():
                from .role_routes import record as role_record
                row.update(role_route_condition=role_record()['condition'],accounting_role=
                    'memory_provider' if project.endswith('-memory') else 'hermes' if project.endswith('-driver') else 'judge')
            if cohort:
                own_cohort=[c for c in calls if c.get('cohort_id')==cohort]
                if any(money(c['cohort_limit_usd'])!=money(cohort_limit) for c in own_cohort):
                    raise RuntimeError('Cohort liability envelope changed')
                if sum(money(c['accounted']) for c in own_cohort)+bound>money(cohort_limit):
                    raise RuntimeError('Cohort liability envelope reached')
                row.update(cohort_id=cohort,cohort_limit_usd=cohort_limit)
            calls.append(row)
        return {'id':row['id'],'request':request}

    def settle(self,identifier,status,data=b'',*,headers=None,body_complete=False):
        # Called after reading/closing a response, never while holding a network lock.
        with self.locked() as state:
            row=next(c for c in state['calls'] if c['id']==identifier)
            self.check_row_profile(row)
            if row['state']=='settled':
                return
            row.update(finished=now(),http_status=status or None,state='uncertain',
                       error_label='response_usage_unavailable' if status==200 else 'upstream_request_failed',
                       response_body_complete=bool(body_complete))
            if headers:
                allowed={'retry-after','x-request-id','x-generation-id','x-openrouter-request-id',
                    'x-ratelimit-limit-requests','x-ratelimit-remaining-requests','x-ratelimit-reset-requests',
                    'x-ratelimit-limit-tokens','x-ratelimit-remaining-tokens','x-ratelimit-reset-tokens'}
                row['response_diagnostic_headers']={str(k).lower():str(v)[:1024]
                    for k,v in headers.items() if str(k).lower() in allowed}
            if status in (401,403,429):
                stop_measured_admission('upstream_rate_limit' if status==429 else 'upstream_authentication_blocked',identifier)
            try:
                if isinstance(data,str):data=data.encode()
                if status != 200 and data:
                    # Native SDK retries can overwrite the original exception.
                    # Retain exact error bytes privately before parsing; this
                    # is diagnostic evidence, never proof of a zero charge.
                    row['private_error_body']=preserve_unusable_response(self.path,identifier,data,body_complete)
                text=data.decode()
                if text.lstrip().startswith('{'):
                    events=[json.loads(text)]
                else:
                    events=[json.loads(line[5:].strip()) for line in text.splitlines()
                            if line.startswith('data:') and line[5:].strip()!='[DONE]']
                row['response_validation']=response_validation(events,body_complete)
                for event in events:
                    for k,v in (('id','upstream_request_id'),('model','served_model'),('provider','served_provider')):
                        if isinstance(event.get(k),str):row[v]=event[k][:256]
                    for choice in event.get('choices') or []:
                        if isinstance(choice.get('finish_reason'),str):row['finish_reason']=choice['finish_reason'][:64]
                if any(e.get('model') is not None and e['model'] not in {self.model,self.profile.get('canonical_slug',self.model)} for e in events):
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
                if status==200 and type(inp) is int and type(out) is int and min(inp,out)>=0:
                    if cost>money(row['reserved']):
                        row.update(accounted=str(cost),state='settled',input_tokens=inp,output_tokens=out,
                            reported_liability_exceeds_bound=True,error_label='reported_liability_exceeds_bound')
                        stop_measured_admission('reported_liability_exceeded',identifier)
                    elif inp<=self.config['context_tokens'] and out<=row['completion_bound_tokens']:
                        row.update(accounted=str(cost),state='settled',input_tokens=inp,output_tokens=out,error_label=None)
            except (ValueError,KeyError,TypeError,UnicodeError,StopIteration):
                pass
            finally:
                # HTTP200 can contain an error envelope or unusable usage.
                # Do not lose the original evidence to a later SDK exception.
                if row.get('finish_reason')=='length':
                    row['error_label']='output_truncated'
                    stop_measured_admission('output_truncated',identifier)
                if row['state']=='uncertain' and status==200 and data:
                    row['private_error_body']=preserve_unusable_response(self.path,identifier,data,body_complete)


def live_budget(*, nonblocking=False, project=None, profile=None):
    if project is not None:
        from .role_routes import profile_for_project
        selected=profile_for_project(project)
        if profile is not None and profile!=selected:raise ValueError("Accounting role/profile conflict")
        profile=selected
    budget=DirectBudget(nonblocking=nonblocking,profile=profile)
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
    if sys.argv[1]=='reserve':
        budget=live_budget(project=value['project'])
    elif sys.argv[1]=='settle':
        base_budget=live_budget()
        state=json.loads(base_budget.path.read_text())
        row=next(r for r in state['calls'] if r['id']==value['id'])
        budget=live_budget(profile=row.get('route_profile',PROFILE_NAME))
        if row.get('route_profile',PROFILE_NAME)!=budget.profile_name:raise ValueError('Settlement route differs from reservation')
    else:raise ValueError('Unknown accounting operation')
    if sys.argv[1]=='reserve':
        print(json.dumps(budget.reserve(value['project'],value['request'])))
    elif sys.argv[1]=='settle':
        budget.settle(value['id'],value['status'],value.get('data',''),headers=value.get('headers'),body_complete=value.get('body_complete',False))
        print('{}')
    else:raise ValueError('Unknown accounting operation')


if __name__=='__main__':main()
