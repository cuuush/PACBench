"""Admit stock chat-deriver retries after an observed, settled output rejection."""
from contextvars import ContextVar
from functools import wraps
from hashlib import sha256

from .dispatch_ownership import OwnershipConflict
from .honcho_first_answer_admission import settled

_physical_send = ContextVar('honcho_week_chat_representation_send',default=None)
PURPOSE = 'deriver.representation'
REJECTION = 'stock-prompt-representation-truncated-and-unparseable-v1'


def watch(original):
    """Observe the pinned executor's rejection before the native save loop."""
    from src.llm.structured_output import StructuredOutputError
    from src.utils.representation import PromptRepresentation

    @wraps(original)
    async def observed(*args,**kwargs):
        sent=[]
        token=_physical_send.set(sent)
        try:
            return await original(*args,**kwargs)
        except StructuredOutputError as error:
            if (len(sent)==1 and kwargs.get('response_model') is PromptRepresentation
                    and str(error).startswith('PromptRepresentation structured output failed failure_class=ValidationError ')
                    and isinstance(error.raw_content,str) and error.raw_content):
                record_rejection(sent[0],sha256(error.raw_content.encode()).hexdigest())
            raise
        finally:
            _physical_send.reset(token)

    return observed


def mark_physical_send(row):
    sent=_physical_send.get()
    if row['workflow_slot'].get('honcho_chat_work',{}).get('purpose')==PURPOSE:
        if sent is None or sent:
            raise OwnershipConflict('Honcho chat representation has no unique native executor send')
        sent.append(row['id'])


def record_rejection(ident,payload_sha256):
    from .honcho_daily_gate import _live
    from .workflow_capability import budget,load

    scope=load()
    with budget().journal() as (rows,append):
        row=rows[ident]
        native=row['honcho_daily_attempt']
        evidence=row['response_validation']
        if (not scope.get('week_schedule') or row['workflow_slot']['honcho_chat_work']['purpose']!=PURPOSE
                or not settled(row) or evidence['finish_reasons']!=['length']
                or evidence['assistant_text_sha256']!=payload_sha256 or row.get('honcho_validation_rejection')):
            return
        _,current=_live(scope,row['workflow_slot'])
        if any(native[key]!=value for key,value in current.items()):
            raise OwnershipConflict('Honcho queue claim changed before its native validation rejection')
        append('validation_rejection',ident,dict(honcho_validation_rejection=dict(
            reason=REJECTION,request_sha256=row['request_sha256'],response_sha256=payload_sha256,
            upstream_request_id=row['upstream_request_id'],native_attempt=native['native_attempt'])))


def reserve_chat_retry(rows,row):
    """Return one exact predecessor for a stock retry. Reject replay and changed batches."""
    job=row['workflow_slot']['honcho_chat_work']
    native=row['honcho_daily_attempt']
    number=native['native_attempt']
    prior=[r for r in rows.values() if (old:=r['workflow_slot'].get('honcho_chat_work'))
           and old['purpose']==job['purpose'] and set(old['queue_ids'])&set(job['queue_ids'])]
    if not prior and number==1:
        return None
    if job['purpose']!=PURPOSE or number not in (2,3) or len(prior)!=number-1:
        raise OwnershipConflict('Honcho chat retry is outside its observed stock attempt sequence')
    for ordinal,old in enumerate(prior,1):
        earlier=old['honcho_daily_attempt']
        proof=old.get('honcho_validation_rejection',{})
        if (old['workflow_slot']!=row['workflow_slot'] or old['dispatch_owner']!=row['dispatch_owner']
                or old['request_sha256']!=row['request_sha256'] or not settled(old)
                or old['response_validation']['finish_reasons']!=['length']
                or any(earlier[key]!=native[key] for key in ('queue_ids','claim','trace'))
                or earlier['native_attempt']!=ordinal
                or proof!=dict(reason=REJECTION,request_sha256=row['request_sha256'],
                              response_sha256=old['response_validation']['assistant_text_sha256'],
                              upstream_request_id=old['upstream_request_id'],native_attempt=ordinal)):
            raise OwnershipConflict('Honcho chat retry lacks a settled, unmodified native validation rejection')
    return prior[-1]
