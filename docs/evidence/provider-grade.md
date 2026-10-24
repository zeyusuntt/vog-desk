# Meterless provider integration grade

Scored 23 Oct 2026 against the IMPLEMENT prompt "Grade my provider integration" on meterless.ai/engines.html (Runtime 1.4, provider contract v4). Code under review is the Cursor block in `src/vogdesk/llm.py` (`NightshiftChat`).

**Claimed level: L0.** L1, L2, L2M and L3 are not implemented. That is intentional: the desk is a text-only forecast loop, and traces must name an explicit Nemotron model, not a `meterless/*` routing mode.

`NightshiftChat.grade()` and `NightshiftChat.INTEGRATION_LEVEL` return the same claim.

## Table

| Requirement | Result | Where | Notes |
|---|---|---|---|
| L0 Text: POST /v1/chat/completions | pass | `src/vogdesk/llm.py` `NightshiftChat.complete` | OpenAI-shaped body; plain text in, string out. |
| L0 Text: real bearer token; `rt-local` rejected | pass | `NightshiftChat.__init__` | Missing or `rt-local` raises `ProviderAuthError`. Token is never printed. |
| L0 Text: 401/403 shown as unauthorized, not offline | pass with a caveat | `_error_from_http`, `online` | Chat raises `ProviderAuthError` (`code="auth"`). `online()` stays a bool (ChatClient contract) and returns False, with `last_error.code == "auth"`. |
| L0 Text: `meterless/*` modes | fail (deliberate) | module comment above `NightshiftChat`; `complete` body `"model"` | Settled 23 Oct 2026: call `nvidia/nemotron-3.5-lightning-30b-a3b` and `nvidia/nemotron-3-ultra-550b-a55b`. Nightshift `meterless/fast` and `meterless/deep` reported `provider_model = meterless/auto`, which cannot prove Nemotron answered. |
| L0 headers `meterless-app`, `meterless-scope: user` | pass | `NightshiftChat.__init__` | App id defaults to `vog-desk`. |
| L0 180 s timeout | pass | `NightshiftChat.TIMEOUT_S` | Applied on the `httpx.Client`. |
| L1 Discovery: GET /v1/models | pass (partial) | `NightshiftChat.models`, `online` | Catalog liveness only. Unknown `meterless/*` ids are not kept for routing because we do not route. |
| L1 GET /v1/meterless/classes (both shapes) | fail | not present | Out of scope for a text desk. |
| L1 one health event | fail | not present | Callers poll `online()`. |
| L2 Chat: attachments, Content-Type reader, progress, task to class, `meterless-accept` | fail | not present | Text-only completions. |
| L2M Media transforms / transcribe | fail | not present | |
| L3 3D mesh/texture jobs | fail | not present | |
| Q1/Q2 Quick Connect `http://host:port/connect#k=` | fail | `_normalise_base_url` strips a fragment and does not take `k` as a token | Constructor still takes `base_url` and `token` separately from `.env`. |
| No unused 1.4 routes (`/v1/images`, speech, embeddings, videos) | pass | only GET `/models` and POST `/chat/completions` | |
| Token never in a URL | pass | Authorization header only | |
| 4xx never retried; 5xx and transport retried 3 times with backoff | pass | `complete` | 502 `empty_response` is retried then raised as `ProviderEmptyResponseError`. 429 is `ProviderRateLimitError`, not retried. Timeouts become `ProviderTimeoutError`. |

## Why L0 is the honest ceiling

The POWER recipe wants `meterless/auto` (and friends) as the model id. This product wants the opposite: every JSONL trace line must name the Nemotron model that answered, which Nightshift only reports reliably when the request already used that id (`response["meterless"]["provider_model"]`, falling back to `response["model"]`).

Raising the grade to L1 would mean a live class picker this desk would never show. L2 and L3 need media and 3D lanes this machine is not running for the forecast.

## Checks

- `tests/test_llm.py` (no network): think-stripping, sample replay, headers and Lightning `max_tokens` floor, 5xx retry vs 4xx no-retry.
- Live smoke (needs Nightshift and `.env`): `open_chat("nightshift").online()` then `complete` on Ultra.
