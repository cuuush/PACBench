"""Verify the reusable source and evaluator identities for MemCal Pizza Week v2."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json

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
    from .workflow_memcal import inspect_memcal_site, MemCalSite, MemCalSiteError
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
