"""Exercise the shared entry points for the fixed workflow protocol routes."""
from importlib import import_module
import json
from types import ModuleType
from unittest.mock import Mock

import pytest

from pacbench.workflows import capability as cap
from pacbench.observational_receipts import ENV as RECEIPT_JOURNAL
from pacbench.observational_receipts import IndexedRunReceiptJournal, RunReceiptJournal


@pytest.fixture
def bind_scope(tmp_path, monkeypatch):
    verification = tmp_path/'prepaid-verification'
    verification.mkdir()
    receipt = verification/'fixture.json'
    receipt.write_text(json.dumps({'data': {'total_credits': 10, 'total_usage': 2}}))
    receipt_sha = cap.sha(receipt)
    receipt.rename(verification/(receipt_sha+'.private.json'))
    confirmation = tmp_path/'recharge.json'
    confirmation.write_text(json.dumps(dict(auto_recharge='off', confirmed_by='test',
                                            confirmed_at='2026-11-26T00:00:00+00:00')))
    scope_file = tmp_path/'workflow-scope.json'
    monkeypatch.setenv(RECEIPT_JOURNAL, str(tmp_path/'receipts.jsonl'))
    monkeypatch.setenv('PACBENCH_OPENROUTER_PROFILE', cap.PROFILE)

    def bind(protocol, **extra):
        scope = dict(root=str(tmp_path), provider='reme', projects={'reme': {'judge': 'test-judge'}},
                     admission=protocol, prepaid=dict(receipt_sha256=receipt_sha, remaining_usd='8',
                                                      source='OpenRouter GET /api/v1/credits'),
                     auto_recharge_evidence=cap.binding(confirmation))
        scope.update(extra)
        scope_file.write_text(json.dumps(scope))
        bound = cap.binding(scope_file)
        monkeypatch.setenv(cap.ENV, json.dumps(bound))
        return scope, bound

    return bind


@pytest.mark.parametrize(('protocol', 'module_name', 'journal'), [
    ('supermemory-stock-source-qualification-v1', 'providers.supermemory.source_admission', 'SourceJournal'),
    ('supermemory-stock-first-question-v1', 'providers.supermemory.question_admission', 'QuestionJournal'),
    ('supermemory-stock-document-week-v1', 'providers.supermemory.week_admission', 'WeekJournal'),
    ('pizza-day-native-document-v1', 'campaigns.pizza_day', None),
    ('luna-judge-development-ab-v1', 'judging.development_live', 'JudgeJournal'),
    ('pizza-week-luna-explicit-overlay-v1', 'cohort_regrade_live', 'OverlayJournal'),
    ('pizza-week-honcho-authored-chats-luna-v4-v1', 'providers.honcho.regrade_live', 'OverlayJournal'),
    ('pizza-week-question-contract-development-shadow-v1', 'judging.shadow_live', 'ShadowJournal'),
    ('pizza-week-question-contract-span-regression-v2', 'judging.shadow_live', 'SpanJournal'),
    ('luna-judge-development-challenge-v2', 'judging.calibration_live', 'ChatJournal'),
    ('pizza-day-wrong-answer-judge-v1', 'campaigns.pizza_day_review', None),
    ('pizza-day-synthetic-memcal-v1', 'campaigns.pizza_day', None),
    ('honcho-seven-daily-documents-v1', 'providers.honcho.daily_gate', None),
])
def test_special_protocol_routes(bind_scope, tmp_path, monkeypatch, protocol, module_name, journal):
    module = import_module(f'pacbench.{module_name}')
    synthetic_memcal = protocol == 'pizza-day-synthetic-memcal-v1'
    day_lineup = protocol == 'pizza-day-native-document-v1'
    honcho_native = module_name == 'providers.honcho.daily_gate'
    extra = {'provider': 'memcal', 'projects': {'memcal': {'judge': 'test-judge'}}} if synthetic_memcal else {}
    scope, bound = bind_scope(protocol, **extra)

    validator = Mock()
    monkeypatch.setattr(module, 'validate_memcal' if synthetic_memcal else 'validate_lineup' if day_lineup else 'validate', validator)
    applicant = Mock(return_value={'special': protocol})
    if synthetic_memcal:
        monkeypatch.setattr(cap, '_memcal_admission', applicant)
    else:
        monkeypatch.setattr(module, 'admission' if honcho_native else 'admit_lineup' if day_lineup else 'admit', applicant)

    assert cap.load() == scope
    validator.assert_called_once_with(scope, bound)
    receipt = cap.budget(project='test-judge', profile=cap.PROFILE)
    if synthetic_memcal:
        expected_journal = IndexedRunReceiptJournal
    elif journal is None:
        expected_journal = RunReceiptJournal
    else:
        journal_module = import_module('pacbench.cohort_regrade_live') if journal == 'OverlayJournal' else module
        expected_journal = getattr(journal_module, journal)
    assert type(receipt) is expected_journal
    assert receipt.path == tmp_path/'receipts.jsonl'

    body = {'messages': []}
    assert cap.admission('test-judge', cap.PROFILE, body) == {'special': protocol}
    args = (scope, 'test-judge', cap.PROFILE, body) if honcho_native else (scope, 'test-judge', body)
    applicant.assert_called_once_with(*args)
    assert validator.call_count == 3

    with pytest.raises(ValueError, match='unexpected workflow role or model route'):
        cap.admission('unbound-project', cap.PROFILE, body)
    with pytest.raises(ValueError, match='unexpected workflow role or model route'):
        cap.admission('test-judge', 'deepseek-flex', body)
    with pytest.raises(ValueError, match='workflow receipt scope'):
        cap.budget(project='unbound-project', profile=cap.PROFILE)
    applicant.assert_called_once()

    if synthetic_memcal or day_lineup:
        bind_scope(protocol, **extra, campaign_stop=str(tmp_path/'different-stop'))
        with pytest.raises(ValueError, match='campaign stop differs'):
            cap.load()


def test_unknown_protocol_never_becomes_an_import_target(tmp_path, monkeypatch):
    root = tmp_path/'reme'
    root.mkdir()
    bound = cap.create(root, 'reme', canary=True, prepaid={}, auto_recharge_evidence={})
    scope = cap.read(bound)
    scope['admission'] = 'pacbench.providers.supermemory.week_admission'
    path = root/'workflow-scope.json'
    path.write_text(json.dumps(scope))
    monkeypatch.setenv(cap.ENV, json.dumps(cap.binding(path)))
    importer = Mock(side_effect=AssertionError('untrusted protocol attempted an import'))
    monkeypatch.setattr(cap, 'import_module', importer)

    with pytest.raises(ValueError, match='unbound native reme continuing-account scope'):
        cap.load()
    with pytest.raises(ValueError, match='unbound native reme continuing-account scope'):
        cap.budget()
    with pytest.raises(ValueError, match='unbound native reme continuing-account scope'):
        cap.admission('test-judge', cap.PROFILE, {})
    importer.assert_not_called()


def test_added_protocol_uses_one_declaration(bind_scope, monkeypatch):
    import sys

    class FixtureJournal(RunReceiptJournal):
        pass

    scope, bound = bind_scope('offline-fixture-v1')
    module = ModuleType('pacbench.workflow_fixture')
    module.validate = Mock()
    module.admit = Mock(return_value={'fixture': True})
    module.FixtureJournal = FixtureJournal
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setitem(cap._SPECIAL_ADMISSIONS, scope['admission'],
                        cap._SpecialAdmission('.workflow_fixture', 'FixtureJournal'))

    assert cap.load() == scope
    module.validate.assert_called_once_with(scope, bound)
    assert type(cap.budget()) is FixtureJournal
    assert cap.admission('test-judge', cap.PROFILE, {}) == {'fixture': True}
    module.admit.assert_called_once_with(scope, 'test-judge', {})
