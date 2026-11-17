import json
import pytest

from pacbench import openrouter_task as task
from pacbench.openrouter_proxy import Budget, send_openrouter
from pacbench.openrouter_route import MODEL, ENDPOINT


def test_real_transport_cannot_use_another_ledger_or_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(task, 'STATE', tmp_path)
    auth = {'task': 'pacbench-pizza-day-20261112', 'model': MODEL,
            'endpoint': ENDPOINT, 'approved_total_usd': '20'}
    (tmp_path / 'authorization.json').write_text(json.dumps(auth))
    def budget(path, cap='20'):
        return Budget(path, cap=cap, context_tokens=100, prompt_per_million='1',
                      completion_per_million='2', max_completion_tokens=50)
    original = budget(tmp_path / 'ledger.json')
    original.request('fixture', {'model': MODEL}, lambda _: (200, b'{}'))
    task.require_authorization(original)
    for b in [budget(tmp_path / 'other.json'), budget(tmp_path / 'ledger.json', '21')]:
        with pytest.raises(RuntimeError, match='accounting identity'):
            task.require_authorization(b)
    monkeypatch.setattr('urllib.request.build_opener', lambda *a: pytest.fail('network call'))
    with pytest.raises(RuntimeError, match='durable task reservation'):
        send_openrouter({}, 'unused', budget=budget(tmp_path / 'ledger.json'))
    # A missing task ledger cannot silently become a fresh paid budget.
    original.path.unlink()
    with pytest.raises(RuntimeError, match='refusing to replace'):
        task.require_authorization(original)
    b = budget(tmp_path / 'ledger.json')
    with pytest.raises(RuntimeError, match='refusing to replace'):
        b.request('test', {'model': MODEL},
                  lambda req: send_openrouter(req, 'unused', budget=b))
