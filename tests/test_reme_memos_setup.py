import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pacbench.reme_memos_setup import PINS, local_endpoint, prepare, settings


class LocalProviderSetupTests(unittest.TestCase):
    def test_local_addresses(self):
        for value in ("http://127.0.0.1:11434/v1", "http://localhost:8000/v1", "http://[::1]:8000/v1/"):
            self.assertEqual(local_endpoint(value), value.rstrip("/"))

    def test_reject_remote_or_secret_addresses(self):
        for value in ("https://api.openai.com/v1", "http://localhost.evil/v1", "http://10.0.0.1/v1",
                      "http://key@localhost/v1", "http://localhost/v1?key=x", "http://localhost/v1#x",
                      "file:///tmp/model", "http://localhost:bad/v1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_endpoint(value)

    def test_memos_is_actual_inference_not_local_only(self):
        cfg = settings("memos", Path("/tmp/run"), "http://127.0.0.1:11434/v1", "local-model")
        self.assertEqual(cfg["plugin_config"]["llm"]["provider"], "openai_compatible")
        self.assertFalse(cfg["plugin_config"]["llm"]["fallbackToHost"])
        self.assertFalse(cfg["plugin_config"]["hub"]["enabled"])
        self.assertFalse(cfg["plugin_config"]["telemetry"]["enabled"])
        self.assertTrue(cfg["plugin_config"]["embedding"]["local"]["offline"])

    def test_explicit_model(self):
        with self.assertRaises(ValueError):
            settings("reme", Path("/tmp/run"), "http://127.0.0.1:11434/v1", " ")

    def test_isolated_native_plugin_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            plugin = source / "integrations/hermes_agent"
            plugin.mkdir(parents=True)
            (plugin / "__init__.py").write_text("# native provider")
            root = base / "run"
            with patch("subprocess.check_output", return_value=PINS["reme"][1] + "\n"):
                result = prepare("reme", root, source, "http://127.0.0.1:11434/v1", "local-model")
                self.assertEqual((root / "hermes-home/plugins/reme").resolve(), plugin)
                self.assertEqual((root / "hermes-home/reme/config.json").stat().st_mode & 0o777, 0o600)
                self.assertFalse(result["ingestion_verified"])
                self.assertFalse(result["fresh_session_recall_verified"])
                with self.assertRaises(FileExistsError):
                    prepare("reme", root, source, "http://127.0.0.1:11434/v1", "local-model")

    def test_wrong_pin_refused_before_state_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            with patch("subprocess.check_output", return_value="wrong\n"), self.assertRaises(ValueError):
                prepare("reme", root, Path(directory), "http://127.0.0.1/v1", "local-model")
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
