from types import SimpleNamespace
import unittest
from provider_adapters.cognee_local import QualificationError, qualify_document


class Backend:
    url = 'http://127.0.0.1:8011'
    def __init__(self, statuses=None):
        self.statuses = iter(statuses or ['', 'DATASET_PROCESSING_STARTED', 'DATASET_PROCESSING_COMPLETED'])
        self.writes = []
        self.text = ''
    def ensure_dataset(self, **kw): pass
    def list_datasets(self, **kw): return [{'id': 'ds', 'name': 'pacbench_test'}]
    def list_dataset_data(self, **kw): return [{'id': 'doc'}] if self.writes else []
    def dataset_pipeline_status(self, **kw):
        assert kw['pipeline'] == 'cognify_pipeline'
        return next(self.statuses)
    def remember_permanent(self, **kw):
        self.writes.append(kw)
        self.text = kw['text']
        return SimpleNamespace(raw={'status': 'running'})
    def read_raw_data(self, **kw): return self.text


class CogneeNativeDocumentTests(unittest.TestCase):
    def run_case(self, backend, **kwargs):
        return qualify_document(backend, service_url=backend.url, dataset='pacbench_test',
                                text='[2026-10-05] Maya\nPizza Friday?\n', sleep=lambda _: None,
                                **kwargs)
    def test_native_document_preserved_no_fake_turns_or_hints(self):
        b = Backend()
        r = self.run_case(b)
        self.assertEqual(len(b.writes), 1)
        self.assertEqual(b.writes[0]['session_ids'], [])
        self.assertEqual(b.text, '[2026-10-05] Maya\nPizza Friday?\n')
        self.assertTrue(r['processing_complete'])
        self.assertFalse(r['provider_qualified'])
        self.assertEqual(r['fresh_session_recall'], 'not-run')
    def test_rejects_stale_pipeline_even_with_empty_dataset(self):
        b = Backend(['DATASET_PROCESSING_COMPLETED'])
        with self.assertRaises(QualificationError): self.run_case(b)
        self.assertEqual(b.writes, [])
    def test_rejects_existing_data(self):
        b = Backend()
        b.writes.append({})
        with self.assertRaises(QualificationError): self.run_case(b)
        self.assertEqual(len(b.writes), 1)
    def test_native_failure_is_not_readiness(self):
        b = Backend(['', 'DATASET_PROCESSING_ERRORED'])
        with self.assertRaises(QualificationError): self.run_case(b)
        self.assertEqual(len(b.writes), 1)
    def test_write_timeout_is_not_retried(self):
        b = Backend()
        def write(**kw):
            b.writes.append(kw)
            raise TimeoutError()
        b.remember_permanent = write
        with self.assertRaises(TimeoutError): self.run_case(b)
        self.assertEqual(len(b.writes), 1)
    def test_missing_status_not_assumed_completed(self):
        b = Backend()
        b.remember_permanent = lambda **kw: SimpleNamespace(raw={})
        with self.assertRaises(QualificationError): self.run_case(b)
    def test_source_mismatch_blocks(self):
        b = Backend()
        b.read_raw_data = lambda **kw: 'changed'
        with self.assertRaises(QualificationError): self.run_case(b)
    def test_remote_backend_refused_even_if_declaration_is_local(self):
        b = Backend()
        b.url = 'https://cloud.example'
        with self.assertRaises(ValueError):
            qualify_document(b, service_url='http://127.0.0.1:8011',
                             dataset='pacbench_test', text='x')
    def test_deadline_preserved(self):
        b = Backend(['', ''])
        now = iter([0, 0, 0, 0, 0, 0, 0, 3])
        with self.assertRaises(QualificationError):
            self.run_case(b, clock=lambda: next(now), timeout=2)
