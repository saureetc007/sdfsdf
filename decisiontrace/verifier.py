"""Evidence verification. Independent of the model.

What this proves: an excerpt the model quoted occurs in the source text (whitespace- and quote-style-insensitive,
case-sensitive). What it does NOT prove: that the excerpt logically supports the decision/status/reasons. That
remains an interpretation by the model and is labelled as such.
"""
import re

from .schema import EvidenceCheck, ValidationNote

MIN_EXCERPT_CHARS = 12
_STOP = {"the", "and", "for", "with", "use", "using", "not", "but", "that", "this", "from", "into", "than", "over", "our", "are", "was", "will", "should"}


def _pattern(excerpt: str):
    parts = []
    for tok in excerpt.split():
        piece = "".join(
            "['\u2018\u2019]" if ch in "'\u2018\u2019" else '["\u201c\u201d]' if ch in '"\u201c\u201d' else re.escape(ch)
            for ch in tok
        )
        parts.append(piece)
    return re.compile(r"\s+".join(parts)) if parts else None


def check_excerpt(source: str, excerpt: str) -> EvidenceCheck:
    ex = excerpt.strip()
    if len(ex) < MIN_EXCERPT_CHARS:
        return EvidenceCheck(excerpt, "too_short")
    pat = _pattern(ex)
    m = pat.search(source) if pat else None
    if not m:
        return EvidenceCheck(excerpt, "not_found")
    return EvidenceCheck(excerpt, "verified_excerpt", m.start(), m.end())


def _sig_tokens(name: str):
    return [t for t in re.findall(r"[A-Za-z0-9_.+#-]{3,}", name.casefold()) if t not in _STOP]


def verify_decisions(source: str, decisions: list):
    """Mutates decisions with verification results. Returns (decisions, notes)."""
    notes, folded = [], source.casefold()
    for d in decisions:
        d.checks = [check_excerpt(source, e) for e in d.evidence]
        ok = sum(c.state == "verified_excerpt" for c in d.checks)
        for c in d.checks:
            if c.state != "verified_excerpt":
                notes.append(ValidationNote(f"evidence_{c.state}", f"excerpt rejected: {c.excerpt[:120]!r}", d.decision))
        if not d.checks:
            d.evidence_state = "missing_evidence"
        elif ok == len(d.checks):
            d.evidence_state = "excerpt_verified"
        elif ok == 0:
            d.evidence_state = "unsupported"
        else:
            d.evidence_state = "partially_verified"
        if ok == 0 and d.status in ("accepted", "rejected"):
            notes.append(ValidationNote("status_demoted", f"'{d.status}' -> 'unresolved': no verified evidence excerpt", d.decision))
            d.flags.append(f"demoted_from_{d.status}_no_verified_evidence")
            d.status = "unresolved"
        if ok == 0 and d.status == "proposed":
            d.flags.append("proposal_without_verified_evidence")
        kept = []
        for a in d.alternatives:
            toks = _sig_tokens(a.name)
            if toks and not any(t in folded for t in toks):
                notes.append(ValidationNote("alternative_not_in_source", f"dropped alternative not mentioned in source: {a.name!r}", d.decision))
                continue
            kept.append(a)
        d.alternatives = kept
    return decisions, notes
