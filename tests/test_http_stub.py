"""Exercises the REAL urllib transport + OllamaProvider against a local STUB HTTP server (not a real Ollama/model).
Proves the wire format/transport code works; says nothing about model quality."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from decisiontrace.extractor import DecisionExtractor, SourceDoc
from decisiontrace.providers import OllamaProvider, ProviderTimeout, ProviderUnavailable

TEXT = "We decided to use SQLite for storage because it needs zero setup."


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, {"models": [{"name": "stub-model:latest"}]})

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        req = json.loads(self.rfile.read(n))
        if req["model"] == "slow":
            import time
            time.sleep(1.5)
        out = {"decisions": [{"decision": "Use SQLite", "status": "accepted", "evidence": [TEXT]}]}
        self._send(200, {"message": {"role": "assistant", "content": json.dumps(out)}})


class HttpStub(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Stub)
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_end_to_end_over_real_http(self):
        p = OllamaProvider(self.url, "stub-model", 5)
        p.check()
        res = DecisionExtractor(p).extract(SourceDoc(text=TEXT))
        self.assertEqual((res.decisions[0].status, res.decisions[0].evidence_state), ("accepted", "excerpt_verified"))

    def test_real_timeout(self):
        with self.assertRaises(ProviderTimeout):
            OllamaProvider(self.url, "slow", 0.3).generate_json("s", "u", {})

    def test_connection_refused(self):
        with self.assertRaises(ProviderUnavailable):
            OllamaProvider("http://127.0.0.1:9", "m", 2).check()


if __name__ == "__main__":
    unittest.main()
