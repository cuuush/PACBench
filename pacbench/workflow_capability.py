"""Bind paid v2 calls to one fresh account, its frozen schedule and its durable workflow."""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from itertools import islice
import hashlib
import json
import os
from pathlib import Path

from .canary_capability import binding, digest, read, sha
from .live_dispatch import process_identity
from .workflow_spec import SPEC, compile_week, instant
from .provider_versions import MEMCAL_REVISION, MEMCAL_WEEK_REVISION, MEMCAL_WEEK_VERSION, memcal_week_pin
from .workflow_memcal_tools import manifest, verify_driver_schemas
from .workflow_tracks import memcal_week_tools, HERMES_TOOLS, MEMOS_OSS_LUNA_COMPAT, MEMOS_OSS_TOOLS, MEMOS_OSS_VERSION, validate_native_deployment

ENV = 'PACBENCH_WORKFLOW_SCOPE'
PROFILE = 'luna-bedrock-global'
MEMOS_OSS_ADMISSION = 'memos-oss-native-file-document-week-v1'
SM_QUESTION_ADMISSION = 'supermemory-stock-first-question-v1'
SM_WEEK_ADMISSION = 'supermemory-stock-document-week-v1'


@lru_cache(maxsize=2)
def schedule(spec_sha, canary):
    if spec_sha != sha(SPEC):
        raise ValueError('workflow spec changed')
    _, docs, turns, frozen = compile_week('continuing-account')
    if canary:
        turns = turns[:4]  # Through the first same-session continuation.
        docs = [doc for doc in docs if instant(doc.cutoff) <= instant(turns[-1].at)]
    return docs, turns, frozen


def create(root, provider, *, canary, prepaid, auto_recharge_evidence, qualification=None):
    root = Path(root).resolve()
    if root.name != provider or provider not in ('reme', 'mem0', 'hindsight', 'openviking','cognee','memos'):
        raise ValueError('no qualified continuing-account binding for this provider')
    if provider == 'mem0' and not canary and qualification is None:
        raise ValueError('Mem0 Week requires a separately verified live canary')
    if provider == 'hindsight' and not canary and qualification is None:
        raise ValueError('Hindsight Week requires a separately verified live canary')
    if provider == 'openviking' and not canary and qualification is None:
        raise ValueError('OpenViking Week requires a separately verified live canary')
    if provider == 'reme' and not canary and qualification is None:
        raise ValueError('ReMe Week requires a separately verified live canary')
    if provider == 'cognee' and not canary and qualification is None:
        raise ValueError('Cognee Week requires a separately verified live canary')
    if provider == 'memos' and not canary and qualification is None:
        raise ValueError('MemOS OSS Week requires a separately verified live canary')
    if qualification is not None and (provider not in ('mem0','hindsight','openviking','reme','cognee','memos') or canary is not False):
        raise ValueError('unexpected live qualification binding')
    if qualification is not None:
        if provider == 'mem0':
            from .workflow_mem0 import check_canary
        elif provider == 'hindsight':
            from .workflow_hindsight import check_canary
        elif provider == 'openviking':
            from .workflow_openviking import check_canary
        elif provider == 'reme':
            from .workflow_reme import check_canary
        elif provider == 'memos':
            from .workflow_memos_oss import check_canary
        else:
            from .workflow_cognee import check_canary
        canary_root = Path(qualification['result']['path']).parent
        if canary_root == root or check_canary(canary_root) != qualification:
            raise ValueError('Week requires the exact retained canary bindings')
    spec_sha = sha(SPEC)
    _, _, frozen = schedule(spec_sha, canary)
    prefix = 'pacbench-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '-' + provider
    roles = {role:prefix+'-'+role for role in ('memory','driver','judge')}
    record = dict(protocol='pizza-week-conversation-v2', run_id=root.parent.name, root=str(root),
                  provider=provider, controller=process_identity(), projects={provider:roles}, spec_sha256=spec_sha, frozen_inputs=frozen,
                  canary=canary, prepaid=prepaid, auto_recharge_evidence=auto_recharge_evidence)
    if provider == 'mem0':
        record['admission'] = 'mem0-native-oss-document-week-v1'
        if qualification is not None:
            record['qualification'] = qualification
    if provider == 'hindsight':
        record['admission'] = 'hindsight-native-file-document-week-v1'
        if qualification is not None:
            record['qualification'] = qualification
    if provider == 'openviking':
        record['admission'] = 'openviking-native-file-document-week-v1'
        if qualification is not None:
            record['qualification'] = qualification
    if provider == 'reme':
        record['admission'] = 'reme-native-watched-resource-week-v1'
        if qualification is not None:
            record['qualification'] = qualification
    if provider == 'cognee':
        record['admission'] = 'cognee-native-permanent-document-week-v1'
        if qualification is not None:
            record['qualification'] = qualification
    if provider == 'memos':
        record['admission'] = MEMOS_OSS_ADMISSION
        if qualification is not None:
            record['qualification'] = qualification
    path = root/'workflow-scope.json'
    with path.open('x') as stream:
        json.dump(record, stream, sort_keys=True)
    return binding(path)


def load():
    bound = json.loads(os.environ[ENV])
    c = read(bound)
    root = Path(c['root'])
    if c.get('admission') == 'supermemory-stock-source-qualification-v1':
        from .supermemory_source_admission import validate
        validate(c, bound)
        return c
    if c.get('admission') == SM_QUESTION_ADMISSION:
        from .supermemory_question_admission import validate
        validate(c, bound)
        return c
    if c.get('admission') == SM_WEEK_ADMISSION:
        from .supermemory_week_admission import validate
        validate(c, bound)
        return c
    if c.get('admission') == 'pizza-day-native-document-v1':
        from .pizza_day_lineup import validate
        validate(c, bound)
        return c
    if c.get('admission') == 'luna-judge-development-ab-v1':
        from .judge_development_live import validate
        validate(c, bound)
        return c
    if c.get('admission') == 'pizza-week-luna-explicit-overlay-v1':
        from .cohort_regrade_live import validate
        validate(c, bound)
        return c
    if c.get('admission') == 'pizza-week-honcho-authored-chats-luna-v4-v1':
        from .honcho_regrade_live import validate
        validate(c, bound)
        return c
    if c.get('admission') in ('pizza-week-question-contract-development-shadow-v1',
                              'pizza-week-question-contract-span-regression-v2'):
        from .question_shadow_live import validate
        validate(c, bound)
        return c
    if c.get('admission') == 'luna-judge-development-challenge-v2':
        from .judge_calibration_live import validate
        validate(c, bound)
        return c
    if c.get('admission') == 'pizza-day-wrong-answer-judge-v1':
        from .pizza_day_control import validate
        validate(c, bound)
        return c
    if c.get('admission') == 'pizza-day-synthetic-memcal-v1':
        from .pizza_day_controller import validate_scope
        validate_scope(c, bound)
        return c
    if c.get('admission') == 'honcho-seven-daily-documents-v1':
        from .honcho_daily_gate import validate
        validate(c, bound)
        return c
    if (root != root.resolve() or root/'workflow-scope.json' != Path(bound['path'])
            or c['run_id'] != root.parent.name or c['provider'] != root.name
            or c['provider'] not in ('reme','memcal','mem0','hindsight','openviking','cognee','memos') or c['protocol'] != 'pizza-week-conversation-v2'
            or type(c['canary']) is not bool or c['spec_sha256'] != sha(SPEC)
            or c['frozen_inputs'] != schedule(c['spec_sha256'], c['canary'])[2]):
        raise ValueError('unbound v2 account or dataset')
    if c['provider'] == 'mem0' and (c.get('admission') != 'mem0-native-oss-document-week-v1'
            or c['projects'] != _mem0_projects(root)):
        raise ValueError('unbound native Mem0 continuing-account canary scope')
    if c['provider'] in ('mem0','hindsight','openviking','reme','cognee','memos') and not c['canary']:
        qualification = c['qualification']
        result = read(qualification['result'])
        canary = read(qualification['scope'])
        canary_root = Path(canary['root'])
        qualified = (result['state'],result['cleanup'])==('live-canary-complete','complete')
        if c['provider']=='hindsight' and qualification.get('cleanup'):
            cleanup = read(qualification['cleanup'])
            qualified = (result['state']=='incomplete' and result['cleanup']=='unverified'
                         and Path(qualification['cleanup']['path'])==canary_root/'service-shutdown-reconciliation.json'
                         and cleanup['state']=='verified-after-exit' and cleanup['result']==qualification['result'])
        if (not result['canary'] or not qualified
                or Path(qualification['scope']['path'])!=canary_root/'workflow-scope.json'
                or Path(qualification['result']['path'])!=canary_root/'result.json'
                or canary_root==root or canary['provider']!=c['provider'] or not canary['canary']
                or canary['frozen_inputs']!=c['frozen_inputs'] or canary['spec_sha256']!=c['spec_sha256']):
            raise ValueError('Week does not bind a distinct completed canary')
    if c['provider']=='hindsight' and (c.get('admission')!='hindsight-native-file-document-week-v1'
            or c['projects']!=_document_projects(root,'hindsight')):
        raise ValueError('unbound native Hindsight continuing-account scope')
    if c['provider']=='openviking' and (c.get('admission')!='openviking-native-file-document-week-v1'
            or c['projects']!=_document_projects(root,'openviking')):
        raise ValueError('unbound native OpenViking continuing-account scope')
    if c['provider']=='reme' and (c.get('admission')!='reme-native-watched-resource-week-v1'
            or c['projects']!=_document_projects(root,'reme')):
        raise ValueError('unbound native ReMe continuing-account scope')
    if c['provider']=='cognee' and (c.get('admission')!='cognee-native-permanent-document-week-v1'
            or c['projects']!=_document_projects(root,'cognee')):
        raise ValueError('unbound native Cognee continuing-account scope')
    if c['provider']=='memos' and (c.get('admission')!=MEMOS_OSS_ADMISSION
            or c['projects']!=_document_projects(root,'memos')):
        raise ValueError('unbound MemoryOS OSS document scope')
    if c['provider'] == 'memcal':
        contract, plan, *_ = _memcal_schedule(c['spec_sha256'], c['canary'])
        if (c.get('admission') != MEMCAL_SCOPE or c.get('contract') != contract or c.get('plan_sha256') != plan
                or c['projects'] != _projects(root)):
            raise ValueError('MemCal capability differs from the frozen release or role binding')
    return c


def confirm_setting(record):
    if (record.get('auto_recharge') != 'off' or not isinstance(record.get('confirmed_by'), str)
            or not record['confirmed_by'].strip() or not isinstance(record.get('confirmed_at'), str)
            or datetime.fromisoformat(record['confirmed_at']).utcoffset() is None):
        raise ValueError('a dated operator observation of Auto Recharge off is required')


def budget(*, project=None, profile=None):
    from .observational_receipts import ENV as JOURNAL
    from .observational_receipts import RunReceiptJournal
    c = load()
    root = Path(c['root'])
    path = Path(os.environ[JOURNAL])
    verification = root/'prepaid-verification'
    balance = verification/(c['prepaid']['receipt_sha256']+'.private.json')
    recharge = read(c['auto_recharge_evidence'])
    confirm_setting(recharge)
    if (path != root/'receipts.jsonl' or path.is_symlink() or balance.resolve().parent != verification
            or sha(balance) != c['prepaid']['receipt_sha256'] or c['prepaid']['source'] != 'OpenRouter GET /api/v1/credits'
            or profile not in (None, PROFILE) or os.environ.get('PACBENCH_OPENROUTER_PROFILE') != PROFILE
            or project is not None and project not in c['projects'][c['provider']].values()):
        raise ValueError('workflow receipt scope, billing confirmation or model route differs')
    data = json.loads(balance.read_bytes(), parse_float=Decimal)['data']
    observed = Decimal(str(data['total_credits'])) - Decimal(str(data['total_usage']))
    if observed != Decimal(c['prepaid']['remaining_usd']):
        raise ValueError('prepaid receipt and recorded observation differ')
    if observed <= 0:
        raise ValueError('OpenRouter prepaid credit exhausted')
    if c.get('admission') == 'luna-judge-development-ab-v1':
        from .judge_development_live import JudgeJournal
        return JudgeJournal(path)
    if c.get('admission') in ('pizza-week-luna-explicit-overlay-v1', 'pizza-week-honcho-authored-chats-luna-v4-v1'):
        from .cohort_regrade_live import OverlayJournal
        return OverlayJournal(path)
    if c.get('admission') == 'supermemory-stock-source-qualification-v1':
        from .supermemory_source_admission import SourceJournal
        return SourceJournal(path)
    if c.get('admission') == SM_QUESTION_ADMISSION:
        from .supermemory_question_admission import QuestionJournal
        return QuestionJournal(path)
    if c.get('admission') == SM_WEEK_ADMISSION:
        from .supermemory_week_admission import WeekJournal
        return WeekJournal(path)
    if c.get('admission') == 'pizza-week-question-contract-development-shadow-v1':
        from .question_shadow_live import ShadowJournal
        return ShadowJournal(path)
    if c.get('admission') == 'pizza-week-question-contract-span-regression-v2':
        from .question_shadow_live import SpanJournal
        return SpanJournal(path)
    if c.get('admission') == 'luna-judge-development-challenge-v2':
        from .judge_calibration_live import ChatJournal
        return ChatJournal(path)
    if c['provider'] == 'memcal':
        from .observational_receipts import IndexedRunReceiptJournal
        return IndexedRunReceiptJournal(path)
    return RunReceiptJournal(path)


def _memcal_revision(scope):
    if scope['admission'] == 'pizza-day-synthetic-memcal-v1':
        return MEMCAL_REVISION
    return scope['access']['memcal_revision']


def owner(env, home, checkpoint, provider, clock):
    c = load()
    if (env.get(ENV) != os.environ[ENV] or provider != c['provider'] or home != Path(c['root'])/'hermes-home'
            or not clock or checkpoint != c['spec_sha256'] or env.get('PACBENCH_WORKFLOW_CAPTURE') != c['protocol']
            or env.get('PACBENCH_PROVIDER_ONLY') != '1' or env.get('PACBENCH_API_INPUT_TRACE') != str(home.parent/'api-inputs.jsonl')):
        raise ValueError('workflow gateway differs from scoped home, clock or capture')
    if provider == 'memcal':
        revision = _memcal_revision(c)
        label, selected_revision, _ = memcal_week_pin()
        if (revision != selected_revision or env.get('PACBENCH_MEMCAL_CONDITION') != label
                or env.get('PACBENCH_TOOL_ALLOWLIST') != ','.join(memcal_week_tools(revision))
                or env.get('PACBENCH_OPENROUTER_PROFILE') != PROFILE
                or env.get('PACBENCH_ROLE_ROUTES') != 'luna-all-roles-v1'
                or env.get('PACBENCH_OFFLINE_CONFORMANCE') or os.environ.get('PACBENCH_JUDGE_OUTPUT_RECOVERY')
                or process_identity() != c['controller']):
            raise ValueError('MemCal gateway lacks the bound owner, Luna route or exclusive native tool set')
    tools = MEMOS_OSS_TOOLS if provider == 'memos' else HERMES_TOOLS[provider]
    if provider == 'honcho' and c['admission'] == 'honcho-seven-daily-documents-v1' and (
            not (c.get('first_question') or c.get('week_schedule')) or process_identity() != c['controller']
            or env.get('PACBENCH_TOOL_ALLOWLIST') != ','.join(tools)
            or env.get('PACBENCH_OPENROUTER_PROFILE') != PROFILE or env.get('PACBENCH_OFFLINE_CONFORMANCE')):
        raise ValueError('Honcho gateway requires its scoped controller, stock toolkit and Luna route')
    if provider in ('mem0','hindsight','openviking','reme','cognee','memos') and (
            env.get('PACBENCH_TOOL_ALLOWLIST') != ','.join(tools)
            or env.get('PACBENCH_OPENROUTER_PROFILE') != PROFILE
            or env.get('PACBENCH_ROLE_ROUTES') != 'luna-all-roles-v1'
            or env.get('PACBENCH_OFFLINE_CONFORMANCE') or process_identity() != c['controller']):
        raise ValueError('Document gateway lacks the bound owner, Luna route or exclusive native tool set')
    if c.get('admission') in (SM_QUESTION_ADMISSION, SM_WEEK_ADMISSION):
        if (env.get('PACBENCH_TOOL_ALLOWLIST') != ','.join(tools)
                or env.get('PACBENCH_OPENROUTER_PROFILE') != PROFILE
                or env.get('PACBENCH_ROLE_ROUTES') != 'luna-all-roles-v1'
                or env.get('PACBENCH_OFFLINE_CONFORMANCE') or process_identity() != c['controller']):
            raise ValueError('Supermemory gateway lacks its exact owner, route or stock tools')
        if c['admission'] == SM_QUESTION_ADMISSION:
            from .supermemory_question_admission import ready
            ready(c)
    if provider == 'memos' and env.get('PACBENCH_MEMOS_OSS_CONFIG') != str(home/'memos-oss.json'):
        raise ValueError('MemOS OSS gateway does not bind its run-owned configuration')
    if provider == 'openviking' and env.get('PACBENCH_SOURCE_ARGUMENT_RULES') != '{}':
        raise ValueError('OpenViking continuing-account gateway must retain supported native source reads')
    if provider=='cognee' and (env.get('PACBENCH_COGNEE_PHYSICAL_EXIT_WATCHER')!='1'
            or env.get('COGNEE_IMPROVE_BACKGROUND')!='false'):
        raise ValueError('Cognee needs a physical native exit watcher and in-process session promotion')


def admission(project, profile, body):
    from .workflow_runner import Condition

    c = load()
    provider = c['provider']
    roles = c['projects'][provider]
    if project not in roles.values() or profile != PROFILE or not isinstance(body, dict):
        raise ValueError('unexpected workflow role or model route')
    if c.get('admission') == 'supermemory-stock-source-qualification-v1':
        from .supermemory_source_admission import admit
        return admit(c, project, body)
    if c.get('admission') == SM_QUESTION_ADMISSION:
        from .supermemory_question_admission import admit
        return admit(c, project, body)
    if c.get('admission') == SM_WEEK_ADMISSION:
        from .supermemory_week_admission import admit
        return admit(c, project, body)
    if c.get('admission') == 'pizza-day-native-document-v1':
        from .pizza_day_lineup import admit
        return admit(c, project, body)
    if c.get('admission') == 'luna-judge-development-ab-v1':
        from .judge_development_live import admit
        return admit(c, project, body)
    if c.get('admission') == 'pizza-week-luna-explicit-overlay-v1':
        from .cohort_regrade_live import admit
        return admit(c, project, body)
    if c.get('admission') == 'pizza-week-honcho-authored-chats-luna-v4-v1':
        from .honcho_regrade_live import admit
        return admit(c, project, body)
    if c.get('admission') in ('pizza-week-question-contract-development-shadow-v1',
                              'pizza-week-question-contract-span-regression-v2'):
        from .question_shadow_live import admit
        return admit(c, project, body)
    if c.get('admission') == 'luna-judge-development-challenge-v2':
        from .judge_calibration_live import admit
        return admit(c, project, body)
    if c.get('admission') == 'pizza-day-wrong-answer-judge-v1':
        from .pizza_day_control import admit
        return admit(c, project, body)
    if c.get('admission') == 'honcho-seven-daily-documents-v1':
        from .honcho_daily_gate import admission as honcho_admission
        return honcho_admission(c, project, profile, body)
    if provider == 'memcal':
        return _memcal_admission(c, project, body)
    if provider in ('mem0','hindsight','openviking','reme','cognee','memos'):
        from .openrouter_route import validate_request
        validate_request(body, profile=PROFILE)
    root = Path(c['root'])
    path = root/'workflow/workflow.jsonl'
    if path != path.resolve():
        raise ValueError('unexpected workflow journal location')
    # Judge workers may read a verified SQLite checkpoint while native work
    # advances independently. The legacy event-based lanes keep their chronology.
    from .workflow_grading import claimed_request
    checkpoint = claimed_request(c, project, body)
    with path.open() as stream:
        lines = islice(stream, 3) if checkpoint is not None else stream
        events = [json.loads(line) for line in lines]
    spec, deployment = events[:2]
    if (spec['phase'] != 'spec' or spec['sha256'] != c['spec_sha256'] or spec['source_basis'] != c['frozen_inputs']
            or deployment['phase'] != 'condition' or deployment['evidence_kind'] != 'native'):
        raise ValueError('unverified workflow or native account')
    condition = Condition(**deployment['condition'])
    record = deployment['deployment_witness']
    validate_native_deployment(condition, record)
    store = {'reme':'reme-workspace','mem0':'mem0-state','hindsight':'fallback-home',
             'openviking':'openviking-data','cognee':'cognee-data','memos':'memos-oss-service'}[provider]
    if (condition.provider != provider or condition.track != 'nightly-document'
            or condition.state != 'continuing-account'
            or provider == 'memos' and condition.version != MEMOS_OSS_VERSION
            or record['store_identity'] != str(root/store)):
        raise ValueError('deployment or native store differs')
    initial = events[2] if len(events) > 2 else {}
    witness = initial.get('evidence', {})
    if (record['account'] != str(root) or initial.get('phase') != 'account-readback'
            or initial.get('question') is not None or witness.get('account') != str(root)
            or not witness.get('readback') or witness.get('chat_count') != 0
            or witness.get('conversation_writes') != 0 or witness.get('source_files') != []):
        raise ValueError('missing fresh native account readback')
    if checkpoint is not None:
        return dict(root=c['root'], capability=json.loads(os.environ[ENV]), run_id=c['run_id'],
                    slot=checkpoint, projects=[project])
    role = next(role for role, name in roles.items() if name == project)
    if provider=='memos' and role=='memory' and (
            os.environ.get('PACBENCH_MEMOS_OSS_LUNA_COMPAT')!=MEMOS_OSS_LUNA_COMPAT or 'top_p' in body):
        raise ValueError('MemOS OSS native request lacks Luna sampling compatibility')
    senders = [c['controller'] if role == 'judge' else record['native_processes'][role]]
    if provider == 'mem0' and role == 'memory':
        # The stock SDK importer lives in the controller. Hermes invokes the
        # same provider backend in its own process during recall and writeback.
        senders.append(record['native_processes']['driver'])
    if process_identity() not in senders:
        raise ValueError('sender differs from run-owned native process')
    docs, turns, _ = schedule(c['spec_sha256'], c['canary'])
    completed = [r for r in events if r['phase']=='readback']
    ready = [r['evidence']['submitted_sha256'] for r in completed if r['evidence'].get('ready') is True and r['evidence'].get('readback')]
    if len(ready) != len(completed) or ready != [d.sha256 for d in docs[:len(ready)]]:
        raise ValueError('previous native document readbacks incomplete')
    event = events[-1]
    if event['phase'] in ('submit','acceptance') and project == roles['memory']:
        doc = docs[len(ready)]
        submit = next(r for r in reversed(events) if r['phase']=='submit')
        if (submit['filename'], submit['content'], submit['sha256'], submit['at']) != (doc.filename, doc.text, doc.sha256, doc.cutoff):
            raise ValueError('native submission differs from current nightly unit')
        if provider == 'mem0':
            all_documents, _, _ = schedule(c['spec_sha256'], False)
            if process_identity() != c['controller'] or not mem0_import_request(
                    body, doc, all_documents, docs[:len(ready)+1]):
                raise ValueError('Mem0 import request lacks its exact released document')
        if provider in ('hindsight','openviking','reme','cognee','memos'):
            from .workflow_runner import inspect_source_cutoff
            all_documents, _, _ = schedule(c['spec_sha256'],False)
            inspect_source_cutoff({'model_requests':[body],'prefetch':None}, all_documents,
                                  [d.filename for d in docs[:len(ready)+1]], [])
        slot = dict(kind='ingest', batch_id=doc.filename)
    elif event['phase']=='dispatch' and project in (roles['driver'], roles['memory']):
        seen = [r['question'] for r in events if r['phase']=='verified-turn']
        turn = turns[len(seen)]
        inventory = events[-2]
        prefix = [d for d in docs if instant(d.cutoff)<=instant(turn.at)]
        if (seen != [t.id for t in turns[:len(seen)]] or event['question'] != turn.id or event['prompt'] != turn.prompt
                or event['at'] != turn.at or inventory['phase'] != 'pre-dispatch-inventory'
                or inventory['account'] != record['account'] or event['account'] != record['account']
                or inventory['question'] != turn.id or inventory['source_files'] != [d.filename for d in prefix]
                or ready != [d.sha256 for d in prefix]):
            raise ValueError('answer outside verified source prefix or schedule')
        if provider == 'mem0' and process_identity() != record['native_processes']['driver']:
            raise ValueError('Mem0 answer-stage request came from outside the warm gateway')
        if project == roles['driver']:
            users = [m for m in body['messages'] if m['role']=='user']
            if (not users or not isinstance(users[-1].get('content'), str) or
                    not (users[-1]['content'] == turn.prompt or users[-1]['content'].startswith(turn.prompt+'\n\n')) or
                    sorted(t['function']['name'] for t in body.get('tools', [])) != sorted(condition.tool_names)):
                raise ValueError('driver question or tool surface differs')
        slot = dict(kind='answer', question_id=turn.id)
    elif event['phase']=='chat-close-started' and project == roles['memory']:
        question = event['question']
        visible, verified = events[-3:-1]
        if (verified.get('phase') != 'verified-turn' or visible.get('phase') != 'answer-visible'
                or verified.get('question') != question or visible.get('question') != question
                or visible['surface']['session_id'] != event['session_id']):
            raise ValueError('native close differs from the verified session')
        if provider == 'mem0' and process_identity() != record['native_processes']['driver']:
            raise ValueError('Mem0 cleanup-stage request came from outside the warm gateway')
        slot = dict(kind='cleanup', question_id=question)
    elif event['phase']=='judge-request' and project == roles['judge']:
        if (event['question'] not in {r['question'] for r in events if r['phase']=='verified-turn'}
                or len(body['messages']) != 1 or body['messages'][0]['role'] != 'user'
                or digest(body['messages'][0]['content']) != event['prompt_sha256']):
            raise ValueError('judge request differs from verified turn and current rubric')
        slot = dict(kind='judge', question_id=event['question'])
    else:
        raise ValueError('workflow stage does not permit this request')
    if provider in ('mem0','hindsight','openviking','reme','cognee','memos'):
        slot['journal_stage_sha256'] = digest(event)
    if provider == 'hindsight' and role == 'memory':
        work = hindsight_consolidation_call(root)
        if work is not None:
            slot['native_work'] = work
    return dict(root=c['root'], capability=json.loads(os.environ[ENV]), run_id=c['run_id'],
                slot={**slot,'provider':provider},projects=[project])


def _mem0_projects(root):
    return _document_projects(root,'mem0')


def _document_projects(root,provider):
    prefix = 'pacbench-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '-' + provider + '-'
    return {provider:{role:prefix+role for role in ('memory','driver','judge')}}


def mem0_import_request(body, document, all_documents, released):
    from mem0.configs.prompts import ADDITIVE_EXTRACTION_PROMPT
    from mem0.memory.utils import parse_messages
    from .workflow_runner import inspect_source_cutoff

    messages = body.get('messages', [])
    if (len(messages) != 2 or messages[0].get('role') != 'system'
            or messages[0].get('content') != ADDITIVE_EXTRACTION_PROMPT or messages[1].get('role') != 'user'
            or not isinstance(messages[1].get('content'), str) or body.get('tools')):
        return False
    current = parse_messages([{'role':'user','content':document.text}])
    prompt = messages[1]['content']
    _, marker, tail = prompt.partition('\n\n## New Messages\n')
    observed, boundary, _ = tail.partition('\n\n## Observation Date\n')
    if (not marker or not boundary or observed != current
            or not prompt.startswith('## Summary\n\n\n## Last k Messages\n')
            or '\n\n## Custom Instructions\n' in prompt):
        return False
    try:
        inspect_source_cutoff({'model_requests':[body], 'prefetch':None}, all_documents,
                              [doc.filename for doc in released], [])
    except ValueError:
        return False
    return True


def confirm_document_sender(row):
    scope = load()
    bound = json.loads(os.environ[ENV])
    root = Path(scope['root'])
    journal = root/'workflow/workflow.jsonl'
    if (scope['provider'] not in ('mem0','hindsight','openviking','reme','cognee','memos') or scope['provider']!=row['workflow_slot']['provider']
            or row['workflow_root']!=str(root)
            or row['workflow_capability']!=bound or journal!=journal.resolve()):
        raise ValueError('Document dispatch no longer belongs to its active scope')
    if scope['provider']=='memos' and row['accounting_role']=='memory_provider' and (
            os.environ.get('PACBENCH_MEMOS_OSS_LUNA_COMPAT')!=MEMOS_OSS_LUNA_COMPAT or
            row.get('native_request_compatibility')!=MEMOS_OSS_LUNA_COMPAT):
        raise ValueError('MemOS OSS sampling compatibility changed before dispatch')
    with journal.open() as stream:
        last = None
        for last in stream:
            pass
    if last is None or digest(json.loads(last)) != row['workflow_slot']['journal_stage_sha256']:
        raise ValueError('Document workflow stage changed before dispatch')

    if scope['provider']=='hindsight' and row['accounting_role']=='memory_provider':
        if hindsight_consolidation_call(root)!=row['workflow_slot'].get('native_work'):
            raise ValueError('Hindsight native consolidation boundary changed before dispatch')


def hindsight_consolidation_call(root):
    from .workflow_hindsight_calls import native_consolidation_call

    bank = json.loads((root/'hermes-home/hindsight/config.json').read_text())['bank_id']
    return native_consolidation_call(bank)


MEMCAL_SCOPE = 'memcal-source-operation-v1'


def _projects(root):
    prefix = 'pacbench-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '-memcal-'
    return {'memcal': {role: prefix + role for role in ('memory', 'driver', 'judge')}}


@lru_cache(maxsize=2)
def _memcal_schedule(spec_sha, canary):
    from .workflow_memcal_contract import verify_contract
    from .workflow_source_plan import _encode
    full_docs, full_turns, frozen = schedule(spec_sha, False)
    verified = verify_contract(full_docs, full_turns, frozen)
    docs, turns, _ = schedule(spec_sha, canary)
    plan = hashlib.sha256(b'pacbench-source-plan-v1\n')
    for kind, entries in [('documents', docs), ('turns', turns), ('all_source_documents', full_docs)]:
        plan.update(kind.encode() + b'\n')
        for obj in entries:
            plan.update(_encode(obj).encode() + b'\n')
    releases = {}
    for doc in docs:
        night = releases.setdefault(doc.cutoff, {})
        for sid, body in zip(doc.source_ids, doc.bodies, strict=True):
            night[sid] = hashlib.sha256(body.encode()).hexdigest()
    by_id = {}
    latest = {}
    for index, turn in enumerate(turns):
        cutoff = max((at for at in releases if instant(at) <= instant(turn.at)), default=None)
        by_id[turn.id] = index, turn, cutoff
        latest[turn.session] = turn.id
    return verified, plan.hexdigest(), releases, by_id, latest


def _access(access, backend):
    mail = access.get('imap') or {}
    folders = mail.get('folders') or {}
    if (access.get('state') != 'source-access-verified' or access.get('memcal_revision') != memcal_week_pin()[1]
            or (access.get('memcal_revision') == MEMCAL_WEEK_REVISION
                and any(access.get(k) != v for k, v in manifest().items()))
            or backend not in ('bluebubbles', 'chatdb') or access.get('imessage_backend') != backend
            or str(mail.get('security', '')).upper() not in ('SSL', 'STARTTLS')
            or set(folders) != {'INBOX', 'Sent'} or any(f.get('mode') != 'readonly'
            or not str(f.get('uidvalidity', '')).isdigit() or int(f['uidvalidity']) <= 0 for f in folders.values())):
        raise ValueError('unqualified mixed MemCal site or mailbox grants')


def create_memcal_scope(root, site, *, canary, prepaid, auto_recharge_evidence):
    """Prepare an arm for an explicitly named site. Startup alone cannot issue a model request."""
    from dataclasses import asdict
    from .workflow_memcal import inspect_memcal_site
    root = Path(root).absolute()
    if root != root.resolve() or root.name != 'memcal' or not root.is_dir() or type(canary) is not bool:
        raise ValueError('expected a dedicated MemCal arm directory and selected schedule')
    confirm_setting(read(auto_recharge_evidence))
    if Decimal(str(prepaid['remaining_usd'])) <= 0:
        raise ValueError('OpenRouter prepaid credit exhausted')
    actual = inspect_memcal_site(site)
    _access(actual, site.imessage_backend)
    spec_sha = sha(SPEC)
    contract, plan, *_ = _memcal_schedule(spec_sha, canary)
    record = dict(protocol='pizza-week-conversation-v2', admission=MEMCAL_SCOPE, run_id=root.parent.name,
                  root=str(root), provider='memcal', controller=process_identity(), projects=_projects(root),
                  spec_sha256=spec_sha, frozen_inputs=schedule(spec_sha, canary)[2], contract=contract,
                  plan_sha256=plan, site_sha256=digest(asdict(site)), account=site.account,
                  store=str(Path(site.memcal_home).resolve()), imessage_backend=site.imessage_backend,
                  access=actual, canary=canary, prepaid=prepaid, auto_recharge_evidence=auto_recharge_evidence)
    path = root/'workflow-scope.json'
    with path.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(record, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return binding(path)


def _progress_scope(progress, c):
    meta = progress.binding('identity') or {}
    if (progress.directory != Path(c['root'])/'workflow'
            or progress.binding('workflow_scope') != json.loads(os.environ[ENV])
            or progress.binding('evidence_kind') != 'native' or not progress.binding('plan_registered')
            or not progress.binding('initial_account_verified') or meta.get('evidence_kind') != 'native'
            or meta.get('protocol') != 'memcal-incremental-v1' or meta.get('store') != c['store']
            or meta.get('account') != c['account'] or meta.get('spec_sha256') != c['spec_sha256']
            or meta.get('source_basis') != c['frozen_inputs'] or meta.get('plan_sha256') != c['plan_sha256']
            or meta.get('canary') is not c['canary']):
        raise ValueError('MemCal progress lacks the bound native account, initial audit or frozen source plan')


def activate_memcal(progress, account, condition, deployment, *, resume_plan=None):
    """Bind verified native processes and source provenance after the empty-store check."""
    from dataclasses import asdict
    from .workflow_delivery import MemCalReaders
    from .workflow_memcal_source import MemCalSourceWitness
    from .workflow_native import NativeAccount, WarmHermes

    c = load()
    _progress_scope(progress, c)
    if (c['provider'] != 'memcal'
            or type(account) is not NativeAccount or not isinstance(account.client, MemCalReaders)
            or not isinstance(account.frontend, WarmHermes) or not isinstance(account.source_witness, MemCalSourceWitness)
            or account.source_witness.site != account.client.site or account.identity != c['account']
            or digest(asdict(account.client.site)) != c['site_sha256']
            or condition.provider != 'memcal' or condition.track != 'first-party-connector'
            or condition.state != 'continuing-account' or sorted(condition.channels) != ['email', 'imessage']
            or Path(account.frontend.owner.env.get('MEMCAL_HOME','')).resolve() != Path(c['store'])
            or deployment.get('source_access') != c['access'] or deployment.get('store_identity') != c['store']
            or deployment.get('account') != account.identity or deployment.get('condition') != vars(condition)
            or progress.binding('identity')['condition'] != json.loads(json.dumps(vars(condition)))
            or deployment.get('source_map_sha256') != account.source_witness.bindings_sha256):
        raise ValueError('MemCal process, native mixed reader, source map or deployment differs')
    if (c['access']['memcal_revision'] == MEMCAL_WEEK_REVISION) != (condition.version == MEMCAL_WEEK_VERSION):
        raise ValueError('MemCal deployment differs from the product qualified for source access')
    validate_native_deployment(condition, deployment)
    _access(deployment['source_access'], c['imessage_backend'])
    synthetic = account.client.site.synthetic_chatdb
    platform = deployment.get('synthetic_chatdb_fixture' if synthetic else 'platform_created_messages_fixture')
    if not isinstance(platform, dict) or set(platform) != {'path','sha256'}:
        raise ValueError('MemCal requires a bound Messages provenance record')
    proof = read(platform)
    native = proof.get('native_capture') or {}
    if synthetic:
        if (proof.get('protocol') != 'synthetic-chatdb-imap-v1' or proof.get('apple_delivery') is not False
                or proof.get('account') != c['account'] or Path(platform['path']).parent != Path(c['root']).parent
                or proof.get('source_map_sha256') != deployment['source_map_sha256']
                or deployment['source_access'].get('source_condition') != 'synthetic-chatdb-imap-v1'
                or deployment.get('platform_created_messages_fixture')):
            raise ValueError('Synthetic chat.db provenance differs from the bound isolated source')
    elif (proof.get('platform') != 'darwin' or proof.get('account') != c['account']
            or proof.get('imessage_backend') != c['imessage_backend']
            or proof.get('source_map_sha256') != deployment['source_map_sha256']
            or any(not isinstance(native.get(k), str) or not native[k].strip()
                   for k in ('host_id', 'messages_database_id', 'capture_id'))):
        raise ValueError('synthetic platform capture differs from the native site or source map')
    gateway = account.frontend.gateway_identity()
    driver = process_identity(gateway[0])
    if (account.frontend.owner.home != Path(c['root'])/'hermes-home'
            or deployment.get('native_processes') != {'driver':driver, 'memory':process_identity()}
            or gateway != [driver['pid'],driver['start_ticks']] or progress.binding('gateway') != gateway
            or driver == c['controller']):
        raise ValueError('MemCal warm gateway does not match the live run-owned process')
    # Verify the source monitor consumed the byte-identical frozen corpus, including
    # historical rosters and clocks that cannot be certified by checking text alone.
    from .pizza_week_dataset import ROOT, load as load_dataset
    from .frozen import load_bundle
    selected = json.loads(SPEC.read_text())
    directory, *_ = load_dataset(ROOT/selected['dataset'])
    manifest = json.loads((directory/'manifest.json').read_text())
    bundle = load_bundle(directory/manifest['source_bundle'], manifest['source_bundle_hash'])
    rows = {r['source_id']: r for life in bundle['lives'].values() for r in life['messages']}
    docs, _, _ = schedule(c['spec_sha256'], c['canary'])
    if (rows != account.source_witness.rows or list(account.source_witness.documents) != docs
            or account.source_witness.store.resolve() != Path(c['store'])/'memcal.db'):
        raise ValueError('native source monitor differs from the frozen public release inputs')
    activation = dict(scope=json.loads(os.environ[ENV]), source_map=deployment['source_map_sha256'],
                      platform_sha256=platform['sha256'], store=c['store'], store_file=_store_file(c),
                      controller=c['controller'], driver=driver)
    previous = progress.binding('memcal_activation')
    if previous is None and process_identity() != c['controller']:
        raise ValueError('only the original controller can qualify a fresh MemCal arm')
    if previous is not None and previous != activation:
        raise ValueError('MemCal platform, store file, gateway or source binding changed')
    generation, owner = _native_owner(progress, c)
    if process_identity() != owner:
        if (not previous or resume_plan is None or resume_plan.digest != c['plan_sha256']
                or not _holds_account_lock(progress)):
            raise ValueError('MemCal takeover requires the original qualified arm, locked account and frozen plan')
        # Re-read all native state from the surviving account and original gateway.
        # There is no operation that starts a replacement gateway in this contract.
        _transfer(progress, c, generation, owner, lambda: account.verify_resume(progress,resume_plan))
    progress.bind('memcal_platform_capture', proof)
    progress.bind('memcal_activation', activation)


def _store_file(c):
    path = Path(c['store'])/'memcal.db'
    if path != path.resolve() or not path.is_file():
        raise ValueError('MemCal store file unavailable')
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def _native_owner(progress, c):
    # Successors are immutable records, selected by a key range on bindings' primary key.
    row = progress.db.execute("SELECT value FROM bindings WHERE name>='memcal-owner/' AND name<'memcal-owner0' "
                              "ORDER BY name DESC LIMIT 1").fetchone()
    if row is None:
        return 0, c['controller']
    record = json.loads(row[0])
    return record['generation'], record['owner']


def _holds_account_lock(progress):
    target = (progress.directory/'workflow-account.lock').stat()
    pid = os.getpid()
    for path in Path('/proc/self/fd').iterdir():
        try:
            local = path.stat()
            if (local.st_dev,local.st_ino) == (target.st_dev,target.st_ino) and _has_flock(pid,int(path.name)):
                return True
        except FileNotFoundError:
            continue  # /proc includes transient directory enumeration descriptors.
    return False


def _has_flock(pid, fd):
    lines = (Path('/proc')/str(pid)/'fdinfo'/str(fd)).read_text().splitlines()
    return any(line.startswith('lock:') and 'FLOCK' in line.split() and 'WRITE' in line.split()
               and str(pid) in line.split() for line in lines)


def _transfer(progress, c, generation, old, audit):
    current = process_identity()
    if old['uid'] != current['uid'] or old['boot_id'] != current['boot_id'] or not _holds_account_lock(progress):
        raise ValueError('MemCal controller takeover requires the original host, user and exclusive account lock')
    try:
        alive = process_identity(old['pid'])
    except FileNotFoundError:
        alive = None
    if alive == old:
        raise ValueError('previous MemCal controller is still live')
    if progress.db.execute('SELECT 1 FROM effects WHERE result IS NULL LIMIT 1').fetchone():
        raise ValueError('unresolved external effect prevents native owner transfer')
    proof = audit()
    activation = progress.binding('memcal_activation')
    gateway = activation['driver']
    source = proof['source']
    if (process_identity(gateway['pid']) != gateway
            or proof['gateway_identity'] != [gateway['pid'],gateway['start_ticks']]
            or type(proof['sessions']) is not int or proof['sessions'] < 0
            or source['store_path'] != str(Path(c['store'])/'memcal.db')
            or source['source_map_sha256'] != activation['source_map']
            or source['cutoff'] != progress.last_cutoff() or _store_file(c) != activation['store_file']):
        raise ValueError('native takeover readback differs from original gateway, site or physical store')
    verified = progress._store(proof)
    # Compare-and-swap immediately before exposing any fresh callback permission.
    with progress._write():
        if (_native_owner(progress,c) != (generation,old)
                or progress.db.execute('SELECT 1 FROM effects WHERE result IS NULL LIMIT 1').fetchone()):
            raise ValueError('native ownership or pending effects changed during takeover')
        progress._bind(f'memcal-owner/{generation+1:020d}', dict(generation=generation+1,prior=old,owner=current,
                       verified_resume_sha256=verified,scope=json.loads(os.environ[ENV])))



def _accepted(progress, at, mapping, expected):
    clock = progress.get_effect('source-clock', at) or {}
    accepted = progress.get_effect('sync', at) or {}
    rows = accepted.get('rows') or {}
    observation = clock.get('observation_sha256')
    if (clock.get('cutoff') != at or instant(clock['as_of']) != instant(at) or clock.get('source_map_sha256') != mapping
            or not isinstance(observation, str) or len(observation) != 64 or not accepted.get('native_id')
            or accepted.get('source_observation_sha256') != observation or accepted.get('source_map_sha256') != mapping
            or accepted.get('accepted') is not True or set(accepted.get('native_cursors') or {}) != {'email','imessage'}
            or set(rows) != set(expected) or any(rows[k].get('source_sha256') != sha256
            or len(rows[k].get('archive_sha256','')) != 64 for k, sha256 in expected.items())):
        raise ValueError('native source-clock or mixed release receipt differs from frozen documents')
    return accepted


def _operation_slot(c, progress, phase, key, inputs):
    from .live_dispatch import owns_fresh_effect
    _progress_scope(progress, c)
    activation = progress.binding('memcal_activation') or {}
    claim = progress.active_effect(phase, key, inputs)
    _, owner = _native_owner(progress,c)
    if (process_identity() != owner or activation.get('scope') != json.loads(os.environ[ENV])
            or activation.get('store') != c['store'] or activation['store_file'] != _store_file(c) or not claim or not owns_fresh_effect(progress, claim)):
        raise ValueError('no live owner of this newly dispatched MemCal operation')
    if c.get('admission') == 'pizza-day-synthetic-memcal-v1':
        from .pizza_day_controller import operation_slot
        return operation_slot(progress,phase,key,inputs,claim,owner,activation['driver'])
    _, _, nights, turns, last = _memcal_schedule(c['spec_sha256'], c['canary'])
    if phase == 'dream':
        accepted = _accepted(progress, key, activation['source_map'], nights[key])
        previous = max((at for at in nights if instant(at) < instant(key)), default=None)
        if accepted != inputs or claim['role'] != 'memory' or previous != progress.last_cutoff():
            raise ValueError('Dream requires the next accepted native collection')
        return dict(kind='ingest',batch_id=key), claim, owner
    index, turn, cutoff = turns[key]
    if not cutoff or cutoff != progress.last_cutoff() or progress.next_ordinal() != index:
        raise ValueError('MemCal turn outside its verified chronological release')
    accepted = _accepted(progress, cutoff, activation['source_map'], nights[cutoff])
    ready = progress.observation('dream', cutoff, 'native-ready') or {}
    dream = progress.get_effect('dream', cutoff)
    if (ready.get('native_id') != accepted['native_id'] or ready.get('ready') is not True
            or not dream or ready.get('readback') != dream or dream.get('pending_spool') != 0
            or not dream.get('finished_at') or set(ready.get('new_source_ids', [])) != set(nights[cutoff])
            or ready.get('source_cursor') != accepted['native_cursors']):
        raise ValueError('MemCal turn requires observed mixed cursors and completed Dream')
    if phase == 'answer' and claim['role'] == 'driver':
        previous = progress.latest_surface(turn.session, before=index)
        if inputs != dict(ordinal=index,prompt=turn.prompt,at=turn.at,
                          session=previous['session_id'] if previous else None,cutoff=cutoff):
            raise ValueError('MemCal answer differs from the current native chat or frozen question')
        return dict(kind='answer',question_id=key), claim, activation['driver']
    if phase == 'chat-close' and claim['role'] == 'memory':
        saved = progress.get_verified(key)
        if last[turn.session] != key or not saved or inputs != {'session_id': saved[1]['session_id']}:
            raise ValueError('MemCal cleanup differs from the final verified chat turn')
        return dict(kind='cleanup',question_id=key), claim, activation['driver']
    raise ValueError('MemCal operation is outside its frozen paid role')


@contextmanager
def memcal_operation(progress, phase, key, inputs):
    """A kernel-held lease exists only inside the new callback, across native processes.

    A killed controller leaves neither a live lock nor a valid process identity. This
    transient file has no recovery semantics; durable effect status lives in SQLite.
    """
    if not os.environ.get(ENV):
        yield
        return
    import fcntl
    import tempfile
    c = load()
    slot, claim, sender = _operation_slot(c, progress, phase, key, inputs)
    lease = progress.directory/'active-native-operation.json'
    with tempfile.NamedTemporaryFile(dir=progress.directory, mode='w', delete=False) as handle:
        temp = Path(handle.name)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            record = dict(slot={**slot, 'provider':'memcal', 'checkpoint_effect':claim}, sender=sender,
                          controller=process_identity(), fd=handle.fileno(), scope=json.loads(os.environ[ENV]))
            json.dump(record, handle,sort_keys=True)
            handle.flush()
            os.replace(temp, lease)
            try:
                yield
            finally:
                lease.unlink(missing_ok=True)
        finally:
            temp.unlink(missing_ok=True)


def _native_lease(c, project):
    from .workflow_progress import WorkflowProgress
    import stat
    directory = Path(c['root'])/'workflow'
    lease = directory/'active-native-operation.json'
    with os.fdopen(os.open(lease, os.O_RDONLY | os.O_NOFOLLOW)) as fd:
        saved = os.fstat(fd.fileno())
        if not stat.S_ISREG(saved.st_mode) or saved.st_size > 8192 or saved.st_mode & 0o077:
            raise ValueError('invalid private MemCal callback lease')
        record = json.load(fd)
        owner = record['controller']
        if (record['scope'] != json.loads(os.environ[ENV])
                or process_identity(owner['pid']) != owner or process_identity() != record['sender']):
            raise ValueError('MemCal native sender or controller is no longer the scoped owner')
        remote = Path('/proc')/str(owner['pid'])
        held = (remote/'fd'/str(record['fd'])).stat()
        if (saved.st_dev, saved.st_ino) != (held.st_dev, held.st_ino) or not _has_flock(owner['pid'],record['fd']):
            raise ValueError('MemCal native operation callback is no longer active')
        slot = record['slot']
        claim = slot['checkpoint_effect']
        if project != c['projects']['memcal'][claim['role']]:
            raise ValueError('MemCal request uses a different role')
        with WorkflowProgress(directory, claim['run_key']) as progress:
            activation = progress.binding('memcal_activation')
            if (progress.active_effect(claim['phase'],claim['key']) != claim
                    or not activation or activation['store_file'] != _store_file(c)
                    or _native_owner(progress,c)[1] != owner or progress.binding('workflow_scope') != record['scope']):
                raise ValueError('MemCal effect is no longer pending for this exact attempt')
        return slot


def native_user_question(content, prompt):
    if content == prompt:
        return True
    prefix = prompt + '\n\n<memory-context>\n'
    suffix = '\n</memory-context>'
    if not isinstance(content, str) or not content.startswith(prefix) or not content.endswith(suffix):
        return False
    context = content[len(prefix):-len(suffix)]
    return bool(context.strip()) and '<memory-context>' not in context and '</memory-context>' not in context


def _memcal_admission(c, project, body):
    from .openrouter_route import validate_request
    from .workflow_grading import claimed_request
    validate_request(body, profile=PROFILE)
    if project == c['projects']['memcal']['judge']:
        slot = claimed_request(c, project, body)
        if slot is None:
            raise ValueError('no live MemCal judge for this verified turn')
        from .workflow_progress import WorkflowProgress
        with WorkflowProgress(Path(c['root'])/'workflow', slot['checkpoint_effect']['run_key']) as progress:
            _progress_scope(progress, c)
            if (progress.binding('memcal_activation') or {}).get('scope') != json.loads(os.environ[ENV]):
                raise ValueError('MemCal judge lacks original qualified source scope')
    else:
        from .workflow_dream_waves import native_dream_slot
        slot = native_dream_slot(_native_lease(c, project))
        if slot['kind'] == 'answer':
            if c.get('admission') == 'pizza-day-synthetic-memcal-v1':
                from .pizza_day_controller import QUESTION, QUESTION_AT
                prompt, at = QUESTION, QUESTION_AT
            else:
                turn = _memcal_schedule(c['spec_sha256'], c['canary'])[3][slot['question_id']][1]
                prompt, at = turn.prompt, turn.at
            users = [m for m in body['messages'] if m['role'] == 'user']
            last = users[-1].get('content') if users else None
            revision = _memcal_revision(c)
            if revision == MEMCAL_WEEK_REVISION:
                verify_driver_schemas(body.get('tools'))
            if (not native_user_question(last, prompt)
                    or sorted(t['function']['name'] for t in body.get('tools', [])) != list(memcal_week_tools(revision))):
                raise ValueError('MemCal driver question or native tool surface differs')
            from .workflow_progress import WorkflowProgress
            from .workflow_source_plan import SourcePlan
            with WorkflowProgress(Path(c['root'])/'workflow', slot['checkpoint_effect']['run_key']) as progress:
                plan = SourcePlan.from_connection(progress.db, c['plan_sha256'])
                plan.verify_no_leak({'model_requests':[body], 'prefetch':None},at,prompt)
    return dict(root=c['root'],capability=json.loads(os.environ[ENV]),run_id=c['run_id'],slot=slot,projects=[project])


def confirm_memcal_sender(row):
    """Recheck callback liveness between preparing the receipt and sending upstream."""
    c = load()
    slot = row['workflow_slot']
    if (c['provider'] != 'memcal' or row['workflow_capability'] != json.loads(os.environ[ENV])
            or row['workflow_root'] != c['root']):
        raise ValueError('prepared request no longer belongs to its MemCal scope')
    if slot['kind'] == 'judge':
        from .workflow_grading import active_claim
        if not active_claim(c,row['project'],slot['checkpoint_effect']):
            raise ValueError('MemCal judge callback is no longer live')
    else:
        from .workflow_dream_waves import native_dream_slot
        if native_dream_slot(_native_lease(c,row['project'])) != slot:
            raise ValueError('MemCal native callback or Dream wave changed before dispatch')


def confirm_workflow_judge_sender(row):
    """Require the prepared judge request to own its active checkpoint at send time."""
    from .workflow_grading import active_claim

    scope = load()
    slot = row['workflow_slot']
    if (scope['provider'] != slot['provider']
            or row['workflow_capability'] != json.loads(os.environ[ENV])
            or row['workflow_root'] != scope['root']
            or not active_claim(scope, row['project'], slot['checkpoint_effect'])):
        raise ValueError('Workflow judge callback is no longer live for its prepared scope')
