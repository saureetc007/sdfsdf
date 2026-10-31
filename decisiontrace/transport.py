"""Minimal HTTP transport on urllib. Injectable so tests never touch the network."""
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field


class NetworkError(Exception):
    def __init__(self, message: str, timeout: bool = False):
        super().__init__(message)
        self.timeout = timeout


@dataclass
class HttpResponse:
    status: int
    headers: dict = field(default_factory=dict)  # lower-cased keys
    body: bytes = b""

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> HttpResponse:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return HttpResponse(r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
    except urllib.error.HTTPError as e:
        return HttpResponse(e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b"")
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        reason = getattr(e, "reason", e)
        is_timeout = isinstance(e, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError))
        raise NetworkError(str(reason), timeout=is_timeout) from e
