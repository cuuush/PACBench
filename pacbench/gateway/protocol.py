"""Bounded protocol shapes only: no prompts, arguments, names or response text."""
from __future__ import annotations


LABELS = {
    'tool_choice': ('absent','auto','none','required','function_object','other_string','other_object','invalid'),
    'tools': ('absent','array','invalid'),
    'response_format': ('absent','text','json_object','json_schema','other'),
    'wire': ('sse','json','other'),
}
TOKEN_FIELDS = ('prompt_tokens','completion_tokens','input_tokens','output_tokens','total_tokens')
TYPES = ('integer','number','boolean','null','string','array','object','other')
COUNTS = ('tool_count','json_values','usage_objects')


def safe_protocol(value):
    if not isinstance(value,dict):return {}
    result={key:value[key] for key,labels in LABELS.items() if value.get(key) in labels}
    for key in COUNTS:
        n=value.get(key)
        if type(n) is int and 0<=n<=1_048_576:result[key]=n
    for key in ('stream','top_usage','nested_usage'):
        if type(value.get(key)) is bool:result[key]=value[key]
    fields=value.get('usage_fields')
    if isinstance(fields,dict):
        result['usage_fields']={k:fields[k] for k in TOKEN_FIELDS if fields.get(k) in TYPES}
    return result


def request_shape(value):
    choice=value.get('tool_choice')
    if 'tool_choice' not in value:kind='absent'
    elif isinstance(choice,str):kind=choice if choice in ('auto','none','required') else 'other_string'
    elif isinstance(choice,dict):kind='function_object' if choice.get('type')=='function' else 'other_object'
    else:kind='invalid'
    tools=value.get('tools')
    result={'tool_choice':kind,'tools':'absent' if 'tools' not in value else 'array' if isinstance(tools,list) else 'invalid',
            'stream':value.get('stream',False),'response_format':'absent'}
    if isinstance(tools,list):result['tool_count']=len(tools)
    if 'response_format' in value:
        fmt=value['response_format']
        fmt=fmt.get('type') if isinstance(fmt,dict) else None
        result['response_format']=fmt if fmt in ('text','json_object','json_schema') else 'other'
    return safe_protocol(result)


def observe_usage(shape,value):
    shape['json_values']=shape.get('json_values',0)+1
    if not isinstance(value,dict):return
    nested=value.get('response')
    for key,parent in (('top_usage',value),('nested_usage',nested)):
        if not isinstance(parent,dict) or not isinstance(parent.get('usage'),dict):continue
        usage=parent['usage']
        shape[key]=True
        shape['usage_objects']=shape.get('usage_objects',0)+1
        fields=shape.setdefault('usage_fields',{})
        for field in TOKEN_FIELDS:
            if field not in usage:continue
            raw=usage[field]
            kind={int:'integer',float:'number',bool:'boolean',str:'string',list:'array',dict:'object',type(None):'null'}.get(type(raw),'other')
            fields[field]=kind
