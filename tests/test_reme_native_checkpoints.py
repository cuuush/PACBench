"""Native resource/write/index lifecycle, with synthetic local completions only."""

import hashlib, json, re

def write_response(body):
    messages=body['messages']
    if any(m.get('role')=='tool' for m in messages):return 'Created the source-linked offline fixture card.'
    names=[t.get('function',{}).get('name') for t in body.get('tools',[])]
    name=next((n for n in names if n=='write' or n.endswith('_write')),None)
    raw='\n'.join(m.get('content','') if isinstance(m.get('content'),str) else '\n'.join(b.get('text','') for b in m.get('content',[]) if isinstance(b,dict)) for m in messages)
    target=re.search(r'Target note path: (.+)',raw);source=re.search(r'Source resource link: (.+)',raw)
    assert name and target and source,'Unexpected native resource call; no synthetic fallback'
    args={'path':target.group(1).strip(),'name':'offline-native-resource',
        'description':'Synthetic native resource lifecycle witness; no extraction quality claim.',
        'content':'Offline derived fixture memory '+hashlib.sha256(raw.encode()).hexdigest()[:16],
        'metadata':{'source_resource':source.group(1).strip()}}
    return {'role':'assistant','content':None,'tool_calls':[{'id':'offline-resource-write','type':'function',
        'function':{'name':name,'arguments':json.dumps(args)}}]}
