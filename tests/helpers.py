import json

from decisiontrace.providers import ModelProvider


class ScriptedProvider(ModelProvider):
    """MOCK model: returns scripted raw text. Tests built on it exercise pipeline handling, NOT model quality."""
    name, model = "scripted-mock", "mock-model"

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def check(self):
        pass

    def generate_json(self, system, user, schema):
        self.calls.append((system, user))
        r = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return r if isinstance(r, str) else json.dumps(r)


def dec(decision, status, evidence=(), reasons=(), alternatives=(), **kw):
    return {"decision": decision, "status": status, "evidence": list(evidence), "reasons": list(reasons), "alternatives": list(alternatives), **kw}
