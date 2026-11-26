"""Bind native MemCal collection, recall and cleanup to durable workflow effects."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path

from ...canary_capability import binding, digest, read, sha
from ...live_dispatch import holds_flock, process_identity
from ...provider_versions import MEMCAL_REVISION, MEMCAL_WEEK_REVISION, MEMCAL_WEEK_VERSION, memcal_week_pin
from .contract import manifest, verify_driver_schemas
from pacbench.campaigns.pizza_week import SPEC, cache_bound_schedule
from pacbench.workflows.spec import instant
from pacbench.workflows.tracks import memcal_week_tools, validate_native_deployment

MEMCAL_SCOPE = 'memcal-source-operation-v1'


def _memcal_revision(scope):
    if scope['admission'] == 'pizza-day-synthetic-memcal-v1':
        return MEMCAL_REVISION
    return scope['access']['memcal_revision']


def validate_scope(c):
    contract, plan, *_ = _memcal_schedule(c['spec_sha256'], c['canary'])
    if (c.get('admission') != MEMCAL_SCOPE or c.get('contract') != contract or c.get('plan_sha256') != plan
            or c['projects'] != projects(Path(c['root']))):
        raise ValueError('MemCal capability differs from the frozen release or role binding')


def validate_owner(c, env):
    from pacbench.workflows.capability import PROFILE

    revision = _memcal_revision(c)
    label, selected_revision, _ = memcal_week_pin()
    if (revision != selected_revision or env.get('PACBENCH_MEMCAL_CONDITION') != label
            or env.get('PACBENCH_TOOL_ALLOWLIST') != ','.join(memcal_week_tools(revision))
            or env.get('PACBENCH_OPENROUTER_PROFILE') != PROFILE
            or env.get('PACBENCH_ROLE_ROUTES') != 'luna-all-roles-v1'
            or env.get('PACBENCH_OFFLINE_CONFORMANCE') or os.environ.get('PACBENCH_JUDGE_OUTPUT_RECOVERY')
            or process_identity() != c['controller']):
        raise ValueError('MemCal gateway lacks the bound owner, Luna route or exclusive native tool set')


def projects(root):
    prefix = 'pacbench-' + hashlib.sha256(str(root).encode()).hexdigest()[:12] + '-memcal-'
    return {'memcal': {role: prefix + role for role in ('memory', 'driver', 'judge')}}


@cache_bound_schedule(maxsize=2)
def _memcal_schedule(spec_sha, canary):
    from pacbench.workflows.capability import schedule
    from .contract import verify_contract
    from pacbench.workflows.source_plan import _encode
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
    from pacbench.workflows.capability import campaign_stop, confirm_setting, schedule
    from .site import inspect_memcal_site
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
                  root=str(root), provider='memcal', controller=process_identity(), projects=projects(root),
                  spec_sha256=spec_sha, frozen_inputs=schedule(spec_sha, canary)[2], contract=contract,
                  plan_sha256=plan, site_sha256=digest(asdict(site)), account=site.account,
                  store=str(Path(site.memcal_home).resolve()), imessage_backend=site.imessage_backend,
                  access=actual, canary=canary, prepaid=prepaid, auto_recharge_evidence=auto_recharge_evidence,
                  campaign_stop=str(campaign_stop(root)))
    path = root/'workflow-scope.json'
    with path.open('x') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(record, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return binding(path)


def _progress_scope(progress, c):
    from pacbench.workflows.capability import ENV

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
    from pacbench.workflows.capability import ENV, load, schedule
    from pacbench.workflows.delivery import MemCalReaders
    from .source import MemCalSourceWitness
    from pacbench.workflows.native import NativeAccount, WarmHermes

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
    from ...campaigns.pizza_week import ROOT, load as load_dataset
    from pacbench.corpus.sources import load_bundle
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
            if (local.st_dev,local.st_ino) == (target.st_dev,target.st_ino) and holds_flock(pid,int(path.name)):
                return True
        except FileNotFoundError:
            continue  # /proc includes transient directory enumeration descriptors.
    return False


def _transfer(progress, c, generation, old, audit):
    from pacbench.workflows.capability import ENV

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
    from ...live_dispatch import owns_fresh_effect
    from pacbench.workflows.capability import ENV

    _progress_scope(progress, c)
    activation = progress.binding('memcal_activation') or {}
    claim = progress.active_effect(phase, key, inputs)
    _, owner = _native_owner(progress,c)
    if (process_identity() != owner or activation.get('scope') != json.loads(os.environ[ENV])
            or activation.get('store') != c['store'] or activation['store_file'] != _store_file(c) or not claim or not owns_fresh_effect(progress, claim)):
        raise ValueError('no live owner of this newly dispatched MemCal operation')
    if c.get('admission') == 'pizza-day-synthetic-memcal-v1':
        from ...campaigns.pizza_day import operation_slot
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
    from pacbench.workflows.capability import ENV, load

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
    from pacbench.workflows.capability import ENV
    from pacbench.workflows.progress import WorkflowProgress
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
        if (saved.st_dev, saved.st_ino) != (held.st_dev, held.st_ino) or not holds_flock(owner['pid'],record['fd']):
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


def _judge_slot(scope, project, body):
    from pacbench.workflows.capability import ENV
    from pacbench.workflows.grading import claimed_request
    from pacbench.workflows.progress import WorkflowProgress

    slot = claimed_request(scope, project, body)
    if slot is None:
        raise ValueError('no live MemCal judge for this verified turn')
    directory = Path(scope['root'])/'workflow'
    with WorkflowProgress(directory, slot['checkpoint_effect']['run_key']) as progress:
        _progress_scope(progress, scope)
        if (progress.binding('memcal_activation') or {}).get('scope') != json.loads(os.environ[ENV]):
            raise ValueError('MemCal judge lacks original qualified source scope')
    return slot


def _verify_answer_request(scope, slot, body):
    from pacbench.workflows.capability import native_user_question
    from pacbench.workflows.progress import WorkflowProgress
    from pacbench.workflows.source_plan import SourcePlan

    if scope.get('admission') == 'pizza-day-synthetic-memcal-v1':
        from ...campaigns.pizza_day import QUESTION, QUESTION_AT

        prompt, at = QUESTION, QUESTION_AT
    else:
        turn = _memcal_schedule(scope['spec_sha256'], scope['canary'])[3][slot['question_id']][1]
        prompt, at = turn.prompt, turn.at
    users = [message for message in body['messages'] if message['role'] == 'user']
    last = users[-1].get('content') if users else None
    revision = _memcal_revision(scope)
    tools = body.get('tools', [])
    if revision == MEMCAL_WEEK_REVISION:
        verify_driver_schemas(body.get('tools'))
    if (not native_user_question(last, prompt)
            or sorted(tool['function']['name'] for tool in tools) != list(memcal_week_tools(revision))):
        raise ValueError('MemCal driver question or native tool surface differs')
    directory = Path(scope['root'])/'workflow'
    with WorkflowProgress(directory, slot['checkpoint_effect']['run_key']) as progress:
        plan = SourcePlan.from_connection(progress.db, scope['plan_sha256'])
        plan.verify_no_leak({'model_requests': [body], 'prefetch': None}, at, prompt)


_observer = None


class DreamWaves:
    def __init__(self):
        self.kind = None
        self.expected = 0
        self.finished = set()
        self.extra = None
        self.second_look = False

    def observe(self, event, data):
        if event == 'propose_wave':
            kind = data['kind']
            if self.kind is not None and len(self.finished) != self.expected:
                raise ValueError('Dream opened a wave before its native predecessors completed')
            if kind == 'second-look':
                if self.second_look or self.kind not in ('main','split') or not data['entities']:
                    raise ValueError('Unexpected stock Dream second-look wave')
                self.second_look = True
                self.extra = {'kind':kind,'entities_sha256':digest(data['entities'])}
            else:
                self.extra = None
                if kind == 'main':
                    self.second_look = False
            self.kind = kind
            self.expected = data['requests']
            self.finished = set()
        elif event == 'propose_request':
            index = data['index']
            if not 1 <= index <= self.expected or index in self.finished:
                raise ValueError('Unexpected Dream work-unit completion')
            self.finished.add(index)
            if len(self.finished) == self.expected:
                self.extra = None
        elif event == 'stage' and data['stage'] != 'propose':
            self.extra = None


@contextmanager
def observed_dream():
    global _observer
    if _observer is not None:
        raise ValueError('Only the run-owned Dream may publish native waves')
    _observer = DreamWaves()
    try:
        yield _observer.observe
    finally:
        _observer = None


def native_dream_slot(slot):
    if slot['kind']=='ingest' and _observer is not None and _observer.extra is not None:
        return {**slot,'native_work':_observer.extra}
    return slot


def admit(scope, project, body, *, native_lease=_native_lease):
    from ...openrouter_route import validate_request
    from pacbench.workflows.capability import ENV, PROFILE

    validate_request(body, profile=PROFILE)
    if project == scope['projects']['memcal']['judge']:
        slot = _judge_slot(scope, project, body)
    else:
        slot = native_dream_slot(native_lease(scope, project))
        if slot['kind'] == 'answer':
            _verify_answer_request(scope, slot, body)
    return dict(root=scope['root'], capability=json.loads(os.environ[ENV]), run_id=scope['run_id'],
                slot=slot, projects=[project])


def confirm_sender(c, row, *, native_lease=_native_lease):
    """Recheck callback liveness between preparing the receipt and sending upstream."""
    from pacbench.workflows.capability import ENV

    slot = row['workflow_slot']
    if (c['provider'] != 'memcal' or row['workflow_capability'] != json.loads(os.environ[ENV])
            or row['workflow_root'] != c['root']):
        raise ValueError('prepared request no longer belongs to its MemCal scope')
    if slot['kind'] == 'judge':
        from pacbench.workflows.grading import active_claim
        if not active_claim(c,row['project'],slot['checkpoint_effect']):
            raise ValueError('MemCal judge callback is no longer live')
    else:
        if native_dream_slot(native_lease(c,row['project'])) != slot:
            raise ValueError('MemCal native callback or Dream wave changed before dispatch')
