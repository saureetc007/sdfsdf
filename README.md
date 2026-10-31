# DecisionTrace

Recover engineering decisions from GitHub issues/PRs (or pasted text) with a **local** LLM, and keep every decision tied to
verbatim evidence from the original discussion. No API keys, no cloud inference. Standard-library Python only (3.10+).

> **Status:** built and unit-tested in a sandbox with no Ollama, no network and no GPU. The extraction pipeline, evidence
> verifier, ingestion, database, search and PR-context code are tested with mocks and a stub HTTP server.
> **Real local-model inference and live GitHub ingestion have NOT been verified yet** - see "Verify on your machine".

## How it works
```
text / GitHub thread -> SourceDoc (trusted metadata from API only)
  -> DecisionExtractor: prompt (untrusted text fenced) -> ModelProvider (Ollama) -> JSON -> schema validation (retry once)
  -> verifier: each quoted excerpt must occur in the source text; unverified accepted/rejected decisions are demoted to "unresolved";
               alternatives never mentioned in the source are dropped; every failure is recorded
  -> SQLite (repositories, sources, extraction_runs, decisions, evidence, alternatives, validation_outcomes) + FTS5 search
```
Badges: **VERIFIED SOURCE EXCERPT** (quote found in source; this proves the text exists, *not* that it logically supports the claim),
**PARTIAL / UNCERTAIN INTERPRETATION**, **UNSUPPORTED CLAIM**, **MISSING EVIDENCE**. If the model runtime is down you get a setup
error - never regex fallback, never fabricated output.

## Setup
```bash
# 1. Python 3.10+ ; no pip dependencies needed
cp .env.example .env            # optional; placeholders only, .env is git-ignored

# 2. Local model runtime (https://ollama.com/download)
ollama serve &                  # desktop app starts it automatically
ollama pull qwen2.5:7b-instruct # ~4.7 GB, wants ~8 GB RAM. Low RAM: ollama pull qwen2.5:3b-instruct
                                #   and set DECISIONTRACE_MODEL=qwen2.5:3b-instruct
python -m decisiontrace check-model
```
Model choice is a default, not a benchmark result: availability and quality were not verifiable offline. Change it freely via `DECISIONTRACE_MODEL`.

## Use
```bash
python -m decisiontrace analyze --demo                    # fictional demo text, real inference, no GitHub needed
python -m decisiontrace analyze notes.txt --save --repo me/proj
cat thread.txt | python -m decisiontrace analyze - --json
python -m decisiontrace import owner/repo --max-items 20  # public repo, unauthenticated (60 requests/hour!)
python -m decisiontrace import owner/repo --no-extract    # store sources only, no model needed
python -m decisiontrace search "job queue" --repo owner/repo --status accepted --verified-only
python -m decisiontrace pr-context --repo owner/repo --pr 123      # or --text-file pr.txt
```
Each imported thread costs 1-3 API requests; set `GITHUB_TOKEN` for higher limits. Rate limits abort the import visibly with the reset time.

## Tests
```bash
python -m unittest discover -s tests -t . -v                      # mocked / stubbed, no model or network needed
DECISIONTRACE_LIVE=1 python -m unittest tests.integration.test_live_ollama -v   # REAL model; needs Ollama + model
```
Mocked tests check pipeline behaviour (validation, demotion, verification, persistence...) - they say nothing about model quality.

## Supported GitHub resources
Issues, pull requests, issue comments, PR reviews, PR review comments (tagged separately in the stored text).
**Not supported:** GitHub Discussions (GraphQL, needs auth), commit messages, linked external docs.

## Known limitations
- Real inference, live GitHub calls and extraction accuracy are untested (see status). Small models can miss or misclassify decisions.
- Evidence verification proves a quote exists, not that it supports the claim; reasons are not individually verified.
- Prompt-injection defences (fenced data, schema validation, evidence checks) reduce but do not eliminate risk.
- PR context is lexical (>=2 shared distinctive terms), so it can miss paraphrases; it never matches on one shared name.
- Alternatives check is token-based. Long threads are chunked (12k chars) - cross-chunk decisions may be split.
- No Streamlit UI/MCP server/GitHub App (could not be tested here). No semantic search. Optional token is read from env only.

## Troubleshooting
`Local model runtime not reachable` -> start Ollama / fix `OLLAMA_BASE_URL`. `model ... not installed` -> `ollama pull <model>`.
`timed out` -> smaller model or larger `DECISIONTRACE_TIMEOUT`. `Model output failed schema validation twice` -> try a larger model.
`rate limit` -> wait for reset or set `GITHUB_TOKEN`.
