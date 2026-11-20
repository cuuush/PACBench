"""Start an isolated continuing account against an already-provisioned synthetic site."""
from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from . import harness
from .canary_capability import digest
from .checkpoint_clock import CheckpointClock
from .context_isolation import validate_recall_environment
from .frozen import load_bundle
from .gateway_client import client_settings
from .gateway_reuse import PersistentGateway
from .provider_versions import MEMCAL_REVISION, verify_memcal_source
from .workflow_delivery import MemCalReaders
from .workflow_memcal_chat import MemCalChatWitness
from .workflow_native import NativeAccount, WarmHermes
from .workflow_runner import Condition
from .workflow_spec import ROOT, SPEC
from .prepare import read_spec
from .workflow_tracks import HERMES_TOOLS


def _source_messages():
    spec = read_spec(SPEC)
    dataset = ROOT / spec['dataset']
    manifest = json.loads((dataset/'manifest.json').read_bytes())
    bundle = load_bundle(dataset/manifest['source_bundle'], manifest['source_bundle_hash'])
    return [r for life in bundle['lives'].values() for r in life['messages']]


def _model_config(store, model, base, key):
    from memcal import config
    cfg = config.load(home=store)
    if (cfg.llm_provider != 'openai-compatible' or cfg.fallback_provider or cfg.openai_base_url != base
            or cfg.openai_api_key != key or (cfg.propose_model,cfg.sweep_model,cfg.match_model) != (model,model,model)
            or cfg.publish_calendar or cfg.publish_reminders):
        raise ValueError('Existing MemCal configuration conflicts with the scoped Luna route or enables outward writes')
    return cfg


@contextmanager
def open_account(*, root, site, scope, ledger, upstream):
    """Yield (condition, account). The site supplies release(cutoff), observe(cutoff),
    deployment(), bindings_path, bindings_sha256. Its actual Messages/IMAP consent,
    mapping, and as-of supply must already exist. The site .env is preserved. Omit
    model overrides there or set MEMCAL_LLM_PROVIDER=openai-compatible, the OpenRouter
    /api/v1 base, and the selected Luna model for propose/sweep/match. Leave the
    compatibility API key to the scoped accounting environment, with fallback and
    calendar/reminder publishing disabled. Existing Hermes homes require their original
    warm owner; this factory never recreates a controller or adopts an orphan.
    """
    from . import workflow_capability as cap
    from .native_pilot import memcal_model_settings
    from .openrouter_route import PROFILES
    from .workflow_memcal import inspect_memcal_site
    from .workflow_memcal_live import run_environment
    from .workflow_memcal_source import MemCalSourceWitness

    root = Path(root)
    if (root.resolve() != root or root.name != 'memcal' or Path(site.memcal_home) != root/'memcal-home'
            or os.environ.get('PACBENCH_DIRECT') != '1' or json.loads(os.environ[cap.ENV]) != scope):
        raise ValueError('MemCal factory requires its run-owned existing store and scoped direct transport')
    verified = cap.load()
    if (verified['root'] != str(root) or verified['provider'] != 'memcal' or verified['account'] != site.account
            or verified['store'] != site.memcal_home or verified['site_sha256'] != digest(asdict(site))):
        raise ValueError('workflow capability differs from site account')
    for name in ('release','observe','deployment'):
        if not callable(getattr(upstream,name,None)):
            raise ValueError(f'provisioned synthetic site requires {name}')
    home = root/'hermes-home'
    if home.exists() or home.is_symlink() or (root/'reuse-clock').exists():
        raise ValueError('existing Hermes home or clock requires its original warm owner')
    validate_recall_environment()
    release = verify_memcal_source(harness.REPO)
    if release['revision'] != MEMCAL_REVISION:
        raise ValueError('MemCal requires the reviewed 0.8.11 release')
    # Checks pinned import, dedicated OS home and both real readers, without fetching bodies.
    inspect_memcal_site(site)
    from memcal import db
    documents, _, frozen = cap.schedule(verified['spec_sha256'], verified['canary'])
    messages = _source_messages()
    if frozen != verified['frozen_inputs']:
        raise ValueError('scoped dataset differs from the verified v2 inputs')
    tools = HERMES_TOOLS['memcal']
    profile = PROFILES['luna-bedrock-global']
    model = profile['model']
    roles = verified['projects']['memcal']
    driver_url, driver_key = client_settings(roles['driver'])
    memory_url, memory_key = client_settings(roles['memory'])
    # Never feed a site mailbox credential to the driver. MemCal resolves source
    # settings from the explicitly scoped local store.
    model_env = memcal_model_settings(model, memory_url, memory_key)
    stack = ExitStack()
    try:
        stack.enter_context(run_environment(model_env))
        cfg = _model_config(root/'memcal-home', model, memory_url, memory_key)
        conn = stack.enter_context(closing(db.connect(cfg.db_path)))
        reader = MemCalReaders(conn, cfg, site=site)
        source = MemCalSourceWitness(conn, site, messages, documents,
            bindings_path=upstream.bindings_path, bindings_sha256=upstream.bindings_sha256,
            release=upstream.release, observe=upstream.observe)
        fallback = root/'fallback-home'
        fallback.mkdir(mode=0o700)
        harness.provision_home(home, provider_plugin_src=Path(release['source'])/'integrations/hermes/memcal',
                               provider_name='memcal',model=model,model_provider='openrouter',model_base_url=driver_url)
        env = harness.turn_env(documents[0].cutoff,home,','.join(tools),dict(
            HOME=str(fallback), MEMCAL_HOME=str(cfg.home), MEMCAL_SRC=release['source'],
            PACBENCH_MEMCAL_CONDITION='stable-0.8.11', PACBENCH_PROVIDER_ONLY='1', HERMES_ENABLE_PROJECT_PLUGINS='0',
            PACBENCH_WORKFLOW_CAPTURE='pizza-week-conversation-v2', PACBENCH_API_INPUT_TRACE=str(root/'api-inputs.jsonl'),
            OPENROUTER_API_KEY=driver_key, OPENROUTER_BASE_URL=driver_url, OPENROUTER_API_BASE=driver_url,
            PACBENCH_PREFETCH_PROVENANCE=json.dumps({'protocol':'native-prefetch-provenance-v1','provider':'memcal',
                'state':'unverified','reason':'Stock brief/wiki injection lacks a typed per-block source witness.'}),
            TZ='America/New_York'))
        clock = CheckpointClock(home, documents[0].cutoff, verified['spec_sha256'])
        cwd = Path(stack.enter_context(TemporaryDirectory(prefix='pacbench-memcal-chat-')))
        owner = stack.enter_context(PersistentGateway(home, harness.ISOLATED_BIN.parent/'python', env, cwd=cwd,
                                    checkpoint=verified['spec_sha256'], observation_clock=clock,provider='memcal'))
        witness = MemCalChatWitness(owner,ledger,deployment=upstream.deployment)
        account = NativeAccount(site.account,'memcal',cfg.home,reader,WarmHermes(owner,clock,witness.idle),
                                witness, source_witness=source)
        condition = Condition('memcal','first-party-connector','continuing-account','Hermes '+owner.pins['hermes'],
            model,profile['endpoint'],f'synthetic {site.imessage_backend} and IMAP','0.8.11',owner.pins['hermes_isolation_sha256'],
            'physical connector service clock; scheduled source visibility and simulated Hermes question clock',tools)
        yield condition, account
    except BaseException as primary:
        try:
            stack.close()
        except BaseException as cleanup:
            primary.add_note(f'MemCal account cleanup also failed ({type(cleanup).__name__})')
        raise
    else:
        stack.close()
