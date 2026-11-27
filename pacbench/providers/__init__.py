"""Native provider selection, Hermes plugin resolution and trial conditions."""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from importlib import import_module

from pacbench.provider_versions import (
    MEMCAL_REVISION,
    MEMCAL_WEEK_REVISION,
    MEMCAL_WEEK_VERSION,
)

ACTIVE_PROVIDERS = ('memcal', 'mem0', 'hindsight', 'supermemory', 'honcho',
                    'openviking', 'reme', 'cognee', 'memos')
RETIRED_PROVIDERS = {
    'byterover': 'ByteRover was removed from the active benchmark at the user\'s direction; historical evidence is retained.',
}


def require_not_retired(provider):
    if provider in RETIRED_PROVIDERS:
        raise ValueError(RETIRED_PROVIDERS[provider])


LIVE_TRIALS = {
    'enabled': True,
    'reason': 'Isolated Hermes gateway/CLI, direct TLS, plugin APIs and persistent authorized OpenRouter accounting verified; every arm still requires native preflight and processing barriers.',
}


def require_live_trials_ready():
    if not LIVE_TRIALS['enabled']:
        raise RuntimeError('Native trials blocked: ' + LIVE_TRIALS['reason'])


def provider_module(name, submodule=None):
    memory = import_module("plugins.memory")
    resolver = getattr(memory, "import_provider_module", None)
    if resolver is not None:
        return resolver(name, submodule)
    suffix = f".{submodule}" if submodule else ""
    return import_module(f"plugins.memory.{name}{suffix}")


@contextmanager
def run_environment(settings):
    previous = {key:os.environ.get(key) for key in settings}
    os.environ.update(settings)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@dataclass(frozen=True)
class Route:
    method: str
    channels: tuple[str, ...]
    access: str
    limitation: str
    reference: str


@dataclass(frozen=True)
class Provider:
    reviewed_version: str
    document: Route | None
    connectors: tuple[Route, ...]
    boundary: str
    version_source: str


BOTH = ('email', 'imessage')


GH = 'https://github.com/'


PYPI = 'https://pypi.org/project/'


def document(method, access, limitation, reference):
    return Route(method, BOTH, access, limitation, reference)


PROVIDERS = {
    'memcal': Provider('0.8.11', None, (
        Route('Messages chatdb or BlueBubbles', ('imessage',), 'Dedicated synthetic Mac Messages account or its BlueBubbles server; scoped Contacts access',
              'Requires platform-produced synthetic data to qualify historical membership.',
              GH+'cuuush/memcal/blob/v0.8.11/docs/sources/imessage.md'),
        Route('IMAP', ('email',), 'read-only synthetic Inbox/Sent mailbox',
              'Reader cursors and real reply headers must be witnessed.',
              GH+'cuuush/memcal/blob/v0.8.11/docs/sources/email.md')),
        'Channel readers; no verified ordinary mixed-document upload.', GH+'cuuush/memcal/releases/tag/v0.8.11'),
    'mem0': Provider('OSS 2.2.1', document('Memory.add', 'OSS application user_id',
        'SDK text input; index is extracted memories, not original-file retrieval.',
        GH+'mem0ai/mem0/tree/94c3fe9f238f3dbf29c9ce98643bd71eb13077cd'), (),
        'Email cookbook requires application ingestion; no verified mailbox connector.', PYPI+'mem0ai/2.2.1/'),
    'hindsight': Provider('1.2.1', document('retain_files', 'bank write/read',
        'Multipart files; await operation completion.', 'https://hindsight.vectorize.io/developer/api/retain'),
        (), 'Bank API; no verified historical mailbox connector.', PYPI+'hindsight-all/1.2.1/'),
    'openviking': Provider('0.5.4.1', document('resources', 'account resource write/read',
        'Upload and wait for hierarchy/index.', GH+'volcengine/OpenViking/blob/989e6c784283b7895b3f0b7db82b5a070e40f33f/docs/en/api/02-resources.md'),
        (), 'VikingBot incoming email/SMTP is a chat channel, not an Inbox/Sent history importer.', PYPI+'openviking/0.5.4.1/'),
    'supermemory': Provider('local server 0.0.9 / SDK 5.0.1', document('add', 'local namespace write/read',
        'Use native processing default; wait for actual readiness.', GH+'supermemoryai/supermemory/tree/server-v0.0.9'), (
        Route('Gmail thread sync', ('email',), 'hosted Max plan and consent for selected labels',
              'Incremental Pub/Sub; local binary does not establish hosted connector parity.',
              GH+'supermemoryai/supermemory/blob/b694e0332ec2816db80bba44dae2d322d1bf41fb/apps/docs/connectors/gmail.mdx'),),
        'No verified iMessage history connector.', GH+'supermemoryai/supermemory/releases/tag/server-v0.0.9'),
    'honcho': Provider('3.6.1 / SDK 2.9.0', None, (
        Route('official Gmail importer', ('email',), 'developer OAuth gmail.readonly on synthetic mailbox',
              'One session per thread, one peer per address; do not infer historical enrollments.',
              GH+'plastic-labs/honcho/blob/ccec469eff7c19c6c02f1e4f7f14a34e2d34a976/docs/v3/guides/gmail.mdx'),),
        'Attributed peer-message ingestion is not bulk document upload. No verified iMessage history connector.',
        GH+'plastic-labs/honcho/releases/tag/v3.6.1'),
    'reme': Provider('0.4.2.2', document('auto_resource', 'write native resource directory',
        'Watcher generates linked cards; Hermes integration has prefetch only.',
        GH+'agentscope-ai/ReMe/tree/36c23674ad8c599dae20b0687af40acd8ecb990e'), (),
        'No verified first-party source account importer.', GH+'agentscope-ai/ReMe/releases/tag/v0.4.2.2'),
    'cognee': Provider('1.6.8 / Hermes 1.4.3', document('remember_permanent', 'dataset write/read',
        'Native multipart remember and processing/readback.',
        GH+'topoteretes/cognee-integrations/tree/a176248914fdb32a78354854b300174203b15a5c'), (
        Route('Gmail incremental sync', ('email',), 'Gmail read-only consent, selected labels',
              'Message documents, Gmail history cursor, updates and deletes; verify installed connector.',
              GH+'topoteretes/cognee/blob/e6b5b5b9c1f1a3056136ddc3c082a0887fd8ace7/cognee-connectors/connectors/gmail/README.md'),),
        'No verified iMessage history connector.', PYPI+'cognee/1.6.8/'),
    'memos': Provider('local npm 2.0.21', None, (),
        'Backup restore plus embedding repair is migration. No verified normal document upload or historical channel sync.',
        'https://www.npmjs.com/package/@memtensor/memos-local-plugin/v/2.0.21'),
}


# Pinned Hermes tool schemas, verified against the allowed and actually offered
# catalogs on the nine reviewed deployments. A new integration needs a new pin.
HERMES_TOOLS = {
    'memcal': ('memcal_activity', 'memcal_add', 'memcal_alias',
        'memcal_answer', 'memcal_conversation', 'memcal_drop',
        'memcal_list_days', 'memcal_list_month', 'memcal_merge',
        'memcal_move_once', 'memcal_note', 'memcal_open',
        'memcal_open_page', 'memcal_open_source', 'memcal_refresh',
        'memcal_reviewed', 'memcal_schedule', 'memcal_search_archive',
        'memcal_search_wiki', 'memcal_todo', 'memcal_update',),
    'mem0': ('mem0_add', 'mem0_delete', 'mem0_search',
        'mem0_update',),
    'hindsight': ('hindsight_recall', 'hindsight_reflect', 'hindsight_retain',),
    'supermemory': ('supermemory-forget', 'supermemory-profile', 'supermemory-read-document',
        'supermemory-save', 'supermemory-search', 'supermemory_forget',
        'supermemory_profile', 'supermemory_read_document', 'supermemory_search',
        'supermemory_store',),
    'honcho': ('honcho_conclude', 'honcho_context', 'honcho_profile',
        'honcho_reasoning', 'honcho_search',),
    'openviking': ('viking_add_resource', 'viking_browse', 'viking_forget',
        'viking_read', 'viking_remember', 'viking_rule_search',
        'viking_search',),
    'reme': (),
    'cognee': ('cognee_code_search', 'cognee_forget', 'cognee_recall',
        'cognee_recap', 'cognee_remember', 'cognee_switch_dataset',),
    'memos': ('memos_environment', 'memos_get', 'memos_search',
        'memos_skill_get', 'memos_skill_list', 'memos_timeline',),
}


MEMOS_OSS_VERSION = 'MemoryOS OSS 2.0.36 / PACBench Hermes REST 1.0.0'


MEMOS_OSS_TOOLS = ('memos_get', 'memos_search')


MEMOS_OSS_LUNA_COMPAT = 'omit-unsupported-top-p-v1'


MEMOS_OSS_PROVIDER = Provider(MEMOS_OSS_VERSION, document(
    'POST /product/add with a file item', 'run-owned local user and MemCube',
    'Sync file parse and source-linked graph readback over the supported REST API.',
    GH+'MemTensor/MemOS/blob/3a16e5fe3048a209f722806f4123fa2e533588fd/src/memos/api/routers/server_router.py#L128'),
    (), 'OSS REST deployment with explicit Hermes capture and recall binding; independent of the npm bridge.',
    GH+'MemTensor/MemOS/releases/tag/v2.0.36')


def memcal_week_tools(revision):
    from .memcal import source as workflow_memcal_tools
    if revision == MEMCAL_REVISION:
        return HERMES_TOOLS['memcal']
    if revision == MEMCAL_WEEK_REVISION:
        return workflow_memcal_tools.TOOLS
    raise ValueError('unqualified MemCal Week revision')


def reviewed_provider(provider, version):
    if provider == 'memos' and version == MEMOS_OSS_VERSION:
        return MEMOS_OSS_PROVIDER
    original = PROVIDERS[provider]
    if provider == 'memcal' and version == MEMCAL_WEEK_VERSION:
        return replace(original, reviewed_version=version,
                       connectors=tuple(replace(r, reference=r.reference.replace('v0.8.11', MEMCAL_WEEK_REVISION))
                                        for r in original.connectors),
                       version_source=GH+'cuuush/memcal/tree/'+MEMCAL_WEEK_REVISION)
    return original


def validate_native_deployment(condition, record):
    from .memcal import source as workflow_memcal_tools
    if (not record.get('synthetic_source_provenance') or not record.get('observed_version')
            or record['observed_version'] != condition.version or not record.get('store_identity')
            or record.get('frontend_identity') != condition.frontend
            or record.get('observed_patch') != condition.gateway_patch
            or sorted(record.get('granted_tools', [])) != sorted(condition.tool_names)
            or not record.get('effective_model_route')
            or record['effective_model_route'] != condition.model_route):
        raise ValueError('unqualified native runtime or synthetic source')
    if condition.track == 'first-party-connector':
        fixture = record.get('platform_created_messages_fixture') or (
            condition.provider == 'memcal' and condition.deployment == 'synthetic chat.db and isolated IMAP'
            and record.get('synthetic_chatdb_fixture'))
        if (not record.get('observed_connector_grants') or not record.get('as_of_upstream')
                or ('imessage' in condition.channels and not fixture)):
            raise ValueError('unverified source grant, cutoff, or platform fixture')
        grants = record['observed_connector_grants']
        if (not isinstance(grants, dict) or set(grants) != set(condition.channels)
                or not all(isinstance(v, list) and v and all(isinstance(g, str) and g.strip() for g in v)
                           for v in grants.values())):
            raise ValueError('missing per-channel connector grants')
    oss = condition.provider == 'memos' and condition.version == MEMOS_OSS_VERSION
    reviewed = reviewed_provider(condition.provider, condition.version)
    tools = MEMOS_OSS_TOOLS if oss else HERMES_TOOLS[condition.provider]
    if condition.provider == 'memcal' and condition.version == MEMCAL_WEEK_VERSION:
        if any(record.get(k) != v for k, v in workflow_memcal_tools.manifest().items()):
            raise ValueError('unqualified MemCal Week product or driver manifest')
        tools = workflow_memcal_tools.TOOLS
    if (condition.version != reviewed.reviewed_version or sorted(condition.tool_names) != list(tools)):
        raise ValueError('native version or tool grants differ from reviewed integration')
    if condition.model != 'openai/gpt-6-luna' or condition.model_route != 'amazon-bedrock/global':
        raise ValueError('native driver differs from approved model or route')


def eligibility(provider, track, channels=BOTH, *, version=None):
    p = reviewed_provider(provider, version)
    if track not in ('nightly-document', 'first-party-connector'):
        raise ValueError('unrecognized ingestion track')
    if not channels or not set(channels) <= set(BOTH):
        raise ValueError('unsupported source scope')
    routes = ([p.document] if p.document else []) if track == 'nightly-document' else p.connectors
    routes = [r for r in routes if set(channels).intersection(r.channels)]
    missing = set(channels).difference(c for r in routes for c in r.channels)
    return {'provider': provider, 'track': track, 'channels': list(channels),
            'documented_for_scope': not missing, 'missing_channels': sorted(missing),
            'routes': [asdict(r) for r in routes], 'reviewed_version': p.reviewed_version,
            'version_source': p.version_source, 'review_date': (
                '2026-11-22' if provider == 'memcal' and version == MEMCAL_WEEK_VERSION else '2026-11-18'),
            'boundary': p.boundary, 'live_verified': False}
