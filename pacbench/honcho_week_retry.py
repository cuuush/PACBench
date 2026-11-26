"""Admit stock representation retries after an observed terminal output rejection."""
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256

from .dispatch_ownership import OwnershipConflict
from .honcho_first_answer_admission import usable_response

_physical_send = ContextVar('honcho_week_representation_send',default=None)
PURPOSE = 'deriver.representation'
REJECTION = 'stock-prompt-representation-truncated-and-unparseable-v1'
SOURCE_REJECTION = 'stock-daily-prompt-representation-truncated-and-empty-v1'
EMPTY_SHA256 = sha256(b'').hexdigest()


def _representation(slot):
    return (bool(slot.get('honcho_daily_release'))
            or slot.get('honcho_chat_work', {}).get('purpose') == PURPOSE)


def _rejection(row):
    """Build the exact proof a matched native parser failure is allowed to record."""
    slot = row['workflow_slot']
    evidence = row.get('response_validation', {})
    if (not _representation(slot) or not usable_response(row, allow_truncated=True)
            or evidence.get('finish_reasons') != ['length']
            or evidence.get('tool_call_fragments') != 0):
        return None
    digest = evidence.get('assistant_text_sha256')
    characters = evidence.get('assistant_text_characters')
    if not digest or type(characters) is not int:
        return None
    if characters == 0 and digest == EMPTY_SHA256 and slot.get('honcho_daily_release'):
        reason = SOURCE_REJECTION
    elif characters > 0 and digest != EMPTY_SHA256:
        reason = REJECTION
    else:
        return None
    return dict(reason=reason,request_sha256=row['request_sha256'],response_sha256=digest,
                upstream_request_id=row['upstream_request_id'],native_attempt=row['honcho_daily_attempt']['native_attempt'])


def watch(original):
    """Observe the pinned parser's rejection before it can return a representation to save."""
    from src.llm.structured_output import StructuredOutputError
    from src.utils.representation import PromptRepresentation

    @wraps(original)
    async def observed(*args,**kwargs):
        sent=[]
        token=_physical_send.set(sent)
        try:
            return await original(*args,**kwargs)
        except StructuredOutputError as error:
            if len(sent)==1 and kwargs.get('response_model') is PromptRepresentation:
                prefix='PromptRepresentation structured output failed failure_class='
                if error.raw_content=='' and str(error).startswith(prefix+'ValueError '):
                    record_rejection(sent[0],EMPTY_SHA256,SOURCE_REJECTION)
                elif (isinstance(error.raw_content,str) and error.raw_content
                      and str(error).startswith(prefix+'ValidationError ')):
                    record_rejection(sent[0],sha256(error.raw_content.encode()).hexdigest(),REJECTION)
            raise
        finally:
            _physical_send.reset(token)

    return observed


def mark_physical_send(row):
    sent=_physical_send.get()
    if _representation(row['workflow_slot']):
        if sent is None or sent:
            raise OwnershipConflict('Honcho representation has no unique native executor send')
        sent.append(row['id'])


def record_rejection(ident,payload_sha256,reason):
    from .honcho_daily_gate import _live
    from .workflow_capability import budget,load

    scope=load()
    with budget().journal() as (rows,append):
        row=rows[ident]
        proof=_rejection(row)
        if (not scope.get('week_schedule') or not proof or proof['reason']!=reason
                or proof['response_sha256']!=payload_sha256 or row.get('honcho_validation_rejection')):
            return
        _,current=_live(scope,row['workflow_slot'])
        if any(row['honcho_daily_attempt'][key]!=value for key,value in current.items()):
            raise OwnershipConflict('Honcho queue claim changed before its native validation rejection')
        append('validation_rejection',ident,dict(honcho_validation_rejection=proof))


def reserve_retry(rows,row):
    """Return one exact predecessor for a stock retry. Reject replay and changed batches."""
    slot=row['workflow_slot']
    native=row['honcho_daily_attempt']
    number=native['native_attempt']
    if release:=slot.get('honcho_daily_release'):
        prior=[r for r in rows.values() if r['workflow_slot'].get('honcho_daily_release')==release]
        claim_keys=('queue_id','claim','trace')
    else:
        job=slot['honcho_chat_work']
        prior=[r for r in rows.values() if (old:=r['workflow_slot'].get('honcho_chat_work'))
               and old['purpose']==job['purpose'] and set(old['queue_ids'])&set(job['queue_ids'])]
        claim_keys=('queue_ids','claim','trace')
    if not prior and number==1:
        return None
    if not _representation(slot) or number not in (2,3) or len(prior)!=number-1:
        raise OwnershipConflict('Honcho representation retry is outside its observed stock attempt sequence')
    parent=None
    generations=set()
    for ordinal,old in enumerate(prior,1):
        earlier=old['honcho_daily_attempt']
        proof=_rejection(old)
        if (any(old.get(key)!=row.get(key) for key in ('workflow_slot','workflow_capability',
                'workflow_root','project','dispatch_owner','request_sha256'))
                or any(earlier[key]!=native[key] for key in claim_keys) or earlier['native_attempt']!=ordinal
                or old.get('retry_of')!=parent or not proof or old.get('honcho_validation_rejection')!=proof
                or old['upstream_request_id'] in generations):
            raise OwnershipConflict('Honcho representation retry lacks an exact native validation rejection')
        generations.add(old['upstream_request_id'])
        parent=old['id']
    return prior[-1]
