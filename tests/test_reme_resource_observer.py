import hashlib
import json
from types import SimpleNamespace

from provider_adapters.reme_service import record_resource_result


def test_observer_preserves_native_result_without_generating_cards(tmp_path):
    result = {"success": True, "metadata": {"results": [{"success": True,
        "metadata": {"path": "daily/2026-09-10/resource.md",
                     "source_resource": "[[resource/2026-09-10/thread.txt]]"}}]}}
    response = SimpleNamespace(model_dump=lambda **kwargs: result)
    changes = [{"path": "resource/2026-09-10/thread.txt", "change": "added"}]
    record_resource_result(tmp_path / "receipts", job_name="auto_resource",
        response=response, kwargs={"changes": changes}, metadata={"results": result["metadata"]["results"]})
    saved = json.loads(next((tmp_path / "receipts").glob("*.json")).read_text())
    assert saved["response"] == result
    assert saved["changes"] == changes
    assert saved["processing_forced"] is False
    assert saved["response_sha256"] == hashlib.sha256(json.dumps(result,
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert not (tmp_path / "daily").exists()


def test_observer_ignores_other_jobs(tmp_path):
    record_resource_result(tmp_path / "receipts", job_name="auto_memory",
        response=None, kwargs={}, metadata={})
    assert not (tmp_path / "receipts").exists()
