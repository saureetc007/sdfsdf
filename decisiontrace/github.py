"""GitHub REST ingestion (public repos, unauthenticated by default). Supports: issues, pull requests,
issue comments, PR reviews, PR review comments. NOT supported: GitHub Discussions (GraphQL-only)."""
import json
import re
import time
from urllib.parse import urlencode

from .extractor import SourceDoc
from .transport import NetworkError, urllib_transport

API = "https://api.github.com"
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")


class GitHubError(Exception):
    pass


class InvalidRepoError(GitHubError):
    pass


class NotFoundError(GitHubError):
    pass


class RateLimitError(GitHubError):
    def __init__(self, message, reset_at=None):
        super().__init__(message)
        self.reset_at = reset_at


class GitHubNetworkError(GitHubError):
    pass


def validate_repo(repo: str) -> str:
    if not isinstance(repo, str) or not REPO_RE.match(repo) or repo.startswith(".") or ".." in repo:
        raise InvalidRepoError(f"Invalid repository '{repo}'. Expected 'owner/name'.")
    return repo


class GitHubClient:
    def __init__(self, token=None, transport=urllib_transport, timeout=30, retries=2, sleep=time.sleep, base=API):
        self._token, self._transport, self.timeout = token, transport, timeout
        self.retries, self._sleep, self.base = retries, sleep, base
        self.requests_made = 0
        self.rate_remaining = None

    def _headers(self, url):
        h = {"Accept": "application/vnd.github+json", "User-Agent": "decisiontrace/0.1", "X-GitHub-Api-Version": "2022-11-28"}
        if self._token and url.startswith(self.base):  # never send the token to another host
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _get(self, url):
        last = None
        for attempt in range(self.retries + 1):
            if attempt:
                self._sleep(min(2 ** attempt, 8))
            try:
                self.requests_made += 1
                r = self._transport("GET", url, self._headers(url), None, self.timeout)
            except NetworkError as e:
                last = GitHubNetworkError(f"Network {'timeout' if e.timeout else 'failure'} contacting GitHub: {e}")
                continue
            rem = r.headers.get("x-ratelimit-remaining")
            if rem is not None and rem.isdigit():
                self.rate_remaining = int(rem)
            if r.status == 200:
                try:
                    return json.loads(r.body), r.headers
                except ValueError as e:
                    raise GitHubError(f"GitHub returned invalid JSON: {e}") from e
            if r.status == 404:
                raise NotFoundError(f"Not found: {url}")
            if r.status in (403, 429):
                low = r.text().lower()
                if rem == "0" or "rate limit" in low or "retry-after" in r.headers:
                    reset = r.headers.get("x-ratelimit-reset")
                    when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(reset))) if reset and reset.isdigit() else "later"
                    raise RateLimitError(f"GitHub rate limit reached; resets {when}. Set GITHUB_TOKEN for a higher limit.", reset)
                raise GitHubError(f"GitHub denied access (HTTP {r.status}): {r.text()[:200]}")
            if r.status >= 500:
                last = GitHubError(f"GitHub server error HTTP {r.status}")
                continue
            raise GitHubError(f"GitHub HTTP {r.status}: {r.text()[:200]}")
        raise last

    def get_json(self, path):
        return self._get(self.base + path)[0]

    def paginate(self, path, params=None, max_items=None):
        q = {"per_page": 100, **(params or {})}
        url, count = f"{self.base}{path}?{urlencode(q)}", 0
        while url:
            data, headers = self._get(url)
            if not isinstance(data, list):
                raise GitHubError(f"Expected a list from {path}")
            for item in data:
                yield item
                count += 1
                if max_items and count >= max_items:
                    return
            m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("link", ""))
            url = m.group(1) if m and m.group(1).startswith(self.base) else None


def _seg(tag, user, ts, body, extra=""):
    return f"[{tag} @{user or 'unknown'}{(' ' + extra) if extra else ''} {ts or ''}]\n{(body or '').strip()}"


def build_thread_doc(repo, item, comments=(), reviews=(), review_comments=()) -> SourceDoc:
    """Combine an issue/PR and its comments into one source text. Segment tags distinguish comment kinds.
    Metadata (url, author, number) comes only from the API payload, never from the model."""
    is_pr = "pull_request" in item or "head" in item
    kind = "pull_request" if is_pr else "issue"
    segs = [f"# {item.get('title', '')}"]
    if (item.get("body") or "").strip():
        segs.append(_seg("pr_body" if is_pr else "issue_body", (item.get("user") or {}).get("login"), item.get("created_at"), item["body"]))
    for c in comments:
        if (c.get("body") or "").strip():
            segs.append(_seg("issue_comment", (c.get("user") or {}).get("login"), c.get("created_at"), c["body"]))
    for r in reviews:
        if (r.get("body") or "").strip():
            segs.append(_seg("review", (r.get("user") or {}).get("login"), r.get("submitted_at"), r["body"], extra=r.get("state", "")))
    for c in review_comments:
        if (c.get("body") or "").strip():
            segs.append(_seg("review_comment", (c.get("user") or {}).get("login"), c.get("created_at"), c["body"], extra=c.get("path", "")))
    num = item["number"]
    return SourceDoc(
        text="\n\n".join(segs), source_type=kind, source_key=f"{repo}#{kind}:{num}", repo=repo, number=num,
        url=item.get("html_url"), author=(item.get("user") or {}).get("login"), created_at=item.get("created_at"), title=item.get("title"),
    )
