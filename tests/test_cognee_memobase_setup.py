import json
from pathlib import Path
import unittest

from pacbench.cognee_memobase_setup import PINS, loopback_url, setup_plan


class LocalQualificationTests(unittest.TestCase):
    def test_loopback(self):
        for url in ['http://127.0.0.1:8019/api/v1', 'http://[::1]:9000', 'https://localhost']:
            self.assertEqual(loopback_url(url), url)

    def test_remote_and_secret_urls_rejected(self):
        for url in ['https://api.memobase.io', 'http://127.0.0.1.evil.test',
                    'http://user:secret@localhost', 'http://localhost/?key=secret',
                    'file:///tmp/a', 'http://localhost:bad', 'http://localhost/#key']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                loopback_url(url)

    def test_cognee_roots_and_dataset_isolated(self):
        a = setup_plan('cognee', Path('/tmp/a'), 'pizza-one')
        b = setup_plan('cognee', Path('/tmp/b'), 'pizza-two')
        self.assertNotEqual(a['environment']['COGNEE_PLUGIN_DATASET'], b['environment']['COGNEE_PLUGIN_DATASET'])
        self.assertEqual(a['environment']['COGNEE_PLUGIN_RECALL_MODE'], 'pinned')
        self.assertIn('COGNEE_BASE_URL', a['unset_environment'])
        self.assertTrue(a['environment']['COGNEE_DATA_ROOT'].startswith('/tmp/a/'))
        self.assertEqual(a['qualification'], 'not-run')
        self.assertEqual(a['document_condition'], 'candidate')

    def test_memobase_uses_local_server_and_separate_user(self):
        p = setup_plan('memobase', Path('/tmp/a'), 'pizza')
        self.assertEqual(p['environment']['MEMOBASE_TIMEZONE'], 'America/New_York')
        self.assertFalse(p['pin']['document_ingestion'])
        self.assertEqual(p['document_condition'], 'excluded')
        self.assertNotIn('MEMOBASE_API_KEY', p['environment'])
        with self.assertRaises(ValueError):
            setup_plan('memobase', Path('/tmp/a'), 'pizza', service_url='https://hosted.test')

    def test_holographic_not_misrepresented_as_document_ingestion(self):
        p = setup_plan('holographic', Path('/tmp/a'), 'pizza')
        self.assertFalse(p['pin']['document_ingestion'])
        self.assertEqual(p['document_condition'], 'excluded')
        cfg = p['hermes_config']['plugins']['hermes-memory-store']
        self.assertNotIn('auto_extract', cfg)  # preserve native default
        self.assertEqual(cfg['embeddings'], 'off')
        self.assertEqual(cfg['rerank'], 'off')

    def test_identifier_and_provider_validation(self):
        for run in ['../a', '', '/tmp/a', 'a/b', 'x' * 81]:
            with self.assertRaises(ValueError):
                setup_plan('cognee', Path('/tmp/a'), run)
        with self.assertRaises(ValueError):
            setup_plan('cloud', Path('/tmp/a'), 'ok')

    def test_pins_and_json(self):
        for name, pin in PINS.items():
            self.assertEqual(len(pin['commit']), 40)
            json.dumps(setup_plan(name, Path('/tmp/a'), 'pizza'))
