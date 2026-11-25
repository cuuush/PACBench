"""Isolate the real judge CLI and supply synthetic HTTP responses in the test process."""

from email.message import Message
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from urllib.request import OpenerDirector, Request

campaign = Path(os.environ['PACBENCH_TEST_CAMPAIGN'])
lock_path = campaign/'execution.lock'
mode = os.environ['PACBENCH_TEST_RESPONSE']
real_campaign = '/workspace/scratch/pizza-v2-live-20261121'
model = 'openai/gpt-6-luna'
api = 'https://openrouter.ai/api/v1/'
sent = []


def refuse_network(event, _args):
    if event.startswith('socket.'):
        raise AssertionError('Calibration fixture must use the synthetic upstream')


sys.addaudithook(refuse_network)

# Redirect the fixed production directory in both module instances without
# importing the entry point ahead of Python's normal -m launch.
def relocated(args):
    return (str(campaign),) if args == (real_campaign,) else args


if Path.__init__ is object.__init__:
    original_new = Path.__new__

    def fixture_new(cls, *args, **kwargs):
        return original_new(cls, *relocated(args), **kwargs)

    Path.__new__ = staticmethod(fixture_new)
else:
    original_init = Path.__init__

    def fixture_init(self, *args, **kwargs):
        original_init(self, *relocated(args), **kwargs)

    Path.__init__ = fixture_init


def held_descriptor():
    matches = [int(fd.name) for fd in Path('/proc/self/fd').iterdir()
               if fd.is_symlink() and fd.readlink() == lock_path]
    assert len(matches) == 1
    fd, = matches
    info = Path(f'/proc/self/fdinfo/{fd}').read_text()
    assert any(line.startswith('lock:') and {'FLOCK', 'WRITE', str(os.getpid())} <= set(line.split())
               for line in info.splitlines())
    return fd


def response(body, content_type='application/json'):
    stream = io.BytesIO(body)
    stream.status = 200
    stream.headers = Message()
    stream.headers['Content-Type'] = content_type
    return stream


def upstream(_opener, request, data=None, timeout=None):
    if isinstance(request, str):
        request = Request(request, data=data)
    assert request.full_url.startswith(api)
    endpoint = request.full_url.removeprefix(api)
    if request.get_method() == 'GET':
        if endpoint == 'models/'+model+'/endpoints':
            value = dict(id=model, endpoints=[dict(tag='amazon-bedrock/global', model_id=model,
                quantization='unknown', service_tier=None, context_length=128000, max_completion_tokens=32768,
                supported_parameters=['tools', 'tool_choice', 'response_format'],
                pricing={'prompt': '0.00000015', 'completion': '0.00000050'})])
        elif endpoint == 'key':
            identity = json.loads((campaign.parent/'pizza-v2-handoff/billing-resume/key.private.json').read_text())
            value = dict(identity['data'], limit=10, limit_remaining=10, limit_reset=None)
        else:
            assert endpoint == 'credits'
            value = dict(total_credits=10, total_usage=0, auto_recharge_enabled=False)
            if mode == 'unlocked':
                fcntl.flock(held_descriptor(), fcntl.LOCK_UN)
        return response(json.dumps({'data': value}).encode())

    assert endpoint == 'chat/completions' and request.get_method() == 'POST'
    assert request.get_header('Authorization') == 'Bearer synthetic-execution-key'
    held_descriptor()
    body = json.loads(request.data)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    sent.append(digest)
    with (campaign/'wire.jsonl').open('a') as stream:
        stream.write(json.dumps(digest)+'\n')
    verdict = json.dumps({'verdicts': [{'id': 'criterion', 'verdict': 'correct', 'reason': 'Synthetic response'}]})
    usage = dict(prompt_tokens=4, completion_tokens=3)
    if mode != 'unsettled':
        usage['cost'] = '0.000012'
    event = dict(id=f'synthetic-generation-{len(sent)}', model=model, provider='Amazon Bedrock',
                 choices=[dict(index=0, delta=dict(content=verdict), finish_reason='stop')], usage=usage)
    return response(('data: '+json.dumps(event)+'\n\ndata: [DONE]\n\n').encode(), 'text/event-stream')


OpenerDirector.open = upstream
