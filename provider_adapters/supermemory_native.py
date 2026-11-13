"""The selected native v5 namespace and document-status API, without inference."""
from __future__ import annotations


def namespace(plugin, home):
    config = plugin._load_supermemory_config(str(home))
    tag = plugin._sanitize_tag(plugin._expand_container_tag(config['container_tag'], 'default'))
    if not config['base_url'].startswith('http://127.0.0.1:'):
        raise ValueError('Supermemory requires the run-owned local server')
    return config, tag


def document(client, tag, identifier, *, endpoint=None, key=None):
    """Keep the native response; read readiness from its documented system field."""
    import urllib.parse
    value = client.documents.get(tag, urllib.parse.quote(identifier, safe='')).model_dump(mode='json')
    if value.get('id') != identifier or not isinstance(value.get('system'), dict):
        raise ValueError('Supermemory native document identity or system status missing')
    system = value['system']
    state = {'id': value['id'], 'status': system.get('status'),
             'dreaming_status': system.get('dreaming_status', system.get('dreamingStatus'))}
    if state['status']=='done' and state['dreaming_status'] is None and endpoint and key:
        witness = completion_witness(endpoint, key, tag, identifier, value)
        state['dreaming_status'] = witness['dreaming_status']
        state['native_completion_witness'] = witness
    return value, state


def completion_witness(endpoint, key, tag, identifier, value):
    """Observe the published local server's v3 consolidation field read-only.

    server-v0.0.9's v5 document response omits dreamingStatus. Its v3 read
    endpoint exposes the actual native phase. Bind both APIs to one namespace,
    public ID, exact content and metadata; never infer completion from indexing.
    No writes, retries, queue changes or SDK downgrade are involved.
    """
    import hashlib
    import json
    import urllib.parse
    from urllib.request import Request, urlopen
    if not endpoint.startswith('http://127.0.0.1:'):
        raise ValueError('Native consolidation observer must stay on run-owned loopback')
    def read(path, body=None):
        request = Request(endpoint.rstrip('/')+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        with urlopen(request, timeout=10) as response:
            return json.load(response)
    matches=[]
    page=1
    while True:
        listed=read('/v3/documents/list',{'containerTags':[tag], 'includeContent':False,'page':page,'limit':100})
        rows=listed.get('memories');pagination=listed.get('pagination',{})
        pages=pagination.get('totalPages',pagination.get('total_pages'))
        current=pagination.get('currentPage',pagination.get('current_page'))
        if (not isinstance(rows,list) or type(pages) not in (int,float) or pages!=int(pages)
                or not 0<=pages<=10000 or current!=page):
            raise ValueError('Native consolidation listing is incomplete')
        matches.extend(item for item in rows if item.get('customId',item.get('custom_id'))==identifier
                       and tag in item.get('containerTags',item.get('container_tags',[])))
        if page>=pages:break
        page+=1
    if len(matches)!=1 or not isinstance(matches[0].get('id'),str):
        raise ValueError('Native consolidation document mapping is not unique')
    native_id=matches[0]['id']
    native=read('/v3/documents/'+urllib.parse.quote(native_id,safe=''))
    if (native.get('id')!=native_id or native.get('customId',native.get('custom_id'))!=identifier
            or tag not in native.get('containerTags',native.get('container_tags',[]))
            or native.get('content')!=value.get('content') or native.get('metadata')!=value.get('metadata')):
        raise ValueError('Native consolidation witness differs from v5 source or namespace')
    phase=native.get('dreamingStatus',native.get('dreaming_status'))
    if not isinstance(phase,str):raise ValueError('Native consolidation status unavailable')
    content=native.get('content')
    return {'protocol':'supermemory-native-v3-completion-observer-v1',
            'native_id':native_id,'public_id':identifier,'namespace':tag,
            'status':native.get('status'),'dreaming_status':phase,
            'content_sha256':hashlib.sha256(content.encode()).hexdigest() if isinstance(content,str) else None,
            'metadata':native.get('metadata'),'processing_forced':False}
