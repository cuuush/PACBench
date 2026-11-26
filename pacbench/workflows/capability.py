"""Bind paid v2 calls to one fresh account, its frozen schedule and its durable workflow."""
from datetime import datetime
from decimal import Decimal
from importlib import import_module
import hashlib
import json
import os
from pathlib import Path
from typing import NamedTuple

from ..canary_capability import binding, digest, read, sha
from ..live_dispatch import process_identity
from pacbench.providers.memcal import capability as _memcal
# Retain the shared capability imports used by native launch and recovery callers.
from pacbench.providers.memcal.capability import (
    MEMCAL_SCOPE,
    _access,
    _memcal_schedule,
    _native_lease,
    _native_owner,
    _store_file,
    _transfer,
    activate_memcal,
    create_memcal_scope,
    memcal_operation,
)
from .document_admission import hindsight_consolidation_call, mem0_import_request
from pacbench.campaigns.pizza_week import SPEC, cache_bound_schedule
from .tracks import HERMES_TOOLS, MEMOS_OSS_TOOLS

ENV = 'PACBENCH_WORKFLOW_SCOPE'
PROFILE = 'luna-bedrock-global'
MEMOS_OSS_ADMISSION = 'memos-oss-native-file-document-week-v1'
SM_QUESTION_ADMISSION = 'supermemory-stock-first-question-v1'
SM_WEEK_ADMISSION = 'supermemory-stock-document-week-v1'


class _DocumentArm(NamedTuple):
    admission: str
    canary_module: str
    store: str


DOCUMENT_ARMS = {
    'mem0': _DocumentArm('mem0-native-oss-document-week-v1', '.providers.mem0', 'mem0-state'),
    'hindsight': _DocumentArm('hindsight-native-file-document-week-v1', '.providers.hindsight.workflow', 'fallback-home'),
    'openviking': _DocumentArm('openviking-native-file-document-week-v1', '.providers.openviking', 'openviking-data'),
    'reme': _DocumentArm('reme-native-watched-resource-week-v1', '.providers.reme', 'reme-workspace'),
    'cognee': _DocumentArm('cognee-native-permanent-document-week-v1', '.providers.cognee', 'cognee-data'),
    'memos': _DocumentArm(MEMOS_OSS_ADMISSION, '.providers.memos', 'memos-oss-service'),
}


class _SpecialAdmission(NamedTuple):
    module: str
    journal: str | None = None
    journal_module: str | None = None
    validator: str = 'validate'
    admit: str | None = 'admit'
    takes_profile: bool = False
    verify_campaign_stop: bool = False


_SPECIAL_ADMISSIONS = {
    'supermemory-stock-source-qualification-v1': _SpecialAdmission('.providers.supermemory.source_admission', 'SourceJournal'),
    SM_QUESTION_ADMISSION: _SpecialAdmission('.providers.supermemory.question_admission', 'QuestionJournal'),
    SM_WEEK_ADMISSION: _SpecialAdmission('.providers.supermemory.week_admission', 'WeekJournal'),
    'pizza-day-native-document-v1': _SpecialAdmission(
        '.campaigns.pizza_day', validator='validate_lineup', admit='admit_lineup', verify_campaign_stop=True),
    'luna-judge-development-ab-v1': _SpecialAdmission('.judging.development_live', 'JudgeJournal'),
    'pizza-week-luna-explicit-overlay-v1': _SpecialAdmission('.cohort_regrade_live', 'OverlayJournal'),
    'pizza-week-honcho-authored-chats-luna-v4-v1': _SpecialAdmission(
        '.providers.honcho.regrade_live', 'OverlayJournal', journal_module='.cohort_regrade_live'),
    'pizza-week-question-contract-development-shadow-v1': _SpecialAdmission('.judging.shadow_live', 'ShadowJournal'),
    'pizza-week-question-contract-span-regression-v2': _SpecialAdmission('.judging.shadow_live', 'SpanJournal'),
    'luna-judge-development-challenge-v2': _SpecialAdmission('.judging.calibration_live', 'ChatJournal'),
    'pizza-day-wrong-answer-judge-v1': _SpecialAdmission('.campaigns.pizza_day_review'),
    'pizza-day-synthetic-memcal-v1': _SpecialAdmission(
        '.campaigns.pizza_day', validator='validate_memcal', admit=None, verify_campaign_stop=True),
    'honcho-seven-daily-documents-v1': _SpecialAdmission('.providers.honcho.daily_gate', admit='admission', takes_profile=True),
}


def _special_admission(scope):
    protocol = scope.get('admission')
    return _SPECIAL_ADMISSIONS.get(protocol) if isinstance(protocol, str) else None


def campaign_stop(root, *, nested_run=False):
    # Document canaries and Week arms share the stop file above their run directories.
    campaign = root.parent.parent if nested_run else root.parent
    return campaign / 'ACCOUNT_STOP'


def scope_campaign_stop(scope):
    root = Path(scope['root'])
    admission = scope.get('admission')
    special = _special_admission(scope)
    if admission == MEMCAL_SCOPE or (special and special.verify_campaign_stop):
        stop = campaign_stop(root)
    elif (admission in (SM_QUESTION_ADMISSION, SM_WEEK_ADMISSION)
          or (scope['protocol'] == 'pizza-week-conversation-v2' and scope['provider'] in DOCUMENT_ARMS)):
        stop = campaign_stop(root, nested_run=True)
    else:
        raise ValueError('unsupported workflow campaign stop scope')
    if scope.get('campaign_stop', str(stop)) != str(stop):
        raise ValueError('workflow campaign stop differs from the original run layout')
    return stop


@cache_bound_schedule(maxsize=2)
def schedule(spec_sha, canary):
    from pacbench.campaigns.pizza_week import compile_pizza_week

    plan = compile_pizza_week(canary=canary)
    if spec_sha != plan.spec_sha256:
        raise ValueError('workflow spec changed')
    return list(plan.documents), list(plan.turns), plan.source_basis


def create(root, provider, *, canary, prepaid, auto_recharge_evidence, qualification=None):
    root = Path(root).resolve()
    if root.name != provider or provider not in DOCUMENT_ARMS:
        raise ValueError('no qualified continuing-account binding for this provider')
    arm = DOCUMENT_ARMS[provider]
    if not canary and qualification is None:
        raise ValueError(f'{provider} Week requires a separately verified live canary')
    if qualification is not None:
        if canary is not False:
            raise ValueError('unexpected live qualification binding')
        canary_root = Path(qualification['result']['path']).parent
        if canary_root == root or import_module(arm.canary_module, 'pacbench').check_canary(canary_root) != qualification:
            raise ValueError('Week requires the exact retained canary bindings')
    spec_sha = sha(SPEC)
    _, _, frozen = schedule(spec_sha, canary)
    record = dict(protocol='pizza-week-conversation-v2', run_id=root.parent.name, root=str(root),
                  provider=provider, admission=arm.admission, controller=process_identity(),
                  projects=_document_projects(root, provider), spec_sha256=spec_sha, frozen_inputs=frozen,
                  canary=canary, prepaid=prepaid, auto_recharge_evidence=auto_recharge_evidence,
                  campaign_stop=str(campaign_stop(root, nested_run=True)))
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
    special = _special_admission(c)
    if special:
        module = import_module(special.module, 'pacbench')
        getattr(module, special.validator)(c, bound)
        if special.verify_campaign_stop:
            scope_campaign_stop(c)
        return c
    provider = c['provider']
    if (root != root.resolve() or root/'workflow-scope.json' != Path(bound['path'])
            or c['run_id'] != root.parent.name or provider != root.name
            or (provider != 'memcal' and provider not in DOCUMENT_ARMS)
            or c['protocol'] != 'pizza-week-conversation-v2' or type(c['canary']) is not bool
            or c['spec_sha256'] != sha(SPEC) or c['frozen_inputs'] != schedule(c['spec_sha256'], c['canary'])[2]):
        raise ValueError('unbound v2 account or dataset')
    if provider in DOCUMENT_ARMS:
        arm = DOCUMENT_ARMS[provider]
        if c.get('admission') != arm.admission or c['projects'] != _document_projects(root, provider):
            raise ValueError(f'unbound native {provider} continuing-account scope')
        if not c['canary']:
            _validate_document_qualification(c, root)
    if provider == 'memcal':
        _memcal.validate_scope(c)
    scope_campaign_stop(c)
    return c


def _validate_document_qualification(scope, root):
    qualification = scope['qualification']
    result = read(qualification['result'])
    canary = read(qualification['scope'])
    canary_root = Path(canary['root'])
    qualified = (result['state'], result['cleanup']) == ('live-canary-complete', 'complete')
    if scope['provider'] == 'hindsight' and qualification.get('cleanup'):
        cleanup = read(qualification['cleanup'])
        qualified = ((result['state'], result['cleanup']) == ('incomplete', 'unverified')
                     and Path(qualification['cleanup']['path']) == canary_root/'service-shutdown-reconciliation.json'
                     and cleanup['state'] == 'verified-after-exit' and cleanup['result'] == qualification['result'])
    if (not result['canary'] or not qualified
            or Path(qualification['scope']['path']) != canary_root/'workflow-scope.json'
            or Path(qualification['result']['path']) != canary_root/'result.json'
            or canary_root == root or canary['provider'] != scope['provider'] or not canary['canary']
            or canary['frozen_inputs'] != scope['frozen_inputs'] or canary['spec_sha256'] != scope['spec_sha256']):
        raise ValueError('Week does not bind a distinct completed canary')


def confirm_setting(record):
    if (record.get('auto_recharge') != 'off' or not isinstance(record.get('confirmed_by'), str)
            or not record['confirmed_by'].strip() or not isinstance(record.get('confirmed_at'), str)
            or datetime.fromisoformat(record['confirmed_at']).utcoffset() is None):
        raise ValueError('a dated operator observation of Auto Recharge off is required')


def budget(*, project=None, profile=None):
    from ..observational_receipts import ENV as JOURNAL
    from ..observational_receipts import RunReceiptJournal
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
    special = _special_admission(c)
    if special and special.journal:
        module = import_module(special.journal_module or special.module, 'pacbench')
        return getattr(module, special.journal)(path)
    if c['provider'] == 'memcal':
        from ..observational_receipts import IndexedRunReceiptJournal
        return IndexedRunReceiptJournal(path)
    return RunReceiptJournal(path)


def owner(env, home, checkpoint, provider, clock):
    c = load()
    if (env.get(ENV) != os.environ[ENV] or provider != c['provider'] or home != Path(c['root'])/'hermes-home'
            or not clock or checkpoint != c['spec_sha256'] or env.get('PACBENCH_WORKFLOW_CAPTURE') != c['protocol']
            or env.get('PACBENCH_PROVIDER_ONLY') != '1' or env.get('PACBENCH_API_INPUT_TRACE') != str(home.parent/'api-inputs.jsonl')):
        raise ValueError('workflow gateway differs from scoped home, clock or capture')
    if provider == 'memcal':
        _memcal.validate_owner(c, env)
    tools = MEMOS_OSS_TOOLS if provider == 'memos' else HERMES_TOOLS[provider]
    if provider == 'honcho' and c['admission'] == 'honcho-seven-daily-documents-v1' and (
            not (c.get('first_question') or c.get('week_schedule')) or process_identity() != c['controller']
            or env.get('PACBENCH_TOOL_ALLOWLIST') != ','.join(tools)
            or env.get('PACBENCH_OPENROUTER_PROFILE') != PROFILE or env.get('PACBENCH_OFFLINE_CONFORMANCE')):
        raise ValueError('Honcho gateway requires its scoped controller, stock toolkit and Luna route')
    if provider in DOCUMENT_ARMS and (
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
            from pacbench.providers.supermemory.question_admission import ready
            ready(c)
    if provider == 'memos' and env.get('PACBENCH_MEMOS_OSS_CONFIG') != str(home/'memos-oss.json'):
        raise ValueError('MemOS OSS gateway does not bind its run-owned configuration')
    if provider == 'openviking' and env.get('PACBENCH_SOURCE_ARGUMENT_RULES') != '{}':
        raise ValueError('OpenViking continuing-account gateway must retain supported native source reads')
    if provider=='cognee' and (env.get('PACBENCH_COGNEE_PHYSICAL_EXIT_WATCHER')!='1'
            or env.get('COGNEE_IMPROVE_BACKGROUND')!='false'):
        raise ValueError('Cognee needs a physical native exit watcher and in-process session promotion')


def admission(project, profile, body):
    c = load()
    provider = c['provider']
    roles = c['projects'][provider]
    if project not in roles.values() or profile != PROFILE or not isinstance(body, dict):
        raise ValueError('unexpected workflow role or model route')
    special = _special_admission(c)
    if special and special.admit:
        module = import_module(special.module, 'pacbench')
        admit = getattr(module, special.admit)
        if special.takes_profile:
            return admit(c, project, profile, body)
        return admit(c, project, body)
    if provider == 'memcal':
        return _memcal_admission(c, project, body)
    from .document_admission import admit_document
    return admit_document(c, project, body)


def _document_projects(root,provider):
    prefix = 'pacbench-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '-' + provider + '-'
    return {provider:{role:prefix+role for role in ('memory','driver','judge')}}


def confirm_document_sender(row):
    from .document_admission import confirm_sender

    return confirm_sender(load(), row)


def native_user_question(content, prompt):
    if content == prompt:
        return True
    prefix = prompt + '\n\n<memory-context>\n'
    suffix = '\n</memory-context>'
    if not isinstance(content, str) or not content.startswith(prefix) or not content.endswith(suffix):
        return False
    context = content[len(prefix):-len(suffix)]
    return bool(context.strip()) and '<memory-context>' not in context and '</memory-context>' not in context


def _memcal_admission(scope, project, body):
    return _memcal.admit(scope, project, body, native_lease=_native_lease)


def confirm_memcal_sender(row):
    return _memcal.confirm_sender(load(), row, native_lease=_native_lease)


def confirm_workflow_judge_sender(row):
    """Require the prepared judge request to own its active checkpoint at send time."""
    from .grading import active_claim

    scope = load()
    slot = row['workflow_slot']
    if (scope['provider'] != slot['provider']
            or row['workflow_capability'] != json.loads(os.environ[ENV])
            or row['workflow_root'] != scope['root']
            or not active_claim(scope, row['project'], slot['checkpoint_effect'])):
        raise ValueError('Workflow judge callback is no longer live for its prepared scope')
