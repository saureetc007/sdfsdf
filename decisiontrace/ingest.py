"""Import a public GitHub repo's issues/PRs (+ comments, reviews, review comments), extract, persist."""
from dataclasses import dataclass, field

from .github import GitHubError, RateLimitError, build_thread_doc, validate_repo
from .providers import ProviderError


@dataclass
class ImportReport:
    repo: str
    threads_seen: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    extracted: int = 0
    decisions_saved: int = 0
    errors: list = field(default_factory=list)
    aborted: str | None = None
    api_requests: int = 0
    rate_remaining: int | None = None


def import_repository(client, db, extractor, repo, max_items=20, state="all", extract=True) -> ImportReport:
    validate_repo(repo)
    rep = ImportReport(repo)
    repo_id = db.upsert_repository(repo)
    try:
        for item in client.paginate(f"/repos/{repo}/issues", {"state": state, "sort": "updated", "direction": "desc"}, max_items=max_items):
            rep.threads_seen += 1
            n, is_pr = item["number"], "pull_request" in item
            parts = {"comments": [], "reviews": [], "review_comments": []}
            paths = {"comments": f"/repos/{repo}/issues/{n}/comments"}
            if is_pr:
                paths["reviews"] = f"/repos/{repo}/pulls/{n}/reviews"
                paths["review_comments"] = f"/repos/{repo}/pulls/{n}/comments"
            for key, path in paths.items():
                try:
                    parts[key] = list(client.paginate(path))
                except RateLimitError:
                    raise
                except GitHubError as e:  # partial failure: keep what we have, report it
                    rep.errors.append(f"#{n} {key}: {e}")
            doc = build_thread_doc(repo, item, **parts)
            sid, status = db.upsert_source(doc, repo_id)
            setattr(rep, status, getattr(rep, status) + 1)
            if not extract or (status == "unchanged" and db.has_successful_run(sid)):
                continue
            try:
                result = extractor.extract(doc)
            except ProviderError:
                raise  # model unavailable/timeout: abort visibly rather than store empty results
            except Exception as e:
                db.record_failed_run(sid, extractor.provider.model, str(e))
                rep.errors.append(f"#{n} extraction: {e}")
                continue
            rep.extracted += 1
            rep.decisions_saved += db.save_extraction(sid, result)
    except RateLimitError as e:
        rep.aborted = f"rate limit: {e}"
    except ProviderError as e:
        rep.aborted = f"model provider: {e}"
    rep.api_requests, rep.rate_remaining = client.requests_made, client.rate_remaining
    return rep
