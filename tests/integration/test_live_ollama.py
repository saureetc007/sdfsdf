"""REAL local-model test. Skipped unless DECISIONTRACE_LIVE=1 and Ollama + the model are available.
Run:  DECISIONTRACE_LIVE=1 python -m unittest tests.integration.test_live_ollama -v   (from the project root)
Model output is non-deterministic: assertions check structural/safety properties, not exact wording."""
import os
import unittest
from pathlib import Path

from decisiontrace.config import Settings
from decisiontrace.extractor import DecisionExtractor, SourceDoc
from decisiontrace.providers import OllamaProvider


@unittest.skipUnless(os.environ.get("DECISIONTRACE_LIVE") == "1", "set DECISIONTRACE_LIVE=1 to run against a real local model")
class LiveOllama(unittest.TestCase):
    def setUp(self):
        s = Settings.from_env()
        self.provider = OllamaProvider(s.ollama_base_url, s.model, s.timeout)
        self.provider.check()

    def test_real_inference_on_demo_text(self):
        text = (Path(__file__).resolve().parents[2] / "examples" / "sample_discussion.txt").read_text(encoding="utf-8")
        res = DecisionExtractor(self.provider).extract(SourceDoc(text=text, is_demo=True))
        print(f"\nmodel={res.model} decisions={len(res.decisions)}")
        for d in res.decisions:
            print(d.status, "|", d.decision, "|", d.evidence_state)
        self.assertTrue(res.raw_responses)
        for d in res.decisions:  # safety invariant: no final status without a verified excerpt
            if d.status in ("accepted", "rejected"):
                self.assertTrue(any(c.state == "verified_excerpt" for c in d.checks))

    def test_no_decision_text_yields_no_accepted_decisions(self):
        res = DecisionExtractor(self.provider).extract(SourceDoc(text="Thanks! I can reproduce this on Linux 6.1. Attaching logs."))
        self.assertEqual([d for d in res.decisions if d.status == "accepted"], [])


if __name__ == "__main__":
    unittest.main()
