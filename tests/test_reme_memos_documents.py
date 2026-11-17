from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from provider_adapters.reme_memos_documents import ingest, payload


class NativeDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.text = "[2026-10-05T18:00:00-04:00] Maya\nPizza Friday at noon?\n"
        (self.source / "messages.txt").write_text(self.text)
        self.manifest = {"at": "2026-10-05T20:00:00-04:00", "source_ids": ["opaque-1"],
                         "document_sha256": hashlib.sha256(self.text.encode()).hexdigest()}
        (self.source / "source.json").write_text(json.dumps(self.manifest))

    def test_reme_preserves_raw_document_without_assistant(self):
        body, receipt = payload("reme", self.source)
        self.assertEqual(body["messages"][0]["content"], self.text)
        self.assertEqual([x["role"] for x in body["messages"]], ["user"])
        self.assertEqual(body["request_id"], body["session_id"])
        self.assertNotIn("memory_hint", body)
        self.assertEqual(receipt["source_ids"], ["opaque-1"])

    def test_memos_is_explicit_raw_source_condition(self):
        body, receipt = payload("memos", self.source)
        trace = body["traces"][0]
        self.assertEqual(trace["userText"], self.text)
        self.assertEqual(trace["summary"], self.text)
        self.assertEqual(trace["agentText"], "")
        self.assertEqual(trace["value"], 0.5)
        self.assertEqual(trace["alpha"], 0.5)
        self.assertEqual(trace["priority"], 0.5)
        self.assertFalse(receipt["extraction_performed"])
        self.assertEqual(receipt["retrieval_credit"], "source-assisted")

    def test_tampered_source_refused(self):
        (self.source / "messages.txt").write_text("tampered")
        with self.assertRaises(ValueError):
            payload("reme", self.source)

    def test_http_success_does_not_override_negative_native_result(self):
        with patch("provider_adapters.reme_memos_documents.request", return_value={"success": False}):
            self.assertFalse(ingest("reme", self.source, "http://127.0.0.1:3456")["ready"])

    def test_memos_requires_exact_readback(self):
        body, _ = payload("memos", self.source)
        acknowledgment = {"imported": 1, "duplicates": 0, "invalid": 0, "failed": 0}
        with patch("provider_adapters.reme_memos_documents.request", side_effect=[acknowledgment, body["traces"][0]]):
            self.assertTrue(ingest("memos", self.source, "http://127.0.0.1:18800")["ready"])
        with patch("provider_adapters.reme_memos_documents.request", side_effect=[acknowledgment, {}]):
            self.assertFalse(ingest("memos", self.source, "http://127.0.0.1:18800")["ready"])

    def test_transport_failure_never_blindly_retries(self):
        with patch("provider_adapters.reme_memos_documents.request", side_effect=TimeoutError) as call:
            receipt = ingest("reme", self.source, "http://127.0.0.1:3456")
        self.assertEqual(call.call_count, 1)
        self.assertFalse(receipt["ready"])
        self.assertEqual(receipt["native_error_class"], "TimeoutError")


if __name__ == "__main__":
    unittest.main()
