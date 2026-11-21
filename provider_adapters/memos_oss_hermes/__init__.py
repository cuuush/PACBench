"""Hermes lifecycle binding for the documented MemTensor MemoryOS OSS API."""
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import UUID
from zoneinfo import ZoneInfo

from agent.memory_provider import MemoryProvider


_TOOLS = (
    {
        'name': 'memos_get',
        'description': 'Read a saved memory from the current MemOS cube by memory ID, including its recorded native sources.',
        'parameters': {'type': 'object', 'properties': {'memory_id': {'type': 'string'}},
                       'required': ['memory_id'], 'additionalProperties': False},
    },
    {
        'name': 'memos_search',
        'description': 'Search the current MemOS cube for relevant memories and their native metadata.',
        'parameters': {'type': 'object', 'properties': {'query': {'type': 'string'}},
                       'required': ['query'], 'additionalProperties': False},
    },
)


class ScopedCube:
    def __init__(self, config):
        url = urlsplit(config['endpoint'])
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path:
            raise ValueError('MemOS OSS requires the run-owned loopback service')
        self.endpoint = url.geturl()
        self.user = str(UUID(config['user']))
        self.token = config['local_api_token']

    def post(self, route, **body):
        return self._request(route, {'user_id': self.user, **body})

    def _request(self, route, body=None):
        payload = None if body is None else json.dumps(body).encode()
        request = Request(self.endpoint + '/product/' + route, data=payload, headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=120) as response:
            result = json.load(response)
        if result.get('code') != 200:
            raise RuntimeError('MemOS OSS returned a native application error')
        return result['data']

    def scheduler(self):
        data = self._request('scheduler/allstatus')['scheduler_summary']
        return {key: data[key] for key in ('waiting', 'in_progress', 'pending', 'failed', 'cancelled')}

    def inventory(self, *, memory_id=None):
        native_filter = {'id': memory_id} if memory_id is not None else {'id': {'gt': ''}}
        data = self.post('get_memory', mem_cube_id=self.user, include_preference=False,
                         include_tool_memory=False, include_skill_memory=False, filter=native_filter)
        records = data['text_mem']
        if len(records) != 1 or records[0]['cube_id'] != self.user:
            raise ValueError('MemOS OSS inventory differs from the bound cube')
        rows = records[0]['memories']
        if len(rows) != records[0]['total_nodes']:
            raise ValueError('MemOS OSS inventory is incomplete')
        return rows

    def search(self, query, session):
        data = self.post('search', readable_cube_ids=[self.user], session_id=session, query=query)
        groups = data.get('text_mem', [])
        if any(g['cube_id'] != self.user for g in groups):
            raise ValueError('MemOS OSS search returned a different cube')
        return [row for group in groups for row in group['memories']]


class MemosOssProvider(MemoryProvider):
    name = 'memososs'

    def __init__(self):
        self.client = None
        self.session = None
        self.closed = False
        self.last_error = False
        self.capture_path = None

    def is_available(self):
        return bool(os.environ.get('PACBENCH_MEMOS_OSS_CONFIG'))

    def initialize(self, session_id, **kwargs):
        config_path = Path(kwargs['hermes_home']) / 'memos-oss.json'
        if config_path.resolve() != Path(os.environ['PACBENCH_MEMOS_OSS_CONFIG']).resolve():
            raise ValueError('MemOS OSS config belongs to another Hermes profile')
        self.client = ScopedCube(json.loads(config_path.read_text()))
        self.session = session_id
        self.capture_path = config_path.parent.parent/'memos-native-captures'
        self.capture_path.mkdir(mode=0o700,exist_ok=True)

    def prefetch(self, query, *, session_id=''):
        memories = self.client.search(query, session_id or self.session)
        return '\n'.join(row['memory'] for row in memories if row.get('memory'))

    def sync_turn(self, user_content, assistant_content, *, session_id='', **_):
        from pacbench.checkpoint_clock import descriptor

        session = session_id or self.session
        if self.closed or session != self.session:
            raise ValueError('MemOS OSS conversation changed its native session identity')
        current=descriptor(validate_clock=False)
        observed=datetime.now(ZoneInfo('America/New_York')).isoformat()
        receipt=self.capture_path/(current['nonce']+'.json')
        if receipt.exists():
            raise ValueError('MemOS OSS turn already has a native capture receipt')
        messages = [{'role': role, 'content': content, 'chat_time': observed} for role,content in
                    (('user',user_content),('assistant',assistant_content))]
        try:
            ack=self.client.post('add', writable_cube_ids=[self.client.user], session_id=session,
                                 async_mode='async', messages=messages)
            if not ack or any(r['cube_id']!=self.client.user or not r.get('memory_id')
                              or not isinstance(r.get('memory'),str) for r in ack):
                raise ValueError('MemOS OSS async capture returned no scoped native working memories')
            proof={'session_id':session,'at':current['at'],'cube_id':self.client.user,
                   'memory_ids':[r['memory_id'] for r in ack],
                   'memory_types':[r['memory_type'] for r in ack],
                   'question_sha256':sha256(user_content.encode()).hexdigest(),
                   'answer_sha256':sha256(assistant_content.encode()).hexdigest(),
                   'exact_user_in_ack':any(user_content in r['memory'] for r in ack),
                   'exact_assistant_in_ack':any(assistant_content in r['memory'] for r in ack)}
            data=json.dumps(proof,sort_keys=True).encode()
            fd=os.open(receipt,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            self.last_error = True
            raise

    def get_tool_schemas(self):
        return list(_TOOLS)

    def handle_tool_call(self, tool_name, args, **_):
        if tool_name == 'memos_search':
            return json.dumps({'memories': self.client.search(args['query'], self.session)}, ensure_ascii=False)
        if tool_name == 'memos_get':
            memory_id = str(UUID(args['memory_id']))
            rows = self.client.inventory(memory_id=memory_id)
            match = next((row for row in rows if row['id'] == memory_id), None)
            return json.dumps({'memory':match}, ensure_ascii=False)
        raise ValueError('Unexpected MemOS OSS tool')

    def shutdown(self):
        self.closed = True


def register(context):
    context.register_memory_provider(MemosOssProvider())
