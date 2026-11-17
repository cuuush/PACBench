"""Complete, metered Chat Completions SSE replies for benchmark judging."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json

VERSION = 'complete-chat-stream-v1'


class IncompleteChatStream(ValueError):
    def __init__(self, reason, response):
        super().__init__(reason)
        self.response=response


def parse_chat_stream(body: bytes, model: str) -> tuple[dict, dict]:
    """Require an actual end, successful finish, and usable cost/token usage.

    Whitespace, heartbeat comments, partial JSON, and a plausible partial verdict
    cannot substitute for a complete response. No network request is made here.
    """
    response={'model':model,'choices':[{'index':0,'message':{'role':'assistant','content':''},'finish_reason':None}]}
    proof={'protocol':VERSION,'body_bytes':len(body),'body_sha256':hashlib.sha256(body).hexdigest(),
           'done':False,'finish_reason':None,'usage_complete':False,'events':0}
    content=[];reasoning=[];details=[];identifier=None;finished=None;done=False
    def fail(reason):
        response['choices'][0]['message']['content']=''.join(content)
        raise IncompleteChatStream(reason,response)
    try:text=body.decode('utf-8')
    except UnicodeError:fail('Judge stream is not UTF-8')
    events=[];data=[]
    for line in text.splitlines()+['']:
        if not line.strip():
            if data:events.append('\n'.join(data));data=[]
        elif line.startswith('data:'):
            data.append(line[5:].removeprefix(' '))
        elif line.startswith(':') or line.startswith(('event:','id:','retry:')):
            continue
        else:
            fail('Judge response contains non-SSE data')
    for payload in events:
        if done:fail('Judge stream contains data after its terminator')
        if payload=='[DONE]':done=True;proof['done']=True;continue
        try:event=json.loads(payload)
        except (ValueError,TypeError):fail('Judge stream contains malformed event JSON')
        if not isinstance(event,dict):fail('Judge stream event is not an object')
        proof['events']+=1
        if event.get('error'):fail('Judge stream reports an upstream error')
        if event.get('model') not in (None,model):fail('Judge stream served a different model')
        if isinstance(event.get('id'),str) and event['id']:
            if identifier is not None and event['id']!=identifier:fail('Judge stream generation identity changed')
            identifier=event['id'];response['id']=identifier
        for name in ('provider','created'):
            if event.get(name) is not None:response[name]=event[name]
        if isinstance(event.get('usage'),dict):response['usage']=event['usage']
        choices=event.get('choices',[])
        if not isinstance(choices,list):fail('Judge stream choices are malformed')
        for choice in choices:
            if not isinstance(choice,dict) or choice.get('index',0)!=0:fail('Judge stream has an unexpected choice')
            if choice.get('error'):fail('Judge stream choice reports an upstream error')
            delta=choice.get('delta') or {}
            if not isinstance(delta,dict):fail('Judge stream delta is malformed')
            if delta.get('tool_calls') or delta.get('function_call'):fail('Judge stream attempted a tool call')
            if delta.get('role') not in (None,'assistant'):fail('Judge stream changed message role')
            piece=delta.get('content')
            if piece is not None:
                if not isinstance(piece,str):fail('Judge stream content is not text')
                if finished and piece:fail('Judge stream content continued after its finish')
                content.append(piece)
            for key in ('reasoning','reasoning_content'):
                if isinstance(delta.get(key),str):reasoning.append(delta[key])
            if isinstance(delta.get('reasoning_details'),list):details.extend(delta['reasoning_details'])
            finish=choice.get('finish_reason')
            if finish is not None:
                if finished is not None and finish!=finished:fail('Judge stream finish reason changed')
                finished=finish;response['choices'][0]['finish_reason']=finish;proof['finish_reason']=finish
    if not done:fail('Judge stream ended without [DONE]')
    if finished!='stop':fail('Judge stream did not finish successfully')
    if not identifier:fail('Judge stream has no generation identity')
    if not ''.join(content).strip():fail('Judge stream contains no answer text')
    usage=response.get('usage',{})
    try:
        cost=Decimal(str(usage['cost']))
        if (not cost.is_finite() or cost<0 or type(usage['prompt_tokens']) is not int
                or type(usage['completion_tokens']) is not int
                or usage['prompt_tokens']<0 or usage['completion_tokens']<0):
            raise ValueError('invalid usage')
    except (ValueError,KeyError,InvalidOperation):fail('Judge stream lacks complete token and cost usage')
    response['choices'][0]['message']['content']=''.join(content)
    if reasoning:response['choices'][0]['message']['reasoning']=''.join(reasoning)
    if details:response['choices'][0]['message']['reasoning_details']=details
    proof['usage_complete']=True
    return response,proof
