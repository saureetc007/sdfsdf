"""Model-provider abstraction + Ollama implementation. The extractor only depends on ModelProvider."""
import json

from .transport import NetworkError, urllib_transport


class ProviderError(Exception):
    pass


class ProviderUnavailable(ProviderError):
    """Runtime not reachable or model not installed. Message contains setup instructions."""


class ProviderTimeout(ProviderError):
    pass


def setup_help(base_url: str, model: str) -> str:
    return (
        f"Local model runtime not reachable at {base_url}.\n"
        "  1. Install Ollama: https://ollama.com/download (Linux: curl -fsSL https://ollama.com/install.sh | sh)\n"
        "  2. Start it:       ollama serve      (the desktop app starts it automatically)\n"
        f"  3. Download model: ollama pull {model}\n"
        "  4. Re-run:         python -m decisiontrace check-model\n"
        "Override the runtime or model with OLLAMA_BASE_URL / DECISIONTRACE_MODEL."
    )


class ModelProvider:
    name = "abstract"
    model = ""

    def check(self) -> None:
        """Raise ProviderUnavailable if the runtime/model cannot be used."""
        raise NotImplementedError

    def generate_json(self, system: str, user: str, schema: dict) -> str:
        """Return the model's raw text response (expected to be JSON matching schema)."""
        raise NotImplementedError


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: float = 300, transport=urllib_transport):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._transport = transport

    def _call(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            return self._transport(method, self.base_url + path, headers, body, self.timeout)
        except NetworkError as e:
            if e.timeout:
                raise ProviderTimeout(f"Model request timed out after {self.timeout}s. Try a smaller model or raise DECISIONTRACE_TIMEOUT.") from e
            raise ProviderUnavailable(setup_help(self.base_url, self.model) + f"\n(network error: {e})") from e

    def check(self) -> None:
        r = self._call("GET", "/api/tags")
        if r.status != 200:
            raise ProviderError(f"Ollama /api/tags returned HTTP {r.status}")
        try:
            names = [m.get("name", "") for m in r.body and json.loads(r.body).get("models", [])]
        except (ValueError, AttributeError) as e:
            raise ProviderError(f"Unexpected response from Ollama /api/tags: {e}") from e
        want = self.model
        if not any(n == want or n == want + ":latest" or (":" not in want and n.startswith(want + ":")) for n in names):
            raise ProviderUnavailable(
                f"Ollama is running but model '{want}' is not installed.\n  Run: ollama pull {want}\n  Installed: {', '.join(names) or '(none)'}"
            )

    def generate_json(self, system: str, user: str, schema: dict) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
        }
        r = self._call("POST", "/api/chat", payload)
        if r.status == 404:
            raise ProviderUnavailable(f"Ollama returned 404 (model '{self.model}' probably not installed). Run: ollama pull {self.model}")
        if r.status != 200:
            raise ProviderError(f"Ollama returned HTTP {r.status}: {r.text()[:300]}")
        try:
            return json.loads(r.body)["message"]["content"]
        except (ValueError, KeyError, TypeError) as e:
            raise ProviderError(f"Unexpected Ollama response shape: {e}") from e
