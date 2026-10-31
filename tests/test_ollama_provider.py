import json
import unittest

from decisiontrace.providers import OllamaProvider, ProviderError, ProviderTimeout, ProviderUnavailable
from decisiontrace.transport import HttpResponse, NetworkError


def provider(handler, model="qwen2.5:7b-instruct"):
    return OllamaProvider("http://localhost:11434", model, 5, transport=handler)


class OllamaProviderTests(unittest.TestCase):
    def test_11_runtime_down_gives_setup_instructions(self):
        def down(*a):
            raise NetworkError("Connection refused")
        for fn in (lambda p: p.check(), lambda p: p.generate_json("s", "u", {})):
            with self.assertRaises(ProviderUnavailable) as cm:
                fn(provider(down))
            self.assertIn("ollama pull qwen2.5:7b-instruct", str(cm.exception))

    def test_15_timeout(self):
        def slow(*a):
            raise NetworkError("timed out", timeout=True)
        with self.assertRaises(ProviderTimeout):
            provider(slow).generate_json("s", "u", {})

    def test_model_not_installed(self):
        tags = HttpResponse(200, {}, json.dumps({"models": [{"name": "llama3.2:3b"}]}).encode())
        with self.assertRaises(ProviderUnavailable) as cm:
            provider(lambda *a: tags).check()
        self.assertIn("llama3.2:3b", str(cm.exception))

    def test_model_installed(self):
        tags = HttpResponse(200, {}, json.dumps({"models": [{"name": "qwen2.5:7b-instruct"}]}).encode())
        provider(lambda *a: tags).check()

    def test_request_shape_and_response(self):
        seen = {}

        def ok(method, url, headers, body, timeout):
            seen.update(method=method, url=url, body=json.loads(body))
            return HttpResponse(200, {}, json.dumps({"message": {"content": '{"decisions": []}'}}).encode())

        out = provider(ok).generate_json("SYS", "USR", {"type": "object"})
        self.assertEqual(out, '{"decisions": []}')
        self.assertTrue(seen["url"].endswith("/api/chat"))
        b = seen["body"]
        self.assertEqual((b["model"], b["stream"], b["format"], b["options"]["temperature"]), ("qwen2.5:7b-instruct", False, {"type": "object"}, 0))

    def test_http_error_and_bad_shape(self):
        with self.assertRaises(ProviderError):
            provider(lambda *a: HttpResponse(500, {}, b"boom")).generate_json("s", "u", {})
        with self.assertRaises(ProviderError):
            provider(lambda *a: HttpResponse(200, {}, b'{"nope": 1}')).generate_json("s", "u", {})
        with self.assertRaises(ProviderUnavailable):
            provider(lambda *a: HttpResponse(404, {}, b"model not found")).generate_json("s", "u", {})


if __name__ == "__main__":
    unittest.main()
