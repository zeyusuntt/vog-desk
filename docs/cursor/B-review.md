# Review of Cursor piece B (Nightshift provider block), 23 Oct 2026 evening PDT

Reviewed by the Claude Code build session before merging into main. Scope: the block between
`# --- provider contract (Cursor)` and `# --- end provider contract (Cursor)` in `src/vogdesk/llm.py`,
plus `docs/evidence/provider-grade.md`.

## Findings

1. Interface intact. `ChatClient`, `ChatResult`, `Message`, `strip_think`, `SampleChat`, `open_chat` and the
   constants outside the block are unchanged. `NightshiftChat(base_url, token, app)` keeps its signature.
2. Contract met: explicit Nemotron ids, `meterless-app` and `meterless-scope: user` headers, `GET /v1/models`
   for `online()`, `max_tokens` floor for Lightning, `strip_think` on every reply, `provider_model` read from
   `response["meterless"]["provider_model"]` with fallback, 180 s timeout, 3 tries with backoff on 5xx and
   transport errors, no retry on 4xx. Token never printed; `rt-local` placeholder rejected.
3. Additions that are good: typed errors (`offline`, `auth`, `rate_limited`, `empty_response`, `timeout`,
   `server`, `client`, `bad_response`), `last_error` so a caller can tell 401 from offline while `online()`
   stays a bool, `_normalise_base_url` that never produces `/v1/v1`, `grade()` reporting L0.
4. Honest grade. The write-up says L0 and explains why `meterless/*` routing is deliberately not used
   (traces must name the Nemotron model). No overclaim.
5. Minor: `import httpx` moved to module level inside the block; httpx is a declared dependency, fine.
   `ProviderError.__init__` gained `code` and `status` keyword arguments; all existing call sites pass
   a message only, so nothing breaks.

## Checks run

- `.venv/bin/python -m pytest -q tests` (Zetaris integration tests deselected): 49 passed.
- Live: `open_chat("nightshift").online()` True, `INTEGRATION_LEVEL` L0; Lightning answered in 8.6 s and
  Ultra in 4.1 s, both with `provider_model` equal to the requested Nemotron id.
- Secret scan of the two files: nothing that looks like a token or key.

## Decision

Merged as is. No session export was produced in Cursor (`docs/cursor/B-provider.md`); this review and the
grade file are the evidence of the piece.
