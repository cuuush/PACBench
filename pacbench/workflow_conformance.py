"""Recording fakes for the workflow contract. Nothing in this module accesses a service."""
import copy
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid

from .workflow_delivery import submit_document


class RecordingClient:
    def __init__(self):
        self.request = None
        self.upload = None

    def add(self, *args, **kwargs):
        self.request = {'method':'add', 'args':args, 'kwargs':kwargs}

    def remember_permanent(self, **kwargs):
        self.request = {'method':'remember_permanent', 'kwargs':kwargs}

    def retain_files(self, bank_id, files, files_metadata):
        path, = files
        self.request = {'method':'retain_files','scope':bank_id,'name':path.name,
                        'body':path.read_text(),'metadata':files_metadata}

    def upload_temp_file(self, path):
        self.upload = {'filename':path.name, 'body':path.read_text()}
        return 'recorded-upload'

    def post(self, path, body):
        self.request = {'method':'post', 'path':path, 'json':body, 'upload':self.upload}


class RecordingAccount:
    evidence_kind = 'recording-fake'

    def __init__(self, condition, upstream=()):
        self.condition = condition
        self.identity = uuid.uuid4().hex
        self.files, self.chats, self.native_writes = [], {}, []
        self.upstream = tuple(upstream)
        self.client = RecordingClient()
        self.root = TemporaryDirectory()
        self.gateway = uuid.uuid4().hex
        self.pending = None
        self.pending_documents = {}

    def verify_condition(self, condition):
        if self.condition != condition:
            raise ValueError('recording condition mismatch')
        return {'account':self.identity,'condition':vars(condition),'store_identity':self.identity,'surface':'recording-fake'}

    def advance(self, at):
        return {'cutoff':at,'service_at':at, 'as_of':at, 'clock':'recording-fake'}

    def submit(self, doc):
        scope = self.root.name if self.condition.provider == 'reme' else self.identity
        submit_document(self.condition.provider,self.client,scope,doc)
        if self.condition.provider == 'reme':
            path = Path(scope)/'resource'/doc.filename
            request = {'method':'write-native-resource','filename':path.name,'body':path.read_text()}
        else:
            request = self.client.request
        # Recover what actually crossed the simulated native interface.
        provider = self.condition.provider
        if provider == 'mem0':
            body = request['args'][0]
        elif provider == 'supermemory':
            body = request['kwargs']['content']
        elif provider == 'cognee':
            body = request['kwargs']['text']
        elif provider == 'openviking':
            body = request['upload']['body']
        else:
            body = request['body']
        self.pending_documents[doc.filename] = body
        return {'native_id':doc.filename, 'accepted':True,'at':doc.cutoff,'request':request}

    def readback(self, doc, acceptance):
        name = acceptance['native_id']
        body = self.pending_documents.pop(name)
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.files.append(name)
        return {'at':doc.cutoff,'native_id':name,'ready':True,
                'submitted_sha256':digest,'readback':{'recording_store_sha256':digest}}

    def sync(self, cutoff, channels):
        # The mock upstream advances its own cursor. The sync call only specifies
        # channels and an as-of cutoff; the benchmark never injects formatted texts.
        self.pending = [d for d in self.upstream if d.cutoff == cutoff and d.channel in channels]
        return {'at':cutoff,'native_id':cutoff,'accepted':True,
                'request':{'sync_through':cutoff,'channels':channels}}

    def sync_readback(self, acceptance):
        self.files.extend(d.filename for d in self.pending)
        return {'at':acceptance['at'],'native_id':acceptance['native_id'],'ready':True,
                'readback':{'recorded_upstream_items':len(self.pending)},
                'new_source_ids':[sid for d in self.pending for sid in d.source_ids],
                'source_cursor':acceptance['at'],
                'channel_readback':{channel:{'cursor':acceptance['at'], 'as_of':acceptance['at'],
                    'new_source_ids':[sid for d in self.pending if d.channel == channel for sid in d.source_ids]}
                    for channel in self.condition.channels}}

    def isolated_copy(self):
        if self.native_writes:
            raise ValueError('control base has previous assistant writes')
        other = RecordingAccount(self.condition)
        other.files = list(self.files)
        return other

    def control_readback(self):
        return {'account':self.identity, 'source_files':list(self.files), 'chat_count':len(self.chats),
                'conversation_writes':len(self.native_writes), 'readback':'recording-store'}

    def source_inventory(self):
        return list(self.files)

    def ask(self, prompt, *, at, session_id):
        if session_id is None:
            session_id = uuid.uuid4().hex
            self.chats[session_id] = []
        records = self.chats[session_id]
        recalled = self.native_writes[-1]['user'] if self.native_writes else ''
        wire = prompt+'\n<memory-context>'+recalled+'</memory-context>' if recalled else prompt
        records.append({'role':'user','content':prompt,'api_content':wire})
        requests = [{'model':self.condition.model, 'request_id':uuid.uuid4().hex,
                     'tools':list(self.condition.tool_names),
                     'messages':[{'role':r['role'],'content':r.get('api_content') or r['content']} for r in records]}]
        answer = 'Recording acknowledgement'
        records.append({'role':'assistant','content':answer})
        self.native_writes.append({'user':prompt,'assistant':answer,'session':session_id})
        return {'session_id':session_id,'history':copy.deepcopy(records),'answer':answer,
                'gateway_identity':self.gateway,'account':self.identity,'question_at':at,
                'write_barrier':{'session_id':session_id, 'pending_writes':0, 'readback':len(self.native_writes)},'source_files':list(self.files),
                'offered_tools':list(self.condition.tool_names),'tool_calls':[],'tool_results':[],
                'model_requests':requests,'prefetch':recalled,'attempts':[]}

    def end_chat(self, session_id):
        history = self.chats.pop(session_id)
        return {'session_id':session_id,'pending_writes':0,'readback':len(history)}

    def close(self):
        self.root.cleanup()
