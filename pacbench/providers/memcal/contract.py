"""Verify the frozen MemCal sources, evaluation contract and stock driver tools."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json

from ...provider_versions import MEMCAL_WEEK_REVISION, MEMCAL_WEEK_TREE


# Digests of the reviewed 2026-11-19 recovery archive members, including LF.
SOURCE_BINDING = 'a27a6cac448ec525c57eda08b9095ce323de279c6a60d849623b809b8ae2a05a'
SOURCE_INDEX = 'eab426c68f478ed4cfbda11d8a89dfc0ae7c0b5622953f93fa9d0d084f405f39'
CONTINUING_ACCOUNT = '5eed5aea85bbca691d776fe1edf497fcb13285d5f49daff4b3c1049417e7cef0'


def member_digest(value):
    raw = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_contract(docs, turns, source_basis):
    index = [{'name':d.filename, 'cutoff':d.cutoff, 'hash':d.sha256, 'ids':list(d.source_ids)} for d in docs]
    if (source_basis.get('sha256') != SOURCE_BINDING or member_digest(index) != SOURCE_INDEX
            or member_digest([asdict(t) for t in turns]) != CONTINUING_ACCOUNT):
        raise ValueError('Frozen MemCal source releases or continuing-account evaluation differ')
    return dict(source_binding=source_basis['sha256'], source_index_sha256=SOURCE_INDEX,
                contract_sha256=CONTINUING_ACCOUNT, records=sum(len(d.source_ids) for d in docs),
                document_channels=dict(Counter(d.channel for d in docs)),
                nightly_cutoffs=sorted({d.cutoff for d in docs}), turns=len(turns),
                chats=len({t.session for t in turns}), gradable_turns=sum(bool(t.claims) for t in turns),
                criteria=sum(len(t.claims) for t in turns))


def source_preflight(contract, site_path):
    from .site import inspect_memcal_site, MemCalSite, MemCalSiteError
    result = dict(contract=contract, paid_calls=0, provider_answer_calls=0)
    if site_path is None:
        return {**result, 'state':'blocked-before-source-access',
                'reason':'No dedicated synthetic macOS Messages/Contacts or BlueBubbles and IMAP site was supplied.'}
    try:
        with site_path.open() as stream:
            site = MemCalSite(**json.load(stream))
        observation = inspect_memcal_site(site)
    except MemCalSiteError as exc:
        return {**result, 'state':'blocked-at-source-access', 'reason':str(exc)}
    except (OSError, TypeError, ValueError):
        return {**result, 'state':'blocked-at-source-access', 'reason':'Invalid explicit synthetic site declaration.'}
    return {**result, 'state':'source-access-verified', 'source_access':observation}


# SHA-256 of the complete OpenAI function envelopes, sorted by function name.
# UTF-8 JSON, sorted keys, compact separators. Only catalog order is immaterial.
SCHEMAS_SHA256 = 'e75e7a0cedbac49f5c21a363dd96717fbdb47a4fcfa0dd179288d7e27fecd069'
TOOLS = ('memcal_activity', 'memcal_add', 'memcal_alias', 'memcal_answer',
         'memcal_conversation', 'memcal_drop', 'memcal_list_days', 'memcal_list_month',
         'memcal_merge', 'memcal_move_once', 'memcal_note', 'memcal_open',
         'memcal_open_source', 'memcal_refresh', 'memcal_reviewed', 'memcal_schedule',
         'memcal_search_archive', 'memcal_search_wiki', 'memcal_todo', 'memcal_update')


def manifest():
    return dict(memcal_revision=MEMCAL_WEEK_REVISION, memcal_tree=MEMCAL_WEEK_TREE,
                driver_schemas_sha256=SCHEMAS_SHA256)


def verify_driver_schemas(tools):
    if not isinstance(tools, list):
        raise ValueError('missing MemCal driver schemas')
    try:
        ordered = sorted(tools, key=lambda t: t['function']['name'])
        encoded = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('malformed MemCal driver schemas') from exc
    if hashlib.sha256(encoded.encode()).hexdigest() != SCHEMAS_SHA256:
        raise ValueError('MemCal driver schemas differ from the pinned stock manifest')


def verify_captured_driver(request):
    from ...canary_capability import digest
    body = request.get('request')
    if (not isinstance(body, dict) or not request.get('request_id') or not request.get('messages')
            or request.get('canonical_request_sha256') != digest(body)
            or body.get('messages') != request.get('messages') or body.get('model') != request.get('model')
            or sorted(request.get('tools', [])) != list(TOOLS)):
        raise ValueError('MemCal driver capture differs from its recorded request')
    verify_driver_schemas(body.get('tools'))
