"""Chronological native membership, before unchanged historical message import.

Only Session.add_peers writes native state. SQL is read-only telemetry. A fixed
source roster is required; late discovery and roster transitions are unsupported.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys

from pacbench.followups import save

LEGACY_PROTOCOL = 'honcho-chronological-membership-v1'
PROTOCOL = 'honcho-chronological-membership-v2'
SUPPORTED = {LEGACY_PROTOCOL, PROTOCOL}
ENVIRONMENT = 'PACBENCH_HONCHO_MEMBERSHIP_PROTOCOL'


class EnrollmentError(ValueError):
    """Safe compatibility diagnosis; never contains a native exception body."""


def instant(value):
    clock = datetime.fromisoformat(value)
    if clock.utcoffset() is None:
        raise EnrollmentError('Honcho membership requires offset-aware source clocks')
    return clock


def initial_at(messages):
    """Setup headroom is not membership evidence; actual rows must be checked."""
    return (min(instant(m['written_at']) for m in messages) - timedelta(minutes=5)).isoformat()


def source_peer_id(name, host, *, protocol=PROTOCOL):
    """Native ID encoding only; original sender/roster metadata stays intact.

    Match the shipped Hermes Honcho sanitize_peer_id convention. Reject alias
    collisions before writes rather than merging distinct people.
    """
    if protocol not in SUPPORTED:raise EnrollmentError('Unsupported Honcho membership protocol')
    value=host['peerName'] if name=='Me' else name
    if protocol==LEGACY_PROTOCOL:return value
    value=re.sub(r'[^a-zA-Z0-9_-]','-',value)
    if not value or len(value)>512:raise EnrollmentError('Source identity cannot fit the native peer-ID contract')
    return value


def validate_peer_identities(plans, previous=None):
    identities=dict(previous or {})
    for plan in plans:
        for name,peer in plan.get('peer_ids',{}).items():
            if name in identities and identities[name]!=peer:
                raise EnrollmentError('Source identity mapping changed')
            if any(other!=name and old==peer for other,old in identities.items()):
                raise EnrollmentError('Distinct source identities collide after native peer-ID encoding')
            identities[name]=peer
    return identities


def source_plans(messages, host, *, protocol=PROTOCOL):
    if not messages:
        raise EnrollmentError('Honcho enrollment requires a nonempty source batch')
    groups = {}
    for message in messages:
        identity = (message['channel'], message['thread'])
        groups.setdefault(identity, []).append(message)
    plans = []
    for identity, batch in groups.items():
        roster = None
        for message in batch:
            members = (message.get('structured') or {}).get('participants')
            if (not isinstance(members, list) or not members or
                    any(not isinstance(name, str) or not name.strip() for name in members) or
                    len(set(members)) != len(members) or 'Me' not in members):
                raise EnrollmentError('Honcho chronological enrollment needs an explicit source roster including Me')
            if roster is None:
                roster = set(members)
            if set(members) != roster:
                raise EnrollmentError('Honcho source roster transition needs a separate native enrollment protocol')
            speaker = 'Me' if message['from_owner'] else message['sender']
            if speaker not in roster:
                raise EnrollmentError('Honcho source speaker is absent from declared membership')
        native = {name: source_peer_id(name,host,protocol=protocol) for name in roster}
        if len(set(native.values())) != len(native) or host['aiPeer'] in native.values():
            raise EnrollmentError('Honcho source/native peer identity collision')
        session_id = 'source-' + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        plans.append({'session_id': session_id, 'participants': sorted(roster),
            'source_peers': sorted(native.values()), 'observer': host['aiPeer'],
            'earliest_source_at': min(instant(m['written_at']) for m in batch).isoformat(),
            **({'peer_ids':dict(sorted(native.items()))} if protocol==PROTOCOL else {})})
    validate_peer_identities(plans)
    return sorted(plans, key=lambda p: (instant(p['earliest_source_at']), p['session_id']))


def validate_windows(plans, snapshot, *, allow_missing=False):
    """The native peer_perspective predicate must admit every source event."""
    observed_at = instant(snapshot['db_at'])
    rows = snapshot['memberships']
    required = {(plan['session_id'], peer): instant(plan['earliest_source_at'])
                for plan in plans for peer in [*plan['source_peers'], plan['observer']]}
    seen = set()
    for row in rows:
        identity = (row['session_id'], row['peer_id'])
        if identity not in required or identity in seen:
            raise EnrollmentError('Honcho native membership contains unexpected or duplicate peers')
        seen.add(identity)
        joined = instant(row['joined_at'])
        if row['left_at'] is not None or joined > required[identity] or joined > observed_at:
            raise EnrollmentError('Honcho native membership does not cover the original source timestamps')
    if not allow_missing and seen != set(required):
        raise EnrollmentError('Honcho native membership is missing source participants or observer')
    return set(required) - seen


def validate_roster_continuity(plans, previous):
    updated = dict(previous)
    for plan in plans:
        prior = previous.get(plan['session_id'])
        if prior is not None and prior != plan['participants']:
            raise EnrollmentError('Honcho source roster changed across imported batches')
        updated[plan['session_id']] = plan['participants']
    return updated


def native_snapshot(root, workspace, plans):
    """Read the run-owned database; never update membership or queue rows."""
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"
    sessions = ','.join(literal(p['session_id']) for p in plans) or 'NULL'
    sql = f"""SELECT json_build_object('db_at',clock_timestamp(),
      'memberships',(SELECT coalesce(json_agg(json_build_object(
        'session_id',session_name,'peer_id',peer_name,'joined_at',joined_at,
        'left_at',left_at)), '[]'::json) FROM session_peers
        WHERE workspace_name={literal(workspace)} AND session_name IN ({sessions})))"""
    identity = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:12]
    env = {k:v for k,v in os.environ.items() if k not in {
        'DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'}}
    proc = subprocess.run(['docker','--host=unix:///var/run/docker.sock','exec',
        'pacbench-honcho-db-'+identity,'psql','-U','postgres','-d','postgres',
        '-X','-t','-A','-c',sql], env=env, capture_output=True, text=True, timeout=15)
    if proc.returncode:
        raise RuntimeError('Honcho native membership telemetry unavailable')
    return json.loads(proc.stdout)


def receipt_path(root, source):
    return root/'honcho-enrollment'/(source.name+'.json')


def verify_before_submission(root, source, messages, host):
    """A saved enrollment alone is insufficient: re-read current native rows."""
    plans = source_plans(messages, host)
    receipt = json.loads(receipt_path(root, source).read_text())
    if (receipt.get('protocol') != PROTOCOL or receipt.get('state') != 'enrolled' or
            receipt.get('source_sha256') != hashlib.sha256((source/'messages.json').read_bytes()).hexdigest() or
            receipt.get('plans') != plans):
        raise EnrollmentError('Honcho source enrollment receipt is absent, changed or incomplete')
    current = native_snapshot(root, host['workspace'], plans)
    validate_windows(plans, current)
    return current


def enroll_source(source, root):
    from honcho import Honcho
    from honcho.api_types import SessionPeerConfig
    from pacbench.provider_loader import provider_module
    HonchoClientConfig=provider_module("honcho","client").HonchoClientConfig
    from provider_adapters import honcho_clock
    from pacbench.stop import check_stop

    if os.environ.get(ENVIRONMENT) != PROTOCOL:
        raise EnrollmentError('Honcho chronological enrollment protocol was not declared')
    check_stop(os.environ.get('PACBENCH_STOP_FILE'))
    config = json.loads((root/'hermes-home/honcho.json').read_text())
    if not config['baseUrl'].startswith('http://127.0.0.1:'):
        raise EnrollmentError('Chronological enrollment requires the isolated local Honcho server')
    host = config['hosts']['hermes']
    messages = json.loads((source/'messages.json').read_text())
    plans = source_plans(messages, host)
    path = receipt_path(root, source)
    if path.exists():
        # Reusing completed membership is read-only. Revalidate source bytes,
        # roster and actual native windows before any later message submission.
        current=verify_before_submission(root,source,messages,host)
        return {**json.loads(path.read_text()),'reuse_verified_native_snapshot':current}
    rosters_path = root/'honcho-enrollment-rosters.json'
    rosters = json.loads(rosters_path.read_text()) if rosters_path.exists() else {}
    validate_roster_continuity(plans, rosters)
    identities_path=root/'honcho-peer-identities.json'
    identities=json.loads(identities_path.read_text()) if identities_path.exists() else {}
    identities=validate_peer_identities(plans,identities)
    record = {'protocol':PROTOCOL,'state':'enrolling','ready':False,'plans':plans,
        'source_sha256':hashlib.sha256((source/'messages.json').read_bytes()).hexdigest(),
        'observations':[],'native_writes':'Session.add_peers only','source_messages_submitted':0}
    save(path, record)
    save(identities_path,identities)
    try:
        cfg = HonchoClientConfig.from_global_config()
        client = Honcho(base_url=config['baseUrl'], workspace_id=host['workspace'],
                        environment='local', max_retries=0, timeout=30)
        for plan in plans:
            check_stop(os.environ.get('PACBENCH_STOP_FILE'))
            before = native_snapshot(root, host['workspace'], [plan])
            missing = validate_windows([plan], before, allow_missing=True)
            if missing:
                horizon = instant(plan['earliest_source_at'])
                if instant(before['db_at']) >= horizon:
                    raise EnrollmentError('Honcho enrollment clock already passed the earliest source event; refusing backdating')
                if honcho_clock.enabled(root):
                    clock, _ = honcho_clock.load(root)
                    now=clock.now();target=horizon-timedelta(minutes=5)
                    if now >= horizon:
                        raise EnrollmentError('Honcho enrollment has no chronological headroom')
                    if now<target:honcho_clock.before_turn(root,target.isoformat(),dict(os.environ))
                peers = []
                for name in plan['source_peers']:
                    peer = client.peer(name)
                    peers.append((peer, SessionPeerConfig(observe_me=cfg.user_observe_me,
                        observe_others=cfg.user_observe_others)) if name == host['peerName'] else peer)
                peers.append((client.peer(host['aiPeer']), SessionPeerConfig(
                    observe_me=cfg.ai_observe_me, observe_others=cfg.ai_observe_others)))
                client.session(plan['session_id']).add_peers(peers)
            after = native_snapshot(root, host['workspace'], [plan])
            validate_windows([plan], after)
            record['observations'].append(after)
            rosters[plan['session_id']] = plan['participants']
            save(rosters_path, rosters)
            save(path, record)
        record.update(state='enrolled', ready=True)
        save(path, record)
        return record
    except BaseException as exc:
        record.update(state='failed', ready=False)
        if isinstance(exc, EnrollmentError):
            record['reason'] = str(exc)
        save(path, record)
        raise


def main():
    from provider_adapters.document_connector import native_error_receipt
    from pacbench.stop import OperatorStop
    try:
        result = enroll_source(Path(sys.argv[1]), Path(sys.argv[2]))
        print(json.dumps(result)); return 0
    except OperatorStop:
        print(json.dumps({'ready':False,'protocol':PROTOCOL,'operator_stop':True})); return 75
    except Exception as exc:
        result={'ready':False,'protocol':PROTOCOL,'native_error':native_error_receipt(exc)}
        if isinstance(exc, EnrollmentError):
            result['reason'] = str(exc)
        print(json.dumps(result))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
