"""Exact response billing evidence, separate from admission estimates.

Only identifiers, numeric usage and terminal metadata may reach a receipt.
Assistant text, prompts, reasoning, tool arguments and headers are excluded.
"""
from __future__ import annotations
import codecs
import json
from decimal import Decimal

PROTOCOL = 'openrouter-response-billing-v1'


def loads(text):
    return json.loads(text, parse_float=Decimal)


def sanitized(event):
    out={k:event[k][:256] for k in ('id','model','provider') if isinstance(event.get(k),str)}
    choices=event.get('choices') or []
    reasons=[c['finish_reason'][:64] for c in (choices if isinstance(choices,list) else [])
             if isinstance(c,dict) and isinstance(c.get('finish_reason'),str)]
    if reasons:out['finish_reasons']=reasons
    usage=event.get('usage')
    if isinstance(usage,dict):
        clean={}
        for key,value in usage.items():
            if key=='cost':
                if not isinstance(value,bool) and isinstance(value,(int,float,Decimal,str)):
                    try:
                        cost=Decimal(str(value))
                        if cost.is_finite() and cost>=0:clean[key]=str(cost)
                    except Exception:pass
            elif key in {'prompt_tokens','completion_tokens','total_tokens'}:
                if type(value) is int and value>=0:clean[key]=value
            elif key in {'prompt_tokens_details','completion_tokens_details','input_tokens_details','output_tokens_details','cost_details'} and isinstance(value,dict):
                detail={}
                for name,number in value.items():
                    if (key=='cost_details' and name in {'upstream_inference_cost','upstream_inference_prompt_cost','upstream_inference_completions_cost'} and
                            not isinstance(number,bool) and isinstance(number,(int,float,Decimal))):
                        n=Decimal(str(number))
                        if n.is_finite() and n>=0:detail[name]=str(n)
                    elif name in {'cached_tokens','cache_write_tokens','reasoning_tokens','audio_tokens','video_tokens','image_tokens'} and type(number) is int and number>=0:
                        detail[name]=number
                if detail:clean[key]=detail
        if clean:out['usage']=clean
    return out


class EventReader:
    """Incremental SSE frames; an invalid tail cannot erase preceding frames."""
    def __init__(self, callback=None):
        self.callback=callback;self.decoder=codecs.getincrementaldecoder('utf-8')()
        self.buffer='';self.lines=[];self.events=[];self.errors=0;self.sse=False

    def _event(self):
        if not self.lines:return
        text='\n'.join(self.lines);self.lines=[]
        if not text.strip() or text.strip()=='[DONE]':return
        try:
            event=loads(text)
            if not isinstance(event,dict):raise ValueError('Not an object')
        except (ValueError,TypeError):self.errors+=1;return
        self.events.append(event)
        if self.callback:self.callback(event)

    def feed(self,data):
        try:self.buffer+=self.decoder.decode(data)
        except UnicodeError:self.errors+=1;return
        while '\n' in self.buffer:
            line,self.buffer=self.buffer.split('\n',1);line=line.rstrip('\r')
            if line.startswith('data:'):
                self.sse=True;self.lines.append(line[5:].lstrip(' '))
            elif not line and self.sse:self._event()
            elif not line:continue
            elif line.startswith((':','event:','id:','retry:')):self.sse=True
            elif not self.sse:
                # Non-streaming JSON can contain newlines. Keep it intact.
                self.buffer=line+'\n'+self.buffer;break

    def finish(self):
        try:self.buffer+=self.decoder.decode(b'',final=True)
        except UnicodeError:self.errors+=1
        if self.sse or self.buffer.startswith('data:'):
            if self.buffer.startswith('data:'):self.lines.append(self.buffer[5:].strip())
            elif self.buffer.strip() and not self.buffer.startswith(':'):self.errors+=1
            self.buffer='';self._event()
        elif self.buffer.strip():
            self.lines=[self.buffer];self.buffer='';self._event()
        return self.events


def parse(data):
    reader=EventReader();reader.feed(data);return reader.finish(),reader.errors


def receipt(identifier,event,*,status,body_sha256,complete):
    safe=sanitized(event)
    return {'protocol':PROTOCOL,'attempt_id':identifier,'http_status':status,
        'generation_id':safe.get('id'),'model':safe.get('model'),'provider':safe.get('provider'),
        'usage':safe.get('usage',{}),'finish_reasons':safe.get('finish_reasons',[]),
        'body_sha256':body_sha256,'body_complete':bool(complete),
        'cost_source':'response.usage.cost','reported_zero':
            Decimal(safe['usage']['cost'])==0 if 'cost' in safe.get('usage',{}) else False}


def financials(row):
    """Legacy rows remain readable; their exposure is never called a charge."""
    state=row['state'];known=state=='settled'
    amount=str(row.get('settled_charge_usd',row['accounted'])) if known else None
    return {'settled_charge_usd':amount,'outstanding_reservation_usd':row['accounted'] if state=='reserved' else '0',
        'unresolved_charge_ceiling_usd':row['accounted'] if state not in {'settled','reserved','allocated','rejected_no_dispatch'} else '0',
        'external_allocation_usd':row['accounted'] if state=='allocated' else '0',
        'exposure_usd':row['accounted'],'reservation_ceiling_usd':row.get('reserved'),
        'charge_evidence':'sanitized-response-receipt' if row.get('billing_receipt') else
            'authoritative-generation-reconciliation' if (row.get('reconciliation') or {}).get('cost_source')=='total_cost' else
            'legacy-ledger-reported-charge-without-raw-receipt' if known else 'unknown'}
