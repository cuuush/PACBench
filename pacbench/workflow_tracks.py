"""Interfaces identified in the 2026-11-18 provider realism review.

Documentation is a capability claim, not proof of a deployed service or grant.
"""
from dataclasses import asdict, dataclass


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


def validate_native_deployment(condition, record):
    if (not record.get('synthetic_source_provenance') or not record.get('observed_version')
            or record['observed_version'] != condition.version or not record.get('store_identity')
            or record.get('frontend_identity') != condition.frontend
            or record.get('observed_patch') != condition.gateway_patch
            or sorted(record.get('granted_tools', [])) != sorted(condition.tool_names)
            or not record.get('effective_model_route')
            or record['effective_model_route'] != condition.model_route):
        raise ValueError('unqualified native runtime or synthetic source')
    if condition.track == 'first-party-connector':
        if (not record.get('observed_connector_grants') or not record.get('as_of_upstream')
                or ('imessage' in condition.channels and not record.get('platform_created_messages_fixture'))):
            raise ValueError('unverified source grant, cutoff, or platform fixture')
        grants = record['observed_connector_grants']
        if (not isinstance(grants, dict) or set(grants) != set(condition.channels)
                or not all(isinstance(v, list) and v and all(isinstance(g, str) and g.strip() for g in v)
                           for v in grants.values())):
            raise ValueError('missing per-channel connector grants')
    if (condition.version != PROVIDERS[condition.provider].reviewed_version or
            sorted(condition.tool_names) != list(HERMES_TOOLS[condition.provider])):
        raise ValueError('native version or tool grants differ from reviewed integration')
    if condition.model != 'openai/gpt-6-luna' or condition.model_route != 'amazon-bedrock/global':
        raise ValueError('native driver differs from approved model or route')


def eligibility(provider, track, channels=BOTH):
    p = PROVIDERS[provider]
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
            'version_source': p.version_source, 'review_date': '2026-11-18',
            'boundary': p.boundary, 'live_verified': False}
