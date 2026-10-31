"""Explicit schema for model output + validation. (Hand-written: pydantic was not installable in the build sandbox.)"""
import json
import re
from dataclasses import dataclass, field

STATUSES = ("accepted", "proposed", "rejected", "conflicting", "unresolved")

# JSON Schema handed to the model runtime for constrained output. Validation below is still authoritative.
JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision": {"type": "string"},
                    "status": {"type": "string", "enum": list(STATUSES)},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                    "alternatives": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "rejected": {"type": "boolean"},
                                "rejection_reason": {"type": ["string", "null"]},
                            },
                            "required": ["name", "rejected"],
                        },
                    },
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "unresolved_questions": {"type": "array", "items": {"type": "string"}},
                    "conflicting_positions": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["decision", "status", "evidence"],
            },
        }
    },
    "required": ["decisions"],
}


class SchemaError(ValueError):
    pass


@dataclass
class Alternative:
    name: str
    rejected: bool
    rejection_reason: str | None = None
    in_source: bool = True  # set by the verifier


@dataclass
class EvidenceCheck:
    excerpt: str
    state: str  # verified_excerpt | not_found | too_short
    start: int | None = None
    end: int | None = None


@dataclass
class Decision:
    decision: str
    status: str
    reasons: list = field(default_factory=list)
    alternatives: list = field(default_factory=list)
    evidence: list = field(default_factory=list)  # raw excerpts claimed by the model
    unresolved_questions: list = field(default_factory=list)
    conflicting_positions: list = field(default_factory=list)
    # filled by verifier
    checks: list = field(default_factory=list)
    evidence_state: str = "unchecked"  # excerpt_verified | partially_verified | missing_evidence | unsupported | unchecked
    flags: list = field(default_factory=list)

    @property
    def label(self) -> str:
        """User-facing trust label. Never claims logical support; only text-match of excerpts."""
        return {
            "excerpt_verified": "VERIFIED SOURCE EXCERPT (text match only; interpretation not machine-verified)",
            "partially_verified": "PARTIAL: some excerpts not found in source - UNCERTAIN INTERPRETATION",
            "missing_evidence": "MISSING EVIDENCE - UNSUPPORTED CLAIM",
            "unsupported": "UNSUPPORTED CLAIM - no excerpt found in source",
            "unchecked": "NOT VERIFIED",
        }[self.evidence_state]


@dataclass
class ValidationNote:
    kind: str
    detail: str
    decision: str | None = None


def _str(v, path, required=False, max_len=2000):
    if v is None and not required:
        return None
    if not isinstance(v, str):
        raise SchemaError(f"{path}: expected string, got {type(v).__name__}")
    v = v.strip()
    if required and not v:
        raise SchemaError(f"{path}: must not be empty")
    if len(v) > max_len:
        raise SchemaError(f"{path}: longer than {max_len} characters")
    return v


def _str_list(v, path):
    if v is None:
        return []
    if not isinstance(v, list):
        raise SchemaError(f"{path}: expected list")
    return [s for s in (_str(x, f"{path}[{i}]") for i, x in enumerate(v)) if s]


def validate_payload(data) -> list:
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        raise SchemaError("top level must be an object with a 'decisions' list")
    out = []
    for i, item in enumerate(data["decisions"]):
        p = f"decisions[{i}]"
        if not isinstance(item, dict):
            raise SchemaError(f"{p}: expected object")
        status = _str(item.get("status"), f"{p}.status", required=True)
        if status not in STATUSES:
            raise SchemaError(f"{p}.status: '{status}' not one of {STATUSES}")
        alts = []
        raw_alts = item.get("alternatives") or []
        if not isinstance(raw_alts, list):
            raise SchemaError(f"{p}.alternatives: expected list")
        for j, a in enumerate(raw_alts):
            if not isinstance(a, dict):
                raise SchemaError(f"{p}.alternatives[{j}]: expected object")
            rejected = a.get("rejected", False)
            if not isinstance(rejected, bool):
                raise SchemaError(f"{p}.alternatives[{j}].rejected: expected boolean")
            reason = _str(a.get("rejection_reason"), f"{p}.alternatives[{j}].rejection_reason") if rejected else None
            alts.append(Alternative(_str(a.get("name"), f"{p}.alternatives[{j}].name", required=True, max_len=300), rejected, reason or None))
        out.append(
            Decision(
                decision=_str(item.get("decision"), f"{p}.decision", required=True, max_len=1000),
                status=status,
                reasons=_str_list(item.get("reasons"), f"{p}.reasons"),
                alternatives=alts,
                evidence=_str_list(item.get("evidence"), f"{p}.evidence"),
                unresolved_questions=_str_list(item.get("unresolved_questions"), f"{p}.unresolved_questions"),
                conflicting_positions=_str_list(item.get("conflicting_positions"), f"{p}.conflicting_positions"),
            )
        )
    return out


def parse_model_output(raw: str) -> list:
    text = (raw or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if m:
        text = m.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SchemaError(f"model output is not valid JSON: {e}") from e
    return validate_payload(data)
