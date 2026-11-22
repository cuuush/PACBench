"""Offline plans for the three native observation-store bindings.

These do not establish readiness or start a service. A byte copy of a live DB,
an endpoint declaration, or a successful constructor is never a readiness proof.
"""
from copy import deepcopy
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib,json
import subprocess

MEM0_PLUGIN_SHA256='0d32999bc684e79fcd243e9f05f3e04fb8b1e1d4442045d2a28817575e09bdfc'


def native_source(provider,home,repo,env,python):
    plugin=Path(home)/'plugins'/provider
    if provider=='reme':
        from .reme_memos_setup import PINS
        source=repo/'vendor/reme';expected=PINS['reme'][1]
        if plugin.resolve()!=source.resolve()/'integrations/hermes_agent':raise ValueError('Native ReMe plugin path differs')
    elif provider=='memcal':
        from .provider_versions import MEMCAL_OPERATIONS_REVISION,MEMCAL_OPERATIONS_CONDITION,MEMCAL_REVISION
        from .provider_versions import MEMCAL_WEEK_CONDITION, MEMCAL_WEEK_REVISION
        source=Path(env['MEMCAL_SRC']).resolve()
        workflow = env.get('PACBENCH_WORKFLOW_CAPTURE') == 'pizza-week-conversation-v2'
        label = env.get('PACBENCH_MEMCAL_CONDITION')
        week_revision = MEMCAL_WEEK_REVISION if label == MEMCAL_WEEK_CONDITION else MEMCAL_REVISION
        expected = env.get('PACBENCH_MEMCAL_REUSE_REVISION', week_revision if workflow else 'dac7214006b84ec364ecbcd9472daca7db588a25')
        stable = expected == MEMCAL_REVISION and env.get('PACBENCH_MEMCAL_CONDITION') == 'stable-0.8.11' and (workflow or bool(env.get('PACBENCH_R2_LIVE_SCOPE')))
        current_week = workflow and expected == MEMCAL_WEEK_REVISION and label == MEMCAL_WEEK_CONDITION
        if workflow and not (stable or current_week):
            raise ValueError('Continuing MemCal workflow requires its reviewed stable condition')
        if not (stable or current_week) and expected not in {'dac7214006b84ec364ecbcd9472daca7db588a25','da58057eb00f79082dfcf8f59baa264267c83ca1',MEMCAL_OPERATIONS_REVISION}:
            raise ValueError('Unknown MemCal review condition')
        if expected==MEMCAL_OPERATIONS_REVISION and env.get('PACBENCH_MEMCAL_CONDITION')!=MEMCAL_OPERATIONS_CONDITION:
            raise ValueError('MemCal observation revision needs its explicit reviewed condition')
        if {f.name for f in plugin.glob('*.py')}!={f.name for f in (source/'integrations/hermes/memcal').glob('*.py')}:
            raise ValueError('MemCal plugin file set differs')
        for file in (source/'integrations/hermes/memcal').glob('*.py'):
            if hashlib.sha256(file.read_bytes()).digest()!=hashlib.sha256((plugin/file.name).read_bytes()).digest():raise ValueError('MemCal plugin differs from the selected tree')
    elif provider in {'openviking','hindsight','supermemory','honcho'}:
        from .catalog_plugins import CATALOG,verify_retained_catalog_provider
        verify_retained_catalog_provider(provider,Path(home))
        return {provider:CATALOG[provider][1]}
    elif provider=='cognee':
        from .cognee_memobase_setup import PINS
        source=repo/'vendor/cognee-integrations';expected=PINS['cognee']['commit']
        installed=plugin/'cognee_integration_hermes/provider.py'
        original=source/'integrations/hermes-agent/cognee_integration_hermes/provider.py'
        if not installed.is_file() or installed.read_bytes()!=original.read_bytes():
            raise ValueError('Native Cognee installed provider differs from the pinned source')
    elif provider=='memos':
        if env.get('PACBENCH_MEMOS_OSS_CONFIG'):
            source=Path(env['PACBENCH_MEMOS_OSS_SOURCE']).resolve()
            expected='3a16e5fe3048a209f722806f4123fa2e533588fd'
            deployed=Path(home)/'plugins/memososs'
            binding=repo/'provider_adapters/memos_oss_hermes'
            files=('__init__.py','plugin.yaml')
            if (Path(env['PACBENCH_MEMOS_OSS_CONFIG']) != Path(home)/'memos-oss.json'
                    or deployed.is_symlink() or any((deployed/f).read_bytes()!=(binding/f).read_bytes() for f in files)):
                raise ValueError('MemOS OSS Hermes REST binding differs')
            actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
            if actual!=expected or subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip():
                raise ValueError('MemOS OSS server source differs from the pinned release')
            return {'memos_oss':actual,'memos_oss_hermes_files':{
                name:hashlib.sha256((binding/name).read_bytes()).hexdigest() for name in files}}
        from .provider_versions import verify_memos_package
        package, release=verify_memos_package(repo)
        if (Path(home)/'plugins/memtensor').resolve()!=package.resolve()/'adapters/hermes/memos_provider':
            raise ValueError('Native MemOS plugin path differs')
        return {'memos':release['sha256']}
    elif provider=='mem0':
        files={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(plugin.glob('*.py'))}
        sha=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()
        version=subprocess.check_output([str(python),'-I','-c','import importlib.metadata;print(importlib.metadata.version("mem0ai"))'],text=True).strip()
        if sha!=MEM0_PLUGIN_SHA256 or version!='2.2.1':raise ValueError('Native Mem0 plugin or OSS SDK pin differs')
        return {'mem0_sdk':version,'mem0_plugin_sha256':sha}
    else:raise ValueError('Unknown native observation provider')
    actual=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if actual!=expected or subprocess.check_output(['git','-C',str(source),'status','--porcelain'],text=True).strip():
        raise ValueError('Native observation provider source differs')
    return {provider:actual, **({'memcal_baseline':label} if provider=='memcal' and (stable or current_week) else {})}


def plan(provider,config,workspace,*,endpoint=None):
    workspace=Path(workspace).resolve();result=deepcopy(config)
    if provider=='reme':
        from .reme_memos_setup import local_endpoint
        if result.get('mode')!='http':raise ValueError('Retain native ReMe HTTP condition')
        result.update(endpoint=local_endpoint(endpoint),workspace_dir=str(workspace))
        return {'provider':provider,'config':result,'native_binding':'reme/config.json',
            'required_proof':['native service PID/start and actual workspace','all auto_memory jobs completed','native source-only checkpoint ready'],
            'live_qualified':False}
    if provider=='mem0':
        if result.get('mode')!='oss' or result.get('oss',{}).get('graph_store'):raise ValueError('Only the pinned Mem0 OSS condition is applicable')
        vector=result['oss']['vector_store']
        if vector['provider']!='qdrant' or any(vector.get('config',{}).get(k) for k in ['host','url','api_key','client']):
            raise ValueError('An isolated native local Qdrant namespace is required')
        vector.setdefault('config',{})['path']=str(workspace/'qdrant')
        return {'provider':provider,'config':result,'native_binding':'mem0.json + stable MEM0_DIR observation alias',
            'state_alias_target':str(workspace/'mem0-state'),
            'required_proof':['native Qdrant checkpoint closed and reopened','native history SQLite path follows the isolated alias','native sync and prefetch workers drained','native source-only checkpoint ready'],
            'live_qualified':False}
    if provider in {'openviking','hindsight','supermemory','honcho','cognee'}:
        from .reme_memos_setup import local_endpoint
        return {'provider':provider,'endpoint':local_endpoint(endpoint)}
    if provider=='memos':
        return {'provider':provider,'config':result,'native_binding':'resolved MEMOS_HOME per independent store',
                'state_alias_target':str(workspace)}
    if provider=='memcal':
        return {'provider':provider,'config':result,'native_binding':'stable MEMCAL_HOME observation alias',
            'state_alias_target':str(workspace),'required_proof':['exact selected MemCal source SHA','closed native SQLite checkpoint and wiki reopened','native archive writes confined to this observation','native correction quality gate'],
            'live_qualified':False}
    raise ValueError('Unknown observation adapter')


def switch_alias(alias,target,drain):
    """Switch only an explicit run-owned link, after independently verified drain."""
    alias=Path(alias);target=Path(target).resolve()
    if not drain.get('sessions') or not all(s.get('clean') for s in drain['sessions']) or any(drain.get(k) for k in
        ['native_release_threads','native_memory_threads','native_failures','native_children']):raise ValueError('Native drain is incomplete')
    if not target.is_dir() or alias.exists() and not alias.is_symlink():raise ValueError('Refuse to replace a native directory')
    temporary=alias.with_name(alias.name+'.next')
    temporary.symlink_to(target,target_is_directory=True);temporary.replace(alias)


def checkpoint_plan(resolved,schedule,provider,*,contracts=None):
    """Keep every exact question time; restart at a changed ingestion boundary."""
    from .document_ingestion import ready_sources
    from .semantic_contract import EvaluationContract
    if provider not in {'memcal','mem0','reme'}:raise ValueError('Unknown staged provider')
    questions=sorted(resolved['checks'],key=lambda q:datetime.fromisoformat(q['at']))
    if len({q['id'] for q in questions})!=len(questions):raise ValueError('Duplicate question identity')
    if contracts is not None and set(contracts)!={q['id'] for q in questions}:raise ValueError('Frozen contract coverage differs')
    groups=[];last_key=None
    for index,q in enumerate(questions):
        at=datetime.fromisoformat(q['at'])
        if at.tzinfo is None:raise ValueError('Question clock needs timezone')
        batches=ready_sources(schedule,provider,q['at'],resolved['timezone'])
        key=(tuple(b['id'] for b in batches),at.astimezone(ZoneInfo(resolved['timezone'])).date().isoformat())
        if key!=last_key:
            groups.append({'id':len(groups)+1,'ready_batch_ids':list(key[0]),'local_date':key[1],
                'restart_before_ingest_or_clock_boundary':True,'questions':[]});last_key=key
        contract=contracts[q['id']] if contracts is not None else None
        if isinstance(contract, EvaluationContract):
            contract = {'question': contract.question, 'contract_hash': contract.contract_hash,
                        'claims': contract.claims}
        elif contract is not None:
            body={k:v for k,v in contract.items() if k!='contract_hash'}
            if (hashlib.sha256(json.dumps(body,sort_keys=True,ensure_ascii=False).encode()).hexdigest()!=contract['contract_hash'] or
                contract['question_at']!=q['at']):raise ValueError('Frozen contract hash or question time differs')
        prompt=contract['question'] if contract else q['prompt']
        groups[-1]['questions'].append({'id':q['id'],'at':q['at'],'prompt':prompt,
            'horizon':questions[index+1]['at'] if index+1<len(questions) else None,
            'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
            'authored_prompt_sha256':hashlib.sha256(q['prompt'].encode()).hexdigest(),
            'prompt_from':'frozen evaluated contract' if contract else 'authored spec; frozen contract not supplied',
            'contract_hash':contract['contract_hash'] if contract else None,
            'criteria_count':len(contract['claims']) if contract else None,'authored_fact_count':len(q['facts'])})
    return {'protocol':'chronological-observation-checkpoints-v1','provider':provider,'groups':groups,
        'questions':len(questions),'criteria':sum(q['criteria_count'] for g in groups for q in g['questions']) if contracts is not None else None,
        'authored_facts':sum(len(q['facts']) for q in questions),
        'times_collapsed':False,'automatic_replay':False,'live_qualified':False,
        'required_gates':['checkpoint native readiness and quiescence','native isolated observation binding',
            'new request-bound clock admission and application-clock audit','prepaid headroom','selected MemCal repair gate when applicable']}
