import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .db import Database
from .extractor import DecisionExtractor, ExtractionError, SourceDoc
from .github import GitHubClient, GitHubError
from .ingest import import_repository
from .pr_context import find_relevant
from .providers import OllamaProvider, ProviderError

BADGE = {
    "excerpt_verified": "[VERIFIED SOURCE EXCERPT]", "partially_verified": "[PARTIAL - UNCERTAIN INTERPRETATION]",
    "missing_evidence": "[MISSING EVIDENCE]", "unsupported": "[UNSUPPORTED CLAIM]", "unchecked": "[NOT VERIFIED]",
}


def _provider(s):
    return OllamaProvider(s.ollama_base_url, s.model, s.timeout)


def print_decision(d, out=sys.stdout):
    g = (lambda k: d[k]) if isinstance(d, dict) else None
    if isinstance(d, dict):
        status, text, state, reasons, alts = d["status"], d["decision"], d["evidence_state"], d["reasons"], d["alternatives"]
        ev = [(e["excerpt"], e["state"]) for e in d["evidence"]]
        src = f"{d.get('repo') or ''} {d['source_key']} {d.get('url') or ''}".strip()
        flags, qs, conf = d["flags"], d["unresolved"], d["conflicts"]
        alts = [(a["name"], bool(a["rejected"]), a["rejection_reason"]) for a in alts]
    else:
        status, text, state, reasons, flags = d.status, d.decision, d.evidence_state, d.reasons, d.flags
        ev = [(c.excerpt, c.state) for c in d.checks]
        alts = [(a.name, a.rejected, a.rejection_reason) for a in d.alternatives]
        qs, conf, src = d.unresolved_questions, d.conflicting_positions, ""
    p = lambda s="": print(s, file=out)
    p(f"* [{status.upper()}] {text}  {BADGE[state]}")
    if src:
        p(f"    source: {src}")
    for r in reasons:
        p(f"    reason (model-stated): {r}")
    for n, rej, why in alts:
        p(f"    alternative: {n}" + (" (REJECTED" + (f": {why}" if why else "; reason not documented") + ")" if rej else ""))
    for q in qs:
        p(f"    open question: {q}")
    for c in conf:
        p(f"    conflicting position: {c}")
    for ex, st in ev:
        p(f"    evidence [{'verified excerpt' if st == 'verified_excerpt' else 'NOT FOUND IN SOURCE' if st == 'not_found' else 'REJECTED (too short)'}]: \"{ex}\"")
    if not ev:
        p("    evidence: none provided by model")
    for f in flags:
        p(f"    flag: {f}")


def cmd_check_model(a, s):
    pr = _provider(s)
    pr.check()
    print(f"OK: Ollama reachable at {s.ollama_base_url}; model '{s.model}' is installed.")
    print("(This checks availability only. Run `python -m decisiontrace analyze --demo` for a real inference request.)")


def cmd_analyze(a, s):
    if a.demo:
        text = (Path(__file__).resolve().parent.parent / "examples" / "sample_discussion.txt").read_text(encoding="utf-8")
        print("NOTE: using built-in DEMO text (fictional).", file=sys.stderr)
    elif a.file == "-":
        text = sys.stdin.read()
    elif a.file:
        text = Path(a.file).read_text(encoding="utf-8")
    else:
        raise SystemExit("Provide a FILE, '-' for stdin, or --demo")
    if not text.strip():
        raise SystemExit("Input text is empty.")
    prov = _provider(s)
    prov.check()
    doc = SourceDoc(text=text, is_demo=a.demo, repo=a.repo)
    res = DecisionExtractor(prov).extract(doc)
    if a.json:
        print(json.dumps([{**d.__dict__, "alternatives": [x.__dict__ for x in d.alternatives], "checks": [c.__dict__ for c in d.checks], "label": d.label} for d in res.decisions], indent=2))
    else:
        print(f"Model: {res.model}   Decisions found: {len(res.decisions)}")
        if not res.decisions:
            print("No supported engineering decisions found in this text.")
        for d in res.decisions:
            print_decision(d)
        for n in res.notes:
            print(f"  validation: {n.kind}: {n.detail}")
    if a.save:
        db = Database(s.db_path)
        rid = db.upsert_repository(a.repo) if a.repo else None
        sid, st = db.upsert_source(doc, rid)
        print(f"Saved: {db.save_extraction(sid, res)} new decision(s) (source {st}).")


def cmd_import(a, s):
    client = GitHubClient(token=s.github_token)
    db = Database(s.db_path)
    ext = None
    if not a.no_extract:
        prov = _provider(s)
        prov.check()
        ext = DecisionExtractor(prov)
    r = import_repository(client, db, ext, a.repo, max_items=a.max_items, state=a.state, extract=not a.no_extract)
    print(f"{r.repo}: threads={r.threads_seen} new={r.new} updated={r.updated} unchanged={r.unchanged} extracted={r.extracted} decisions_saved={r.decisions_saved}")
    print(f"API requests: {r.api_requests}; rate limit remaining: {r.rate_remaining if r.rate_remaining is not None else 'unknown'}")
    for e in r.errors:
        print(f"  error: {e}")
    if r.aborted:
        print(f"ABORTED: {r.aborted}")
        return 2
    return 1 if r.errors else 0


def cmd_search(a, s):
    rows = Database(s.db_path).search(a.query, repo=a.repo, status=a.status, limit=a.limit, verified_only=a.verified_only)
    print(f"{len(rows)} result(s)")
    for d in rows:
        print_decision(d)


def cmd_pr_context(a, s):
    db = Database(s.db_path)
    if a.text_file:
        text, key = Path(a.text_file).read_text(encoding="utf-8"), None
    else:
        if not a.repo or not a.pr:
            raise SystemExit("Provide --text-file, or --repo and --pr")
        pr = GitHubClient(token=s.github_token).get_json(f"/repos/{a.repo}/pulls/{a.pr}")
        text, key = f"{pr.get('title', '')}\n{pr.get('body') or ''}", f"{a.repo}#pull_request:{a.pr}"
    hits = find_relevant(db, text, repo=a.repo, exclude_source_key=key, min_terms=a.min_terms, include_unverified=a.include_unverified)
    print(f"{len(hits)} potentially relevant stored decision(s) (lexical match, conservative)")
    for h in hits:
        print_decision(h["decision"])
        print(f"    why relevant: {h['why']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="decisiontrace", description="Evidence-grounded engineering decision extraction (local LLM).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-model").set_defaults(fn=cmd_check_model)
    p = sub.add_parser("analyze", help="extract decisions from pasted text (no GitHub needed)")
    p.add_argument("file", nargs="?")
    p.add_argument("--demo", action="store_true", help="use the built-in fictional demo discussion")
    p.add_argument("--repo")
    p.add_argument("--save", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("import", help="import a public GitHub repo (issues, PRs, comments, reviews)")
    p.add_argument("repo")
    p.add_argument("--max-items", type=int, default=20)
    p.add_argument("--state", default="all", choices=["all", "open", "closed"])
    p.add_argument("--no-extract", action="store_true", help="only store sources; no model needed")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("search")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--repo")
    p.add_argument("--status")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--verified-only", action="store_true")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("pr-context")
    p.add_argument("--repo")
    p.add_argument("--pr", type=int)
    p.add_argument("--text-file")
    p.add_argument("--min-terms", type=int, default=2)
    p.add_argument("--include-unverified", action="store_true")
    p.set_defaults(fn=cmd_pr_context)
    a = ap.parse_args(argv)
    s = Settings.from_env()
    try:
        return a.fn(a, s) or 0
    except (ProviderError, ExtractionError, GitHubError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
