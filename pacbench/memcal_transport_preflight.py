"""Exercise stock MemCal collection only, with a frozen mixed bundle and private IMAP."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from . import frozen
from .document_ingestion import prepare_sources
from .imessage_membership import CONDITION
from .native_checkpoint_sources import memcal_archive_readback
from .provider_versions import verify_memcal_source
from .workflow_spec import SPEC, nightly_documents

ROOT = Path(__file__).resolve().parents[1]
COLLECT = """
import json, sys
from pathlib import Path
from memcal import config, db
from contextlib import closing
from provider_adapters.document_connector import collect_memcal_sources, memcal_source_mailbox
cfg = config.load()
source, root = map(Path, sys.argv[1:])
with memcal_source_mailbox(source, root, cfg) as (messages, mailbox):
    cfg.ensure_dirs()
    with closing(db.open_db(cfg.db_path)) as conn:
        result = collect_memcal_sources(source, root, cfg, conn, messages, mailbox)
print(json.dumps(result, default=sorted))
"""


def run(bundle_path, expected_hash, output, server_python):
    pin = verify_memcal_source(ROOT)
    bundle = frozen.load_bundle(bundle_path, expected_hash)
    if len(bundle['lives']) != 1:
        raise ValueError('Expected a single synthetic life')
    records = next(iter(bundle['lives'].values()))['messages']
    if {r['channel'] for r in records} != {'email', 'imessage'}:
        raise ValueError('Expected both email and iMessage')
    spec = json.loads(SPEC.read_text())
    documents = nightly_documents(records, spec['timezone'], spec['nightly_cutoff'])
    releases = {}
    for document in documents:
        releases.setdefault(document.cutoff, set()).update(document.source_ids)
    output = Path(output).resolve()
    output.mkdir(mode=0o700, parents=True)
    schedule = prepare_sources(output, records, spec['timezone'], membership_condition=CONDITION,
                               observed_email_headers_only=True)
    if len(schedule) != len(releases):
        raise ValueError('Fixture days differ from v2 nightly eligibility')
    root = output/'repeat-1/memcal'
    home = root/'fallback-home'
    home.mkdir(parents=True)
    store = root/'memcal-home'
    env = {'HOME':str(home), 'MEMCAL_HOME':str(store), 'PATH':os.defpath,
           'PYTHONPATH':os.pathsep.join([pin['source'], str(ROOT)]),
           'PACBENCH_IMAP_SERVER_PYTHON':str(Path(server_python).absolute()),
           'TZ':'America/New_York', 'LANG':'C.UTF-8'}
    checks = []
    days = []
    for batch, (at, eligible_ids) in zip(schedule, releases.items()):
        day = batch['id']
        if day != at[:10] or set(batch['source_ids']) != eligible_ids:
            raise ValueError('Fixture release differs from canonical nightly eligibility')
        started = datetime.now(timezone.utc).isoformat()
        result = subprocess.run([sys.executable, '-c', COLLECT, str(output/'sources'/day), str(root)],
                                 env={**env, 'MEMCAL_TODAY':at}, cwd=home, capture_output=True,
                                 text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f'Native collection failed on {day}: {result.stderr}')
        receipt = json.loads(result.stdout)
        source_ids = {channel:[r['source_id'] for r in records if r['source_id'] in eligible_ids
                               and r['channel']==channel] for channel in ('email','imessage')}
        expected_mail = len(source_ids['email'])
        operations = [c['operation'] for c in receipt['email_transport']['commands']]
        if ('STARTTLS' not in operations or 'SEARCH' not in operations or ('FETCH' in operations) != bool(expected_mail)
                or receipt['email_ingest']['read'] != expected_mail):
            raise ValueError('Stock IMAP search/fetch evidence differs from the eligible release')
        days.append(day)
        readback = memcal_archive_readback(output, days, store)
        with closing(sqlite3.connect((store/'memcal.db').as_uri()+'?mode=ro', uri=True)) as conn:
            total = conn.execute('SELECT count(*) FROM archive').fetchone()[0]
        if total != sum(len(b['source_ids']) for b in schedule[:len(days)]):
            raise ValueError('Historical archive has missing or extra records')
        item = {'day':day, 'collection_at':at, 'cumulative_archive_count':total,
                'physical_started_utc':started, 'physical_finished_utc':datetime.now(timezone.utc).isoformat(),
                'released_source_ids':source_ids, 'receipt':receipt, 'readback':readback}
        (output/(day+'.json')).write_text(json.dumps(item, indent=2)+'\n')
        checks.append({'day':day, 'scenario_cutoff':at, 'cumulative_archive_count':total,
                       'iMessages':len(source_ids['imessage']),
                       'email':receipt.get('email_ingest',{}).get('read',0)})
    report = {'state':'collection-transport-pass', 'memcal_revision':pin['revision'],
              'pacbench_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
              'bundle_hash':expected_hash, 'batches':checks, 'model_calls':0, 'questions_asked':0,
              'email_transport':'Stock reader against local private pymap Maildir via STARTTLS',
              'v2_nightly_document_count':len(documents),
              'reply_and_address_headers':'source-supplied only; synthetic sender addresses and stable Message-IDs for transport',
              'imessage_transport':'Stock chat.db and Contacts readers against Linux schema fixtures',
              'apple_created_source_verified':False, 'dream_executed':False}
    (output/'summary.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--bundle-hash', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--server-python', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.bundle, args.bundle_hash, args.output, args.server_python), indent=2))


if __name__ == '__main__':
    main()
