"""Configuration from environment variables (and an optional local .env file). No secrets are hard-coded."""
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "qwen2.5:7b-instruct"


def load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader. Existing environment variables win. Values are never printed."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ and value:
            os.environ[key] = value


@dataclass
class Settings:
    ollama_base_url: str
    model: str
    db_path: str
    timeout: float
    github_token: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
            model=os.environ.get("DECISIONTRACE_MODEL", DEFAULT_MODEL),
            db_path=os.environ.get("DECISIONTRACE_DB", "decisiontrace.db"),
            timeout=float(os.environ.get("DECISIONTRACE_TIMEOUT", "300")),
            github_token=os.environ.get("GITHUB_TOKEN") or None,
        )
