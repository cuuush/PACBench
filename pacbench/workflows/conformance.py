"""Recording fakes for the workflow contract. Nothing in this module accesses a service."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from pacbench.corpus.r2 import compile_pizza_week
from pacbench.providers import PROVIDERS, eligibility
from pacbench.workflows.runner import Condition, run_compiled_workflow
from pacbench.workflows.spec import compile_prepared

from .delivery import submit_document


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

    def post(self, path, body, *, timeout):
        self.request = {'method':'post', 'path':path, 'json':body, 'upload':self.upload, 'timeout':timeout}


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
        native = submit_document(self.condition.provider,self.client,scope,doc)
        if self.condition.provider == 'reme':
            path = Path(native['resource'])
            request = {'method':'write-native-resource','filename':path.name,'date':path.parent.name,'body':path.read_text()}
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


def workflow_main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan','conform','preflight'))
    parser.add_argument('--state', choices=('continuing-account','isolated-control'), default='continuing-account')
    parser.add_argument('--track', choices=('nightly-document','first-party-connector'), required=True)
    parser.add_argument('--seven-daily-documents', action='store_true',
                        help='exercise the frozen one-document-per-creation-day layout offline')
    parser.add_argument('--prepared', type=Path, help='frozen preparation directory or week.json (default: Pizza Week)')
    parser.add_argument('--provider', choices=tuple(PROVIDERS))
    parser.add_argument('--output', type=Path, help='new private evidence directory for conformance')
    parser.add_argument('--site', type=Path, help='explicit authorized synthetic MemCal site for preflight')
    args = parser.parse_args(argv)
    if args.site and args.action != 'preflight':
        parser.error('--site is only used by preflight')
    if (args.action == 'conform') != (args.output is not None):
        parser.error('conform requires a new --output; plan writes only to stdout')
    if args.prepared and (args.action == 'preflight' or args.seven_daily_documents):
        parser.error('--prepared supports offline plan and conformance with its declared source layout')
    if args.seven_daily_documents and (
            args.action == 'preflight' or (args.track, args.state) != ('nightly-document', 'continuing-account')):
        parser.error('seven daily files require the continuing document plan or conformance')
    try:
        plan = (compile_prepared(args.prepared, args.state) if args.prepared
                else compile_pizza_week(args.state, seven_daily_documents=args.seven_daily_documents))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        parser.error(str(error))
    docs, turns, frozen = plan.documents, plan.turns, plan.source_basis
    if args.action == 'preflight':
        if (args.provider, args.track, args.state) != ('memcal', 'first-party-connector', 'continuing-account'):
            parser.error('preflight requires the continuing-account MemCal first-party connector')
        from pacbench.providers.memcal.source import source_preflight, verify_contract
        result = source_preflight(verify_contract(list(docs), list(turns), frozen), args.site)
        print(json.dumps(result, indent=2))
        return 0 if result['state'] == 'source-access-verified' else 2
    result = {'spec':plan.spec['version'],'spec_sha256':plan.spec_sha256,'judge':plan.spec['judge'],'source_basis':frozen,
              'track':args.track,'state':args.state,'nightly_files':len(docs),
              'cutoffs':sorted({d.cutoff for d in docs}),
              'turns_by_measure':dict(Counter(t.measure for t in turns)),
              'criteria_by_measure':dict(sum_claims(turns)),
              'prompt_caching':'unmeasured','providers':{}}
    if plan.layout is not None:
        result.update(source_layout=plan.layout['source_layout'],
                      document_index_sha256=plan.layout['normalized_index_sha256'])
    if plan.prepared:
        result['source_documents'] = result.pop('nightly_files')
        result['workflow_format'] = plan.spec['format']
    output_created = False
    performed_work = False
    for provider in ([args.provider] if args.provider else PROVIDERS):
        row = eligibility(provider,args.track,plan.channels)
        result['providers'][provider] = row
        covered = tuple(c for c in plan.channels if c not in row['missing_channels'])
        if args.action == 'plan':
            continue
        if not covered:
            row.update(result='conformance-unavailable', recorded_files=0, offline_recorded_turns=0,
                       reason=f'No documented {args.track} route for the generic offline conformance workflow. '
                              + row['boundary'])
            continue
        if not output_created:
            args.output.mkdir(parents=True,mode=0o700,exist_ok=False)
            output_created = True
        condition = Condition(provider,args.track,args.state,'recording frontend','recording sentinel',
                              'in-process','offline recording fake',PROVIDERS[provider].reviewed_version,
                              'no Hermes process','synthetic event clock',(),covered)
        account = RecordingAccount(condition, plan.documents_for(covered))
        try:
            scores = run_compiled_workflow(plan, condition, account, args.output/provider)
            row.update(offline_recorded_turns=sum(r['answered'] for r in scores.values()), recorded_files=len(account.files))
            if row['recorded_files'] or row['offline_recorded_turns']:
                row['result'] = 'offline-conformance' if covered == plan.channels else 'channel-only import conformance'
                performed_work = True
            else:
                row.update(result='conformance-unavailable', reason='Conformance recorded no source files or turns.')
        finally:
            account.close()
    print(json.dumps(result,indent=2))
    return 2 if args.action == 'conform' and not performed_work else 0


def sum_claims(turns):
    counts = Counter()
    for turn in turns:
        counts[turn.measure] += len(turn.claims)
    return counts
