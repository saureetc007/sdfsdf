"""Conservative retrieval of stored decisions relevant to a pull request.

Lexical, not semantic: a decision is returned only if >= MIN_TERMS distinct non-generic terms from the PR text also
occur in the decision's own text (decision, alternatives; reasons count too). One shared technology name is never enough.
Only decisions with at least one verified source excerpt are returned unless include_unverified=True.
"""
import re

GENERIC = set("""a an and are as at be but by can do does for from has have how if in into is it its not of on or our should so that the
their then there these this to use used using was we were what when which will with without you your add adds added fix fixes fixed update
updates updated change changes changed remove removed support supports new old make makes pull request issue pr code file files test
tests testing bug feature implement implementation also just need needs want would could""".split())


def terms(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_.+#-]{2,}", text.casefold()) if t not in GENERIC}


def find_relevant(db, pr_text, repo=None, exclude_source_key=None, min_terms=2, include_unverified=False, limit=10):
    pr_terms = terms(pr_text)
    out = []
    for d in db.search("", repo=repo, limit=100000):
        if exclude_source_key and d["source_key"] == exclude_source_key:
            continue
        if not include_unverified and not any(e["state"] == "verified_excerpt" for e in d["evidence"]):
            continue
        hay = " ".join([d["decision"], *d["reasons"], *(a["name"] for a in d["alternatives"])])
        matched = sorted(pr_terms & terms(hay))
        if len(matched) >= min_terms:
            out.append({"decision": d, "matched_terms": matched, "score": len(matched),
                        "why": f"shares {len(matched)} distinct terms with this PR ({', '.join(matched)}); lexical overlap only - verify the source before relying on it"})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]
