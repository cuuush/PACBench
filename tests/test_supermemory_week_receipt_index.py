"""Exercise the real Week receipt gate using local synthetic journals."""

import json

from pacbench.providers.supermemory_evidence import ADMISSION_SPEC
from tests.receipt_fixtures import JournalFixture as WeekJournal

def driver(question, tool_rounds=()):
    return {'question_id': question, 'supermemory_week_claim': 'claim',
            'supermemory_first_question': {'tool_rounds': list(tool_rounds)}}

def receipt(identifier, slot, role='memory_provider'):
    return {'id': identifier, 'project': 'fixture', 'accounting_role': role, 'finished': 'done',
            'http_status': 200, 'dispatch_execution': 'response_ended', 'dispatch_owner': 'owner',
            'upstream_request_id': f'gen-{identifier}', 'state': 'settled', 'workflow_slot': slot,
            'response_validation': {'envelope_errors': [], 'wire_parse_errors': 0, 'finish_reasons': ['tool_calls']},
            'stock_worker_tool_response': {'generation_id': f'gen-{identifier}', 'reply': {'verified': 0}}}

def seed(path, rows):
    with path.open('w') as stream:
        for row in rows:
            stream.write(json.dumps({'kind': 'prepared', 'id': row['id'], 'row': row}) + '\n')
    journal = WeekJournal(path)
    with journal.journal() as (attempts, _):
        attempts.enable_admission(ADMISSION_SPEC)
    return journal

def test_week_scopes_use_prepared_order_after_an_older_attempt_changes(tmp_path):
    first = receipt('z-first', driver('active'), 'hermes')
    latest = receipt('a-latest', driver('active', ['first']), 'hermes')
    journal = seed(tmp_path / 'receipts.jsonl', [first, latest])
    with journal.journal() as (rows, append):
        append('classification', first['id'], {'state': 'settled'})
        assert rows.last_for_scope('driver_question', 'active')['id'] == latest['id']
