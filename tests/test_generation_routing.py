"""Task B regression: Spark generation routes to Meta's Model API only.

One gateway, one credential: `pacbench.generation` and `pacbench.render`
post to `https://api.meta.ai/v1/chat/completions` with the META bearer
credential from the PACBench-owned `.env`. No OpenRouter route remains,
no silent model substitution, and the secret never appears in errors.

Offline: all network calls are mocked; nothing here touches the network
or reads the real `.env` secret.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


META_URL = "https://api.meta.ai/v1/chat/completions"


def _fake_response(payload: dict):
    response = mock.MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    context = mock.MagicMock()
    context.__enter__.return_value = response
    context.__exit__.return_value = False
    return context


def _choices_payload(text="rewritten beat"):
    return {"choices": [{"message": {"content": text}}]}


class GatewayIsMetaOnly(unittest.TestCase):
    def test_generation_gateway_url_is_meta(self):
        from pacbench import generation
        self.assertEqual(generation.GATEWAY_URL, META_URL)

    def test_generation_gateway_matches_driver_base_url(self):
        from pacbench import creds, generation
        self.assertTrue(
            generation.GATEWAY_URL.startswith(creds.BASE_URL.rstrip("/") + "/"),
            f"{generation.GATEWAY_URL} should extend driver BASE_URL {creds.BASE_URL}")

    def test_render_gateway_matches_generation_gateway(self):
        from pacbench import generation
        from pacbench import render
        self.assertEqual(render.META_GATEWAY_URL, generation.GATEWAY_URL)

    def test_no_openrouter_url_in_generation_path(self):
        from pacbench import generation
        from pacbench import render
        for url in (generation.GATEWAY_URL, render.META_GATEWAY_URL):
            self.assertNotIn("openrouter", url)

    def test_default_model_is_native_id(self):
        from pacbench import render
        self.assertEqual(render.DEFAULT_MODEL, "muse-spark-1.3-contributor")
        self.assertFalse(render.DEFAULT_MODEL.startswith("meta/"))


class SparkGeneratorRouting(unittest.TestCase):
    def _capture(self, payload=None, error=None):
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["request"] = request
            seen["timeout"] = timeout
            if error is not None:
                raise error
            return _fake_response(payload)

        return seen, fake_urlopen

    def test_posts_to_meta_with_bearer_credential(self):
        from pacbench.generation import SparkConfig, spark_generator
        seen, fake = self._capture(_choices_payload("hello"))
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            out = spark_generator(SparkConfig(model="muse-spark-1.3-contributor"),
                                  "SECRET-CRED")("prompt")
        self.assertEqual(out, "hello")
        request = seen["request"]
        self.assertEqual(request.full_url, META_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer SECRET-CRED")
        body = json.loads(request.data.decode())
        self.assertEqual(body["model"], "muse-spark-1.3-contributor")

    def test_transport_failure_raises_without_leaking_secret(self):
        from pacbench.generation import GenerationError, SparkConfig, spark_generator
        seen, fake = self._capture(error=OSError("gateway down"))
        generate = spark_generator(SparkConfig(model="muse-spark-1.3-contributor"),
                                   "SECRET-CRED")
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            with self.assertRaises(GenerationError) as ctx:
                generate("prompt")
        self.assertNotIn("SECRET-CRED", str(ctx.exception))
        self.assertIn("muse-spark-1.3-contributor", str(ctx.exception))

    def test_unusable_payload_raises_without_leaking_secret(self):
        from pacbench.generation import GenerationError, SparkConfig, spark_generator
        seen, fake = self._capture({"bogus": True})
        generate = spark_generator(SparkConfig(model="muse-spark-1.3-contributor"),
                                   "SECRET-CRED")
        with mock.patch("urllib.request.urlopen", side_effect=fake):
            with self.assertRaises(GenerationError) as ctx:
                generate("prompt")
        self.assertNotIn("SECRET-CRED", str(ctx.exception))

    def test_generator_from_env_routes_to_meta(self):
        from pacbench import generation
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("MODEL=muse-spark-1.3-contributor\nMETA=SECRET-CRED\n")
            seen, fake = self._capture(_choices_payload("hi"))
            with mock.patch("urllib.request.urlopen", side_effect=fake):
                generate = generation.generator_from_env(env)
                self.assertEqual(generate("prompt"), "hi")
        self.assertEqual(seen["request"].full_url, META_URL)
        self.assertEqual(seen["request"].get_header("Authorization"),
                         "Bearer SECRET-CRED")


class RenderGeneratorRouting(unittest.TestCase):
    def test_meta_generator_posts_to_meta(self):
        from pacbench import render
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["request"] = request
            return _fake_response(_choices_payload("natural rewrite"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            out = render.meta_generator(
                "SECRET-CRED",
                model="muse-spark-1.3-contributor")("Rewrite this.")
        self.assertEqual(out, "natural rewrite")
        self.assertEqual(seen["request"].full_url, META_URL)
        self.assertEqual(seen["request"].get_header("Authorization"),
                         "Bearer SECRET-CRED")

    def test_legacy_openrouter_name_routes_to_meta_unchanged(self):
        """The kept-for-imports alias must hit the Meta gateway, never OpenRouter,
        and must not remap the model (fail closed, no silent substitution)."""
        from pacbench import render
        seen = {}

        def fake_urlopen(request, timeout=None):
            seen["request"] = request
            return _fake_response(_choices_payload("x"))

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            render.openrouter_generator("SECRET-CRED",
                                        model="meta/muse-spark-1.3-contributor")("p")
        self.assertEqual(seen["request"].full_url, META_URL)
        body = json.loads(seen["request"].data.decode())
        self.assertEqual(body["model"], "meta/muse-spark-1.3-contributor")

    def test_load_key_prefers_meta_over_legacy(self):
        from pacbench import render
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("MODEL=muse-spark-1.3-contributor\n"
                           "OPENROUTER_API_KEY=legacy-key\n"
                           "META=new-secret\n")
            self.assertEqual(render.load_key(env), "new-secret")

    def test_load_key_falls_back_to_legacy(self):
        from pacbench import render
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("OPENROUTER_API_KEY=legacy-key\n")
            self.assertEqual(render.load_key(env), "legacy-key")
            env.write_text("sk-or-legacy\n")
            self.assertEqual(render.load_key(env), "sk-or-legacy")

    def test_load_key_missing_is_none(self):
        from pacbench import render
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(render.load_key(Path(tmp) / "nope"))


if __name__ == "__main__":
    unittest.main()
