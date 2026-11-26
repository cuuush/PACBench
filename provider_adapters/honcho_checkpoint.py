"""Create and verify a private logical backup of a sealed Pizza Week Honcho checkpoint."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from pacbench.gateway_reuse import atomic
from pacbench.campaigns.pizza_week import ORIGINAL, load
from provider_adapters.honcho_local import PGVECTOR_IMAGE, verify_persisted_messages

DOCKER = ['docker', '--host=unix:///var/run/docker.sock']
TABLES = ('workspaces', 'peers', 'sessions', 'session_peers', 'messages',
          'message_embeddings', 'collections', 'documents', 'document_sources',
          'conclusion_source_messages', 'queue', 'active_queue_sessions',
          'queue_item_batches', 'dream_runs', 'dream_run_changes')


def docker(*args, **kwargs):
    return subprocess.run([*DOCKER, *args], check=True, timeout=180, **kwargs)


def sql(container, database, query):
    result = docker('exec', container, 'psql', '-XAt', '-v', 'ON_ERROR_STOP=1',
                    '-U', 'postgres', '-d', database, '-c', query, capture_output=True)
    return result.stdout.decode().strip()


def native_state(container, database, acknowledgments):
    messages = json.loads(sql(container, database, """SELECT json_agg(json_build_object(
        'id', public_id, 'session_id', session_name, 'peer_id', peer_name,
        'content', content, 'created_at', created_at, 'metadata', metadata))
        FROM messages"""))
    ids = verify_persisted_messages(acknowledgments, messages)
    # One PostgreSQL transaction sees a consistent snapshot for all the pinned
    # tables. The row encoding, C sort order and LF separators match the
    # original per-table readback. Only digests leave this process.
    parts = [f"""SELECT '{table}' AS tab, coalesce(string_agg(
                row_to_json(t)::text, chr(10) ORDER BY row_to_json(t)::text COLLATE "C"), '') AS rows
                FROM {table} t""" for table in TABLES]
    query = 'SELECT json_object_agg(tab, rows) FROM (' + ' UNION ALL '.join(parts) + ') AS snapshots'
    values = json.loads(sql(container, database, query))
    inventory = {table:hashlib.sha256(values[table].encode()).hexdigest() for table in TABLES}
    return {'source_count': len(ids), 'source_ids': ids, 'tables_sha256': inventory}


def run(prior, destination, dataset):
    prior = prior.resolve(strict=True)
    ownership = json.loads((prior/'native-container-ownership.json').read_text())
    record = json.loads((prior/'first-checkpoint-ingest.json').read_text())
    qualified = json.loads((prior/'qualification.json').read_text())
    _, schedule, _, _ = load(dataset)
    if (ownership['run_root'] != str(prior) or ownership['data_bind_mount'] != str(prior/'honcho-db')
            or qualified['frozen_inputs']['sha256'] != ORIGINAL or not qualified['native_ready']
            or not record['ready'] or record['source_ids'] != schedule[0]['source_ids']
            or record['document_sha256'] != schedule[0]['document_sha256']):
        raise ValueError('Original database ownership, frozen source, or native readiness differs')
    return archive_source_store(prior, destination, record, prior/'first-checkpoint-ingest.json')


def archive_source_store(prior, destination, record, source_receipt):
    ownership = json.loads((prior/'native-container-ownership.json').read_text())
    if ownership['run_root'] != str(prior) or ownership['data_bind_mount'] != str(prior/'honcho-db'):
        raise ValueError('unverified PostgreSQL data ownership')
    old = docker('ps', '-q', '--filter', 'label=pacbench.import-root='+str(prior), capture_output=True)
    if old.stdout.strip():
        raise ValueError('Original checkpoint has a running owner')
    destination.mkdir(mode=0o700, exist_ok=False)
    archive = destination/'checkpoint.pgdump'
    started = time.monotonic()
    # Only a temporary copy is started. PostgreSQL reads its unchanged access configuration.
    # The original PGDATA is mounted read-only and the container has no network.
    bootstrap = """set -eu
    test ! -e /sealed/postmaster.pid
    mkdir -m 700 /tmp/checkpoint
    cp -a /sealed/. /tmp/checkpoint/
    exec postgres -D /tmp/checkpoint -c listen_addresses='' -c unix_socket_directories=/tmp
    """
    container = docker('run', '--rm', '-d', '--network', 'none', '--user', '999:999',
            '--label', 'pacbench.honcho-checkpoint='+str(destination.resolve()),
            '--mount', f'type=bind,src={prior}/honcho-db,dst=/sealed,readonly',
            '-e', 'PGHOST=/tmp', '--entrypoint', 'sh', PGVECTOR_IMAGE, '-c', bootstrap,
            capture_output=True).stdout.decode().strip()
    try:
        for _ in range(100):
            result = subprocess.run([*DOCKER, 'exec', container, 'pg_isready', '-U', 'postgres'],
                                    capture_output=True, timeout=10)
            if result.returncode == 0:
                break
            if result.returncode >= 125 or b'No such container' in result.stderr:
                raise RuntimeError('Private copy of sealed PostgreSQL failed to start')
            time.sleep(.3)
        else:
            raise RuntimeError('Private snapshot startup deadline exceeded')
        baseline = native_state(container, 'postgres', record['result']['result'])
        workspace = sql(container, 'postgres', 'SELECT name FROM workspaces')
        if workspace != 'pacbench-'+hashlib.sha256(str(prior).encode()).hexdigest()[:12]:
            raise ValueError('Sealed database does not contain exactly the owned workspace')
        with archive.open('xb') as out:
            docker('exec', container, 'pg_dump', '-Fc', '-U', 'postgres', 'postgres', stdout=out)
        with archive.open('rb') as saved:
            digest = hashlib.file_digest(saved, 'sha256').hexdigest()
        restored = {}
        for name in ('observation_a', 'observation_b'):
            docker('exec', container, 'createdb', '-T', 'template0', '-U', 'postgres', name)
            with archive.open('rb') as src:
                docker('exec', '-i', container, 'pg_restore', '--exit-on-error', '-U', 'postgres',
                       '-d', name, stdin=src)
            state = native_state(container, name, record['result']['result'])
            if state != baseline:
                raise ValueError('Restored checkpoint failed exact native identity/readback')
            restored[name] = state
        atomic(destination/'native-snapshot.json', {'state':'two-independent-restore-readbacks',
            'workspace':workspace, 'source_receipt':str(source_receipt),
            'archive_sha256':digest, 'baseline':baseline, 'restored':restored,
            'elapsed_s':time.monotonic()-started, 'warm_hermes_turns':0,
            'native_write_isolation':'unverified'})
    finally:
        subprocess.run([*DOCKER, 'stop', '-t', '15', container], stdout=subprocess.DEVNULL, timeout=30)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('prior', 'destination', 'dataset'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    run(args.prior, args.destination, args.dataset)


def verify(proof, schedule, ids):
    receipt = json.loads(Path(proof['native_closure']['evidence'][0]['path']).read_text())
    baseline = receipt['baseline']
    bindings = proof['ingestion_receipts']
    paths = [bindings[day]['path'] for day in ids]
    expected = sorted(m['id'] for path in paths
                      for ack in json.loads(Path(path).read_text())['result']['result']
                      for m in ack['messages'])
    if (ids != [b['id'] for b in schedule[:len(ids)]] or len(expected) != sum(len(b['source_ids']) for b in schedule[:len(ids)]) or baseline['source_ids'] != expected
            or receipt['state'] != 'two-independent-restore-readbacks'
            or set(receipt['restored']) != {'observation_a','observation_b'}
            or any(value != baseline for value in receipt['restored'].values())
            or set(baseline['tables_sha256']) != set(TABLES)
            or paths != receipt.get('source_receipts',[receipt['source_receipt']])
            or receipt['archive_sha256'] != proof['manifest']['files']['checkpoint.pgdump']):
        raise ValueError('Honcho native logical checkpoint evidence differs')


def bind_profile(home, observation, endpoint):
    path = Path(home)/'honcho.json'
    config = json.loads(path.read_text())
    host = config['hosts']['hermes']
    if (host['sessionStrategy'] != 'per-session' or host['observationMode'] != 'directional'
            or host['workspace'] != observation['honcho_workspace']):
        raise ValueError('Honcho native per-session/workspace condition differs')
    config['baseUrl'] = endpoint
    atomic(path, config)


def verify_turn(observation, turn, drain):
    native = next((p for s in drain['sessions'] if s['session_id'] == turn.session_id
                   for p in s['providers'] if p['name'] == 'honcho'), None)
    if not native or (native['endpoint'], native['workspace']) != (
            observation['endpoint'], observation['honcho_workspace']):
        raise ValueError('Stock Honcho manager did not bind the restored workspace endpoint')
    return verify_persisted_turn(observation, turn, native['workspace'], expected_session=native['session_key'])


def verify_persisted_turn(observation, turn, workspace, *, expected_session=None):
    from datetime import datetime, timedelta
    from .honcho_clock import snapshot, step_if_idle
    root = Path(observation['service_root'])
    container = json.loads((root/'provider-clock.json').read_text())['container']
    rows = json.loads(sql(container, 'postgres', """SELECT coalesce(json_agg(json_build_object(
        'id',public_id,'author',peer_name,'session',session_name,'content',content) ORDER BY id),
        '[]'::json) FROM messages WHERE session_name NOT LIKE 'source-%'"""))
    from dataclasses import asdict
    from pacbench.native_writes import describe_captures, write_inputs, honcho_status
    from .honcho_local import processing_snapshot,queue_errors
    inputs=write_inputs([asdict(turn)],'honcho')
    home=Path(observation.get('hermes_home',root.parent/'checkpoint-1/hermes-home'))
    captured=describe_captures(home.parent,inputs)
    desc=captured['captures'][0]
    native_id=desc['session']
    if ((expected_session is not None and expected_session!=native_id) or captured['workspace']!=workspace
            or any(row['session']!=native_id for row in rows)):
        raise ValueError('Honcho observation contains a foreign native session')
    chunks=desc['chunks']
    suffix=rows[-len(chunks):] if chunks else []
    if not chunks or [(r['author'],r['content']) for r in suffix]!=[(m['peer'],m['content']) for m in chunks]:
        raise ValueError('Honcho native capture differs from its stock two-sided transform')
    ids=[row['id'] for row in suffix]
    captures={native_id:{'hermes_session':turn.session_id,'input_bindings':[desc['binding']],
                         'capture_chunks':[{'binding':desc['binding'],'chunks':chunks}],
                         'conclusions':[dict(w,observer_id=captured['assistant'],
                                       observed_id=captured['owner'] if w['peer']=='user' else captured['assistant'])
                                       for w in inputs['conclusions']]}}
    witnesses={}
    deadline = time.monotonic()+240
    while time.monotonic()<deadline:
        status = snapshot(root, ids)
        if status['errors']:
            raise ValueError('Honcho native write processing failed')
        seen = {r['id'] for r in status['messages'] if r['processed'] and not r['has_error']}
        if seen == set(ids) and not any(status[k] for k in ('pending','claims','embeddings')):
            states=honcho_status(observation['endpoint'],workspace,captured['owner'],captures,
                    lambda:queue_errors(root),read_processing=lambda:processing_snapshot(root,workspace,captures),
                    witnesses=witnesses)
            if not all(row['status']=='completed' and row['explicit_writes_verified'] for row in states):
                raise ValueError('Honcho causal representation/explicit conclusion witness incomplete')
            return {'captured_ids':ids,'native_session':native_id,'status':status,'capture_witnesses':states}
        if status.get('next_eligible_at') and not any(status[k] for k in ('claims','eligible','embeddings')):
            step_if_idle(root, datetime.fromisoformat(status['next_eligible_at'])+timedelta(milliseconds=10),
                         ids=ids)
        time.sleep(.5)
    raise TimeoutError('Honcho write capture was not completely processed within the canary window')


def restore(container, checkpoint):
    path = Path(checkpoint['archive'])
    proof = json.loads((path.parent/'native-snapshot.json').read_text())
    if (hashlib.sha256(path.read_bytes()).hexdigest() != proof['archive_sha256']
            or checkpoint['workspace'] != proof['workspace']):
        raise ValueError('Honcho logical backup identity differs')
    with path.open('rb') as source:
        docker('exec','-i',container,'pg_restore','--exit-on-error','-U','postgres','-d','postgres',
               stdin=source)
    acks = [ack for path in proof.get('source_receipts',[proof['source_receipt']])
            for ack in json.loads(Path(path).read_text())['result']['result']]
    if native_state(container, 'postgres', acks) != proof['baseline']:
        raise ValueError('Observation PostgreSQL differs from the sealed source checkpoint')
