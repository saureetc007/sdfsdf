import json
import unittest

from decisiontrace.db import Database
from decisiontrace.extractor import DecisionExtractor
from decisiontrace.github import (GitHubClient, GitHubNetworkError, InvalidRepoError, NotFoundError, RateLimitError, build_thread_doc, validate_repo)
from decisiontrace.ingest import import_repository
from decisiontrace.transport import HttpResponse, NetworkError
from helpers import ScriptedProvider, dec

B = "https://api.github.com"


def ok(data, link=None, **h):
    hdr = {"x-ratelimit-remaining": "50", **h}
    if link:
        hdr["link"] = f'<{link}>; rel="next"'
    return HttpResponse(200, hdr, json.dumps(data).encode())


class FakeGitHub:
    """Routes by URL path. Values may be a response, a list of responses (consumed in order) or an exception."""
    def __init__(self, routes):
        self.routes, self.log = routes, []

    def __call__(self, method, url, headers, body, timeout):
        self.log.append((url, headers))
        path = url.split("?")[0]
        key = url if url in self.routes else path
        r = self.routes.get(key, HttpResponse(404, {}, b"{}"))
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if isinstance(r, Exception):
            raise r
        return r


ISSUE = {"number": 1, "title": "Pick DB", "body": "Which database?", "user": {"login": "alice"}, "created_at": "2024-01-01T00:00:00Z", "html_url": "https://github.com/o/r/issues/1"}
PR = {"number": 2, "title": "Add SQLite", "body": "Implements it", "user": {"login": "bob"}, "created_at": "2024-01-02T00:00:00Z", "html_url": "https://github.com/o/r/pull/2", "pull_request": {}}
COMMENT = {"body": "We decided to use SQLite because it has zero setup.", "user": {"login": "carol"}, "created_at": "t"}


def client(routes, **kw):
    return GitHubClient(transport=FakeGitHub(routes), sleep=lambda s: None, **kw)


class ClientTests(unittest.TestCase):
    def test_invalid_repo_ids(self):
        for bad in ("nope", "a/b/c", "../etc", "a/ b", "", "o/.."):
            with self.assertRaises(InvalidRepoError):
                validate_repo(bad)
        validate_repo("owner/repo.name-1")

    def test_12_pagination_follows_next_link(self):
        c = client({f"{B}/x": ok([1, 2], link=f"{B}/x?page=2"), f"{B}/x?page=2": ok([3])})
        self.assertEqual(list(c.paginate("/x")), [1, 2, 3])

    def test_pagination_respects_max_items(self):
        c = client({f"{B}/x": ok([1, 2, 3], link=f"{B}/x?page=2")})
        self.assertEqual(list(c.paginate("/x", max_items=2)), [1, 2])

    def test_pagination_ignores_foreign_next_link(self):
        c = client({f"{B}/x": ok([1], link="https://evil.example/steal")}, token="SECRET")
        self.assertEqual(list(c.paginate("/x")), [1])
        self.assertEqual(len(c._transport.log), 1)

    def test_token_only_sent_to_github_and_not_in_errors(self):
        c = client({f"{B}/x": ok([])}, token="SECRET")
        list(c.paginate("/x"))
        self.assertEqual(c._transport.log[0][1]["Authorization"], "Bearer SECRET")
        self.assertNotIn("Authorization", c._headers("https://evil.example/"))

    def test_15_404(self):
        with self.assertRaises(NotFoundError):
            client({}).get_json("/repos/o/missing")

    def test_15_rate_limit(self):
        r = HttpResponse(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1893456000"}, b'{"message":"API rate limit exceeded"}')
        with self.assertRaises(RateLimitError) as cm:
            client({f"{B}/x": r}).get_json("/x")
        self.assertIn("resets", str(cm.exception))

    def test_15_network_failure_retries_then_raises(self):
        c = client({f"{B}/x": NetworkError("boom")})
        with self.assertRaises(GitHubNetworkError):
            c.get_json("/x")
        self.assertEqual(c.requests_made, 3)

    def test_15_timeout_is_reported(self):
        with self.assertRaises(GitHubNetworkError) as cm:
            client({f"{B}/x": NetworkError("t", timeout=True)}).get_json("/x")
        self.assertIn("timeout", str(cm.exception))

    def test_5xx_retried_then_succeeds(self):
        c = client({f"{B}/x": [HttpResponse(502, {}, b""), ok([1])]})
        self.assertEqual(list(c.paginate("/x")), [1])

    def test_issue_vs_pr_comment_kinds_distinguished(self):
        doc = build_thread_doc("o/r", PR, comments=[COMMENT], reviews=[{"body": "Looks good", "state": "APPROVED", "user": {"login": "d"}}],
                               review_comments=[{"body": "nit: rename", "path": "a.py", "user": {"login": "e"}}])
        self.assertEqual((doc.source_type, doc.url, doc.source_key), ("pull_request", "https://github.com/o/r/pull/2", "o/r#pull_request:2"))
        for tag in ("[pr_body", "[issue_comment", "[review @d APPROVED", "[review_comment @e a.py"):
            self.assertIn(tag, doc.text)


def routes_for_import(comments_response=None):
    return {
        f"{B}/repos/o/r/issues": ok([ISSUE, PR]),
        f"{B}/repos/o/r/issues/1/comments": comments_response or ok([COMMENT]),
        f"{B}/repos/o/r/issues/2/comments": ok([]),
        f"{B}/repos/o/r/pulls/2/reviews": ok([]),
        f"{B}/repos/o/r/pulls/2/comments": ok([]),
    }


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.prov = ScriptedProvider({"decisions": [dec("Use SQLite", "accepted", ["We decided to use SQLite because it has zero setup."])]},
                                     {"decisions": []})

    def run_import(self, routes, **kw):
        return import_repository(client(routes), self.db, DecisionExtractor(self.prov), "o/r", **kw)

    def test_12_import_then_duplicate_import(self):
        r1 = self.run_import(routes_for_import())
        self.assertEqual((r1.threads_seen, r1.new, r1.extracted, r1.decisions_saved), (2, 2, 2, 1))
        calls = len(self.prov.calls)
        r2 = self.run_import(routes_for_import())
        self.assertEqual((r2.unchanged, r2.new, r2.extracted), (2, 0, 0))
        self.assertEqual(len(self.prov.calls), calls)  # no re-inference on duplicates
        self.assertEqual(len(self.db.search("")), 1)

    def test_trusted_metadata_comes_from_api(self):
        self.run_import(routes_for_import())
        d = self.db.search("SQLite")[0]
        self.assertEqual((d["url"], d["repo"], d["number"]), ("https://github.com/o/r/issues/1", "o/r", 1))

    def test_15_partial_failure_is_reported_and_import_continues(self):
        r = self.run_import(routes_for_import(comments_response=HttpResponse(500, {}, b"x")))
        self.assertEqual(r.threads_seen, 2)
        self.assertTrue(any("#1 comments" in e for e in r.errors))

    def test_15_rate_limit_aborts_visibly(self):
        rl = HttpResponse(403, {"x-ratelimit-remaining": "0"}, b'{"message":"rate limit"}')
        r = self.run_import(routes_for_import(comments_response=rl))
        self.assertIn("rate limit", r.aborted)

    def test_no_extract_mode_needs_no_model(self):
        r = import_repository(client(routes_for_import()), self.db, None, "o/r", extract=False)
        self.assertEqual((r.new, r.extracted), (2, 0))

    def test_model_outage_aborts_instead_of_storing_empty_results(self):
        from decisiontrace.providers import ProviderUnavailable

        class Down(ScriptedProvider):
            def generate_json(self, *a):
                raise ProviderUnavailable("down")
        r = import_repository(client(routes_for_import()), self.db, DecisionExtractor(Down()), "o/r")
        self.assertIn("model provider", r.aborted)
        self.assertEqual(self.db.search(""), [])


if __name__ == "__main__":
    unittest.main()
