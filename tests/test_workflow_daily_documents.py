from datetime import datetime, timedelta
from hashlib import sha256
import json

import pytest

from pacbench.frozen import load_bundle
from pacbench.pizza_week_dataset import ROOT
from pacbench.workflow_daily_documents import compile_daily_week, daily_documents, message_metadata
from pacbench.workflow_spec import Document, compile_week


def test_seven_verified_source_files_retain_the_original_evaluation():
    spec, docs, turns, basis, manifest = compile_daily_week()
    _, original, frozen_turns, frozen_basis = compile_week('continuing-account')
    assert turns == frozen_turns and basis == frozen_basis
    assert len(docs) == 7 and len(original) == 48 and len(turns) == 49
    assert sum(len(turn.claims) for turn in turns) == 192
    assert [len(d.source_ids) for d in docs] == [39, 23, 27, 24, 9, 10, 10]
    assert [d.cutoff for d in docs] == list(dict.fromkeys(doc.cutoff for doc in original))
    assert manifest['source_records_by_channel'] == {'imessage': 120, 'email': 22}
    assert manifest['normalized_index_sha256'] == '55e204958b1c38d0dc4177d47789bc78473c44b96f183a554f98390bb5b22ce9'
    root = ROOT / spec['dataset']
    source = json.loads((root / 'manifest.json').read_text())
    bundle = load_bundle(root / source['source_bundle'], source['source_bundle_hash'])
    rows = {r['source_id']: r for life in bundle['lives'].values() for r in life['messages']}
    correction = manifest['verified_mail_history_exclusion']
    for daily in docs:
        records = [rows[key] for key in daily.source_ids]
        assert records == sorted(records, key=lambda row: (datetime.fromisoformat(row['written_at']), row['source_id']))
        # Exact reconstruction rules out a title, carried-forward history, or any
        # additional record outside the original day's ordered message blocks.
        assert daily.text == '\n\n'.join(
            json.dumps(message_metadata(row), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            + '\n' + (row['text'].encode()[:correction['retained_authored_bytes']].decode()
                      if row['source_id'] == correction['source_id'] else row['text']) for row in records) + '\n'
    cutoff = rows[correction['source_id']]['written_at'][:10]
    file = next(doc for doc in docs if doc.filename.startswith(cutoff))
    assert rows[correction['quoted_prior_source_id']]['text'] not in file.text
    assert rows['message-70eaf4efec7abbe141b4813e']['text'] in file.text
    text = file.bodies[file.source_ids.index(correction['source_id'])]
    assert sha256(text.encode()).hexdigest() == correction['projected_body_sha256']


def fixture_row(text, day, ident):
    return dict(source_id=ident, sender='Jo', from_owner=False, channel='imessage', thread='duo',
                written_at=f'2026-10-{day}T09:00:00-04:00', arrived_at=f'2026-10-{day}T09:00:00-04:00',
                structured={'participants': ['Me', 'Jo']}, text=text)


def export(row, day):
    return Document('original.txt', f'2026-10-{day}T21:00:00-04:00', row['channel'], row['text'],
                    (row['source_id'],), (row['text'],))


def test_rejects_prior_day_backfill_even_if_the_frozen_release_contains_it():
    row = fixture_row('Late standalone history.', '05', 'source-1')
    row['arrived_at'] = (datetime.fromisoformat(row['arrived_at']) + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match='prior-day backfill'):
        daily_documents([export(row, '06')], [row], 'America/New_York')


def test_authored_quotation_stays_and_standalone_prior_day_message_does_not():
    old = fixture_row('The earlier whole message with an extra sentence.', '05', 'old')
    new = fixture_row('They wrote this:\n> An actual authored quote.\n\n  Reply with spacing. ', '06', 'new')
    docs = daily_documents([export(old, '05'), export(new, '06')], [old, new], 'America/New_York')
    assert [doc.source_ids for doc in docs] == [('old',), ('new',)]
    assert docs[1].text.partition('\n')[2].encode() == new['text'].encode() + b'\n'
    assert old['text'] not in docs[1].text
    with pytest.raises(ValueError, match='added records'):
        daily_documents([export(new, '06')], [old, new], 'America/New_York')
