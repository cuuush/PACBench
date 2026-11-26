"""Audit the shared retained evidence for native document canaries."""
from dataclasses import dataclass
from pathlib import Path
from socket import create_connection
from urllib.parse import urlsplit

from .canary_capability import binding, read, sha
from .observational_receipts import RunReceiptJournal
from .openrouter_route import PROFILES
from .workflow_capability import DOCUMENT_ARMS, PROFILE, schedule
from .workflow_metrics import summarize
from .workflow_receipts import receipt_summary
from .workflow_runner import Condition
from .workflow_spec import SPEC, Turn
from .workflow_tracks import validate_native_deployment


@dataclass(frozen=True)
class DocumentCanaryAudit:
    root: Path
    bindings: dict[str, dict[str, str]]
    condition: dict
    deployment: dict
    outcome: tuple[str, str]
    spec_sha256: str
    turns: list[Turn]
    events: list[dict]
    receipts: list[dict]


def _verify_retained_identity(provider: str, scope: dict, result: dict) -> None:
    """Check the identity fields recorded by each retained layout."""
    if provider != 'memos' and scope['provider'] != provider:
        raise ValueError(f'{provider} canary has a different scope provider')
    if provider != 'openviking' and scope.get('admission') != DOCUMENT_ARMS[provider].admission:
        raise ValueError(f'{provider} canary has a different scope admission')
    if provider not in ('mem0', 'hindsight') and result['provider'] != provider:
        raise ValueError(f'{provider} canary has a different result provider')


def audit_document_canary(root: Path, provider: str) -> DocumentCanaryAudit:
    root = Path(root).resolve(strict=True)
    bindings = {'result': binding(root/'result.json'), 'scope': binding(root/'workflow-scope.json')}
    result, scope = read(bindings['result']), read(bindings['scope'])
    spec_sha256 = sha(SPEC)
    documents, turns, frozen = schedule(spec_sha256, True)
    if (root.name != provider or scope['root'] != str(root) or scope['canary'] is not True
            or scope['spec_sha256'] != spec_sha256 or scope['frozen_inputs'] != frozen):
        raise ValueError(f'{provider} canary scope differs from the frozen inputs')
    _verify_retained_identity(provider, scope, result)
    outcome = result['state'], result['cleanup']
    # Hindsight checks the outcome after reconciling shutdown in its provider audit.
    if provider != 'hindsight' and outcome != ('live-canary-complete', 'complete'):
        raise ValueError(f'{provider} canary did not complete successfully')

    events = []
    report = summarize(root/'workflow/workflow.jsonl', events=events)
    if not report or report['incomplete_tail'] is not None or report['status'] != 'completion':
        raise ValueError(f'{provider} canary journal did not reach a complete terminal event')
    condition = events[1]['condition']
    deployment = events[1]['deployment_witness']
    if provider != 'mem0':
        # The retained Mem0 canary carries a separate static plugin/runtime proof.
        validate_native_deployment(Condition(**condition), deployment)

    receipts = RunReceiptJournal(root/'receipts.jsonl').snapshot()
    billing = receipt_summary(root, receipts, scope['run_id'])
    roles = {row['accounting_role'] for row in receipts if row.get('upstream_request_id')}
    coverage = report['coverage']
    if (result['canary'] is not True or result['workflow'] != report or result['billing'] != billing
            or billing['complete_cost_usd'] is None or roles != {'hermes', 'judge', 'memory_provider'}
            or (coverage['verified_turns'], coverage['graded_criteria']) != (4, 12)
            or any(row['driver_attempt_coverage']['coverage'] != 'complete' for row in report['turns'])):
        raise ValueError(f'{provider} requires a complete native and receipted canary')
    if provider != 'memos' and (coverage['planned_turns'], coverage['planned_criteria']) != (4, 12):
        raise ValueError(f'{provider} canary plan has different question or criterion counts')

    if provider != 'memos':
        profile = PROFILES[PROFILE]
        checkout = Path(__file__).resolve().parents[1]
        revision = next(line.split('=', 1)[1] for line in (checkout/'vendor/HERMES_PIN').read_text().splitlines()
                        if line.startswith('HERMES_PIN='))
        if (condition['provider'] != provider or condition['track'] != 'nightly-document'
                or condition['state'] != 'continuing-account' or condition['model'] != profile['model']
                or condition['model_route'] != profile['endpoint'] or condition['frontend'] != 'Hermes '+revision
                or condition['gateway_patch'] != sha(checkout/'patches/hermes-isolated.diff')
                or deployment['account'] != str(root)):
            raise ValueError(f'{provider} canary runtime and route differ from the Week condition')

    expected_questions = [turn.id for turn in turns]
    answer_phase = 'answer-visible' if provider == 'memos' else 'verified-turn'
    if [row['question'] for row in events if row['phase'] == answer_phase] != expected_questions:
        raise ValueError(f'{provider} canary questions differ from the frozen schedule')

    visible = [row for row in events if row['phase'] == 'answer-visible']
    if provider != 'mem0' and (len(visible) != 4
            or sum(row['phase'] == 'chat-closed' for row in events) != 3
            or visible[0]['surface']['session_id'] != visible[3]['surface']['session_id']):
        raise ValueError(f'{provider} canary sessions differ from the frozen schedule')

    sources = [row['evidence'] for row in events if row['phase'] == 'readback']
    ready = [row['ready'] for row in sources]
    if provider == 'mem0':
        ready = [bool(value) for value in ready]
    if ([(row['native_id'], row['submitted_sha256']) for row in sources]
            != [(doc.filename, doc.sha256) for doc in documents]
            or ready != [True]*len(documents)):
        raise ValueError(f'{provider} canary lacks the frozen native source readbacks')
    return DocumentCanaryAudit(root, bindings, condition, deployment, outcome, spec_sha256, turns, events, receipts)


def verify_listener_stopped(url: str, provider: str) -> None:
    endpoint = urlsplit(url)
    if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not endpoint.port:
        raise ValueError(f'Unexpected {provider} service endpoint')
    try:
        connection = create_connection((endpoint.hostname, endpoint.port), timeout=2)
    except ConnectionRefusedError:
        return
    connection.close()
    raise ValueError(f'{provider} canary listener remains active')
