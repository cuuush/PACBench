"""Audit historical clock instructions and current unmodified native prompts."""
import hashlib
import json

LEGACY='user-clock-prefix-v1'
VERSION='ephemeral-system-clock-v2'
NATIVE='native-question-v2'
SUPPORTED=(LEGACY,VERSION,NATIVE)
# Exact English session-title request from the pinned native Hermes checkout.
# This auxiliary call has no answer role; it remains in costs and input traces.
NATIVE_TITLE_PROMPT_SHA256='3f1eef0616ddd2c13d45a3f8cef4ed648dadfdacdba7fbedceebf5a04d549998'
NATIVE_TITLE_SOURCE_SHA256='8d5e38f31819f89677d1893e4332e049889a662fbaf09bcff77b8a3f004f70ab'


def instruction(at):
    return f'[Simulated now: {at}. Treat this as the current date and time for everything in this turn.]'


def expected_prompt(at,question,protocol=LEGACY):
    if protocol==LEGACY:return instruction(at)+'\n\n'+question
    if protocol in (VERSION,NATIVE):return question
    raise ValueError('Unknown clock context protocol')


def judge_question(turn):
    """Keep the judge's explicit date context without polluting native queries."""
    if (getattr(turn,'clock_context',None) or {}).get('protocol') in (VERSION,NATIVE):
        return instruction(turn.at)+'\n\n'+turn.prompt
    return turn.prompt


def delivery(root, turn, *, before=(), identifiers=None, hook_before=0, hook_index=None, protocol=VERSION):
    """Verify independently observed HTTP input and native provider query."""
    from .api_input_trace import rows
    record={'protocol':protocol,'at':turn.at,'state':'fail',
            'query_sha256':hashlib.sha256(turn.prompt.encode()).hexdigest()}
    try:
        if protocol not in (VERSION,NATIVE):raise ValueError('Unsupported actual-input protocol')
        candidates=rows(root/'api-inputs.jsonl')
        if identifiers is not None:
            candidates=[r for r in candidates if r.get('request_id') in identifiers]
        else:
            candidates=[r for r in candidates if r.get('request_id') not in set(before)]
        if not candidates or identifiers is not None and len(candidates)!=len(identifiers):
            raise ValueError('Missing/ambiguous actual HTTP input witness')
        actor_ids=[];auxiliary_ids=[]
        for row in candidates:
            if row.get('protocol')!='direct-hermes-api-input-v1':
                raise ValueError('Unknown HTTP input witness protocol')
            systems=[m.get('content') for m in row['messages'] if m['role']=='system']
            users=[m.get('content') for m in row['messages'] if m['role']=='user']
            if (len(systems)==1 and isinstance(systems[0],str) and
                    hashlib.sha256(systems[0].encode()).hexdigest()==NATIVE_TITLE_PROMPT_SHA256):
                from pathlib import Path
                source=Path(__file__).resolve().parents[1]/'vendor/hermes-isolated/agent/title_generator.py'
                if hashlib.sha256(source.read_bytes()).hexdigest()!=NATIVE_TITLE_SOURCE_SHA256:
                    raise ValueError('Native auxiliary-title source differs from pinned classifier')
                auxiliary_ids.append(row['request_id'])
                continue
            if (len(systems)!=1 or not isinstance(systems[0],str) or
                    not users or not isinstance(users[-1],str) or not
                    (users[-1]==turn.prompt or users[-1].startswith(turn.prompt+'\n\n')) or
                    turn.prefetch_context and turn.prefetch_context not in users[-1]):
                raise ValueError('Scenario clock or natural question differs at HTTP boundary')
            if protocol==VERSION:
                if not systems[0].endswith(instruction(turn.at)):
                    raise ValueError('Scenario clock missing at historical HTTP boundary')
            else:
                native=(getattr(turn,'context_isolation',None) or {})
                if (native.get('state')!='pass' or
                        hashlib.sha256(systems[0].encode()).hexdigest()!=native.get('system_prompt_sha256') or
                        '[Simulated now:' in systems[0]):
                    raise ValueError('Actual system input differs from the native saved prompt or contains benchmark clock instructions')
            actor_ids.append(row['request_id'])
        if not actor_ids:
            raise ValueError('No actual answer request; auxiliary calls cannot prove clock delivery')
        hooks=[json.loads(line) for line in (root/'context.jsonl').read_text().splitlines()]
        matches=[(index,h) for index,h in enumerate(hooks) if index>=hook_before and
                 (hook_index is None or index==hook_index) and h.get('session_id')==turn.session_id and
                 h.get('query')==turn.prompt and h.get('prefetch')==turn.prefetch_context]
        if len(matches)!=1:
            raise ValueError('Native automatic-retrieval query differs from natural question')
        index,hook=matches[0]
        record.update(state='pass',native_query_index=index,
            actor_api_input_ids=actor_ids,auxiliary_title_api_input_ids=auxiliary_ids,
            native_query_sha256=hashlib.sha256(json.dumps(hook,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
            api_input_ids=[r['request_id'] for r in candidates],
            api_input_sha256=[hashlib.sha256(json.dumps(r,sort_keys=True,ensure_ascii=False).encode()).hexdigest() for r in candidates],
            reason=('Actual HTTP system input carries the clock; native retrieval receives the unchanged natural question' if protocol==VERSION else
                    'Actual HTTP system input equals the native saved prompt; native retrieval receives the verbatim question with no benchmark clock instructions'))
    except (OSError,ValueError,KeyError,TypeError,IndexError) as exc:
        record['reason']=str(exc)
    return record
