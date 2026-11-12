"""Exercise the installed SDK with MockTransport, never a provider endpoint."""
import json
import os
import socket
import sys
import tempfile
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import honcho
from honcho import Honcho as NativeHoncho
from provider_adapters import honcho_enrollment as enrollment


def forbid_network(*args, **kwargs):
    raise RuntimeError('No live network in native SDK diagnostic')


socket.socket.connect = forbid_network
state = {'db_at':'2026-09-10T21:58:00+00:00','memberships':[]}
requests = []


def serve(request):
    body = json.loads(request.content)
    path = request.url.path
    requests.append({'path':path,'method':request.method,'body':body})
    if '/sessions/' in path and path.endswith('/peers'):
        session = path.split('/')[-2]
        state['memberships'] = [{'session_id':session,'peer_id':name,
            'joined_at':state['db_at'],'left_at':None} for name in body]
        return httpx.Response(200, json={})
    return httpx.Response(200, json={'id':body.get('id','isolated'),
        'workspace_id':'isolated','metadata':{},'configuration':{},
        'created_at':state['db_at'],'is_active':True})


honcho.Honcho = lambda **kwargs: NativeHoncho(**kwargs,
    http_client=httpx.Client(transport=httpx.MockTransport(serve)))
module = ModuleType('plugins.memory.honcho.client')
module.HonchoClientConfig = SimpleNamespace(from_global_config=lambda: SimpleNamespace(
    user_observe_me=True,user_observe_others=False,ai_observe_me=False,ai_observe_others=True))
sys.modules['plugins.memory.honcho.client'] = module
from provider_adapters import honcho_clock
honcho_clock.enabled = lambda root: False
enrollment.native_snapshot = lambda *args: deepcopy(state)
os.environ[enrollment.ENVIRONMENT] = enrollment.PROTOCOL
os.environ.pop('PACBENCH_STOP_FILE',None)

with tempfile.TemporaryDirectory(prefix='honcho-sdk-offline-') as directory:
    root = Path(directory)/'arm'
    source = Path(directory)/'day'
    source.mkdir()
    (root/'hermes-home').mkdir(parents=True)
    (root/'hermes-home/honcho.json').write_text(json.dumps({
        'baseUrl':'http://127.0.0.1:1','hosts':{'hermes':{
            'peerName':'user','aiPeer':'hermes','workspace':'isolated'}}}))
    original = json.dumps([{'source_id':'fixture','channel':'imessage','thread':'opaque',
        'sender':'Sam','from_owner':False,'text':'Original source body.',
        'written_at':'2026-09-10T22:00:00+00:00','structured':{'participants':['Me','Sam']}}])
    (source/'messages.json').write_text(original)
    result = enrollment.enroll_source(source,root)
    assert result['ready'] is True
    assert requests[-1]['path'].endswith('/peers')
    assert set(requests[-1]['body']) == {'Sam','user','hermes'}
    assert requests[-1]['body']['user']['observe_me'] is True
    assert requests[-1]['body']['hermes']['observe_others'] is True
    assert not any('/messages' in r['path'] or '/chat' in r['path'] for r in requests)
    assert (source/'messages.json').read_text() == original
    print(json.dumps({'sdk_version':version('honcho-ai'),'diagnostic_only':True,
        'transport':'HTTPX MockTransport; native SDK unmodified','requests':requests,
        'source_messages_submitted':0,'live_network_calls':0,'model_calls':0,
        'result':result['state'],
        'limitations':['Server responses and membership telemetry are fixture data, not a live database.']},indent=2))
