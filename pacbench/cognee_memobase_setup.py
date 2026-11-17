"""Fail-closed local setup plans for additional native memory providers.

This does not install packages, ingest source, start services or establish recall.
Plans deliberately distinguish an available plugin from a qualified document arm.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

PINS = {
    'holographic': {
        'repository': 'https://github.com/itpartypattaya/hermes-plugin-holographic',
        'commit': '05e890b461dc3e6390982cb21c5b471a6ed3d8b4',
        'capture': 'agent-tools-or-opt-in-user-sentence-extraction',
        'document_ingestion': False,
    },
    'cognee': {
        'repository': 'https://github.com/topoteretes/cognee-integrations',
        'commit': 'a176248914fdb32a78354854b300174203b15a5c',
        'plugin_subdirectory': 'integrations/hermes-agent',
        'backend_requirement': 'cognee==1.6.8',
        'capture': 'typed-session-entries-and-session-end-graph-promotion',
        'document_ingestion': True,
    },
    'memobase': {
        'repository': 'https://github.com/fred0m/hermes-memobase-provider',
        'commit': '4e8e447966acefaea372395e2db96ca0d0127f18',
        'capture': 'paired-turn-chat-buffer-and-session-end-flush',
        'document_ingestion': False,
    },
}


def loopback_url(value: str) -> str:
    """Require a literal loopback endpoint; do not resolve arbitrary DNS names.

    This check protects the memory-service destination, not every downstream
    model configured inside that service. Those require separate verification.
    """
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('A loopback HTTP(S) URL is required')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Credentials, query and fragment are forbidden in service URLs')
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        local = False
    if not local:
        raise ValueError('Memory service must be on loopback, not a hosted endpoint')
    # Accessing .port also rejects malformed/non-numeric port values.
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError('Invalid port')
    return value.rstrip('/')


def setup_plan(provider: str, root: Path, run_id: str, *, service_url: str | None = None) -> dict:
    if provider not in PINS:
        raise ValueError('Unknown provider')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', run_id):
        raise ValueError('run_id must be a non-path identifier of at most 80 characters')
    root = root.expanduser().resolve()
    scope = 'pacbench_' + hashlib.sha256(run_id.encode()).hexdigest()[:20]
    home = root / provider / 'hermes-home'
    plan = {
        'protocol': 'local-provider-qualification-v1', 'provider': provider,
        'run_id': run_id, 'pin': dict(PINS[provider]),
        'qualification': 'not-run',
        'document_condition': 'candidate' if PINS[provider]['document_ingestion'] else 'excluded',
        'root': str(root / provider),
        'environment': {'HERMES_HOME': str(home)},
        'hermes_config': {'memory': {'provider': provider}},
        'required_evidence': [
            'actual loaded provider revision (not only installed revision)',
            'lossless source delivery with ownership and timestamps',
            'native processing completion, not only accepted writes',
            'fresh-session saved model input containing actual prefetch output',
            'all provider tool calls and failures',
            'all extraction/embedding/reranking endpoints and optional cloud fallbacks',
        ],
    }
    if provider == 'cognee':
        if service_url:
            raise ValueError('Cognee plan uses native local mode; do not select remote mode')
        plan['environment'].update({
            'COGNEE_DATA_ROOT': str(root / provider / 'data'),
            'COGNEE_SYSTEM_ROOT': str(root / provider / 'system'),
            'COGNEE_PLUGIN_DATASET': scope,
            'COGNEE_PLUGIN_RECALL_MODE': 'pinned',
        })
        plan['unset_environment'] = ['COGNEE_BASE_URL', 'COGNEE_SERVICE_URL', 'COGNEE_API_KEY']
        plan['configuration'] = {
            'embedded': False, 'service_url': '', 'dataset': scope,
            'recall_mode': 'pinned',
        }
        plan['ingestion'] = {
            'candidate': 'native-raw-document-remember',
            'api': '/api/v1/remember',
            'requires': 'native dataset pipeline status and stored raw-content verification',
        }
        plan['blockers'] = [
            'Isolated native runtime/server, unique local port and inference configuration must be provisioned',
            'Document connector and native completion witnesses still require live qualification',
        ]
    elif provider == 'memobase':
        url = loopback_url(service_url or 'http://127.0.0.1:8019/api/v1')
        plan['environment'].update({
            'MEMOBASE_BASE_URL': url, 'MEMOBASE_USER_NAME': scope,
            'MEMOBASE_TIMEZONE': 'America/New_York',
        })
        plan['configuration'] = {'timezone': 'America/New_York'}
        plan['blockers'] = [
            'Self-hosted PostgreSQL/pgvector, Redis and API must be isolated per run',
            'Native plugin captures paired user/assistant turns; no artificial assistant turns allowed',
            'Server accepts doc blobs but inspected dispatcher extracts only chat/summary blobs',
            'Excluded from the document benchmark until supported document extraction exists',
        ]
    else:
        if service_url:
            raise ValueError('Holographic requires no service URL')
        plan['hermes_config']['plugins'] = {'hermes-memory-store': {
            'db_path': str(root / provider / 'memory_store.db'),
            'embeddings': 'off', 'rerank': 'off',
        }}
        plan['blockers'] = [
            'Older Hermes bundles override the installed standalone provider of the same name',
            'No documented raw-document ingestion; do not seed extracted facts to manufacture support',
            'Excluded from the document benchmark; conversational exposure is not a substitute',
        ]
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider', choices=sorted(PINS))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--service-url')
    args = parser.parse_args(argv)
    print(json.dumps(setup_plan(args.provider, args.root, args.run_id,
                                service_url=args.service_url), indent=2))


if __name__ == '__main__':
    main()
