"""Chat model access for the desk: one interface, three providers (Nightshift, sample, Nebius fallback).

Settled 23 Oct 2026: call explicit model ids through Nightshift, never the `meterless/*` routing modes,
so every trace line names the Nemotron model that answered. Lightning for EvidenceAgent and narration,
Ultra for the DutyLead and the Verifier. Secrets come from `.env` through python-dotenv; nothing here
prints them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

LIGHTNING = "nvidia/nemotron-3.5-lightning-30b-a3b"   # fast lane: evidence summary, narration
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"           # deep lane: DutyLead, Verifier
MIN_TOKENS_LIGHTNING = 600                            # below ~500 Lightning returns an empty reply (502)
THINK_END = "</think>"

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class ChatResult(BaseModel):
    content: str
    model_requested: str
    provider_model: str | None = None   # meterless.provider_model from the response, or the provider's model field
    reasoning: str | None = None        # text before the last </think>, stripped from content
    usage: dict = Field(default_factory=dict)
    latency_ms: int = 0
    provider: str = "unknown"
    cached: bool = False


class ChatClient(Protocol):
    name: str

    def online(self) -> bool: ...

    def complete(self, messages: list[Message], model: str, max_tokens: int = 1024,
                 temperature: float = 0.2, extra: dict | None = None) -> ChatResult: ...


def strip_think(text: str) -> tuple[str, str | None]:
    """Ultra puts its reasoning before `</think>` in the content. Return (answer, reasoning)."""
    if THINK_END not in text:
        return text.strip(), None
    head, _, tail = text.rpartition(THINK_END)
    head = re.sub(r"^\s*<think>\s*", "", head)
    return tail.strip(), head.strip() or None


def request_key(messages: list[Message], model: str, extra: dict | None = None) -> str:
    blob = json.dumps({"model": model, "messages": [m.model_dump() for m in messages], "extra": extra or {}}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class SampleChat:
    """Replays recorded responses so a judge can run the desk with no keys (SAMPLE_MODE=true).

    Recordings are JSON files `<key>.json` under `root`, keyed by request_key. In record mode a live
    client answers and the response is saved. With no recording and no live client, a fixed fallback
    answer is returned and marked `cached=False, provider="sample-fallback"` so traces stay honest.
    """
    name = "sample"

    def __init__(self, root: Path, live: ChatClient | None = None):
        self.root = Path(root)
        self.live = live

    def online(self) -> bool:
        return True

    def complete(self, messages: list[Message], model: str, max_tokens: int = 1024,
                 temperature: float = 0.2, extra: dict | None = None) -> ChatResult:
        key = request_key(messages, model, extra)
        path = self.root / f"{key}.json"
        if path.exists():
            r = ChatResult.model_validate_json(path.read_text())
            r.cached = True
            return r
        if self.live is not None:
            r = self.live.complete(messages, model, max_tokens, temperature, extra)
            self.root.mkdir(parents=True, exist_ok=True)
            path.write_text(r.model_dump_json(indent=1))
            return r
        return ChatResult(content="[no recorded response for this request]", model_requested=model,
                          provider="sample-fallback")


# --- provider contract (Cursor) -----------------------------------------------------------------
# Piece B: the Nightshift provider block, built in Cursor.
# as a provider"), adapted for this desk. Contract:
#   base URL METERLESS_BASE_URL (default http://127.0.0.1:8088/v1), bearer METERLESS_RUNTIME_TOKEN,
#   headers `meterless-app: <METERLESS_APP>` and `meterless-scope: user`;
#   GET /v1/models decides online(); POST /v1/chat/completions with an explicit model id;
#   max_tokens floor MIN_TOKENS_LIGHTNING for LIGHTNING; strip_think on every reply;
#   provider_model read from response["meterless"]["provider_model"] (fallback response["model"]);
#   180 s timeout, 3 tries with backoff on 5xx and connection errors, never on 4xx; never log the token.
# Deliberate deviation from the recipe's meterless/* routing modes: every call uses an explicit
# Nemotron id so traces can prove which model answered.
import httpx

_PLACEHOLDER_TOKEN = "rt-local"


class ProviderError(RuntimeError):
    """Nightshift / Meterless failure. `code` is one of offline, auth, rate_limited,
    empty_response, timeout, server, client, bad_response."""

    def __init__(self, message: str, code: str = "provider", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class ProviderOfflineError(ProviderError):
    def __init__(self, message: str = "Nightshift is unreachable", status: int | None = None):
        super().__init__(message, code="offline", status=status)


class ProviderAuthError(ProviderError):
    def __init__(self, message: str = "Nightshift rejected the runtime token", status: int | None = 401):
        super().__init__(message, code="auth", status=status)


class ProviderRateLimitError(ProviderError):
    def __init__(self, message: str = "Nightshift rate limited the request", status: int = 429):
        super().__init__(message, code="rate_limited", status=status)


class ProviderEmptyResponseError(ProviderError):
    def __init__(self, message: str = "Lightning returned an empty reply", status: int = 502):
        super().__init__(message, code="empty_response", status=status)


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str = "Nightshift timed out", status: int | None = None):
        super().__init__(message, code="timeout", status=status)


def _normalise_base_url(url: str) -> str:
    """Accept a host, a /v1 URL, or a pasted /models|/healthz|/chat/completions path. Never /v1/v1."""
    raw = (url or "").strip()
    if not raw:
        raw = "http://127.0.0.1:8088/v1"
    if "://" not in raw:
        raw = "http://" + raw
    host, _, _frag = raw.partition("#")
    host = host.split("?", 1)[0]
    for suffix in ("/chat/completions", "/models", "/healthz"):
        if host.endswith(suffix):
            host = host[: -len(suffix)]
    host = host.rstrip("/")
    if host.endswith("/v1"):
        return host
    return host + "/v1"


def _body_snippet(response: httpx.Response) -> str:
    try:
        return (response.text or "")[:200]
    except Exception:
        return ""


def _error_from_http(response: httpx.Response) -> ProviderError:
    snippet = _body_snippet(response)
    status = response.status_code
    if status in (401, 403):
        return ProviderAuthError(f"{status} from Nightshift (unauthorized)", status=status)
    if status == 429:
        return ProviderRateLimitError(f"429 from Nightshift (rate limited)")
    if status == 502 and "empty_response" in snippet:
        return ProviderEmptyResponseError(
            "Lightning returned an empty reply (502). Raise max_tokens; the floor is MIN_TOKENS_LIGHTNING.",
        )
    if status >= 500:
        return ProviderError(f"{status} from provider: {snippet}", code="server", status=status)
    return ProviderError(f"{status} from provider: {snippet}", code="client", status=status)


class NightshiftChat:
    """OpenAI-compatible client for Meterless Nightshift (integration grade L0).

    L0: real bearer token (rt-local rejected), meterless-app and meterless-scope headers,
    GET /v1/models for liveness, POST /v1/chat/completions, 180 s timeout, typed 401/403 as
    auth rather than a silent offline. This desk sends explicit Nemotron model ids, not
    meterless/* routing modes, so each trace names the model that answered.

    Not claimed: L1 (GET /v1/meterless/classes, health event, unknown meterless/* kept for
    routing), L2 (attachments, SSE, task-to-class), L3 (3D jobs). See grade() and
    docs/evidence/provider-grade.md.
    """

    name = "nightshift"
    TIMEOUT_S = 180.0
    TRIES = 3
    INTEGRATION_LEVEL = "L0"

    def __init__(self, base_url: str, token: str, app: str = "vog-desk"):
        if not (token or "").strip() or token.strip() == _PLACEHOLDER_TOKEN:
            raise ProviderAuthError("Meterless runtime token is missing or is the rt-local placeholder")
        self.base_url = _normalise_base_url(base_url)
        self.app = app
        self.last_error: ProviderError | None = None
        self._client = httpx.Client(
            base_url=self.base_url, timeout=self.TIMEOUT_S,
            headers={"Authorization": f"Bearer {token}", "meterless-app": app, "meterless-scope": "user",
                     "Content-Type": "application/json"},
        )

    def models(self) -> list[str]:
        try:
            r = self._client.get("/models")
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Nightshift timed out on GET /models") from exc
        except httpx.TransportError as exc:
            raise ProviderOfflineError("Nightshift is unreachable") from exc
        if r.status_code >= 400:
            raise _error_from_http(r)
        try:
            payload = r.json()
        except ValueError as exc:
            raise ProviderError("Nightshift /models returned non-JSON", code="bad_response") from exc
        return [m.get("id", "") for m in payload.get("data", []) if m.get("id")]

    def online(self) -> bool:
        """True only when GET /v1/models returns at least one id.

        Unauthorized (401/403) is not treated as a live catalog: this method stays a bool
        for ChatClient, and last_error.code is 'auth' so a caller can tell it from offline.
        """
        try:
            ids = self.models()
        except ProviderError as exc:
            self.last_error = exc
            return False
        self.last_error = None
        return len(ids) > 0

    def complete(self, messages: list[Message], model: str, max_tokens: int = 1024,
                 temperature: float = 0.2, extra: dict | None = None) -> ChatResult:
        if model == LIGHTNING:
            max_tokens = max(max_tokens, MIN_TOKENS_LIGHTNING)
        body = {"model": model, "messages": [m.model_dump() for m in messages],
                "max_tokens": max_tokens, "temperature": temperature}
        # Provider pass-through, e.g. {"chat_template_kwargs": {"enable_thinking": False}}: with thinking on,
        # Lightning spends the whole budget reasoning over a long evidence table and Nightshift returns
        # 502 empty_response (seen 23 Oct 2026, even at max_tokens 8000); with it off it answers in 2 s.
        if extra:
            body.update(extra)
        last: ProviderError | None = None
        for attempt in range(self.TRIES):
            t0 = time.perf_counter()
            try:
                r = self._client.post("/chat/completions", json=body)
            except httpx.TimeoutException as exc:
                last = ProviderTimeoutError("Nightshift timed out on chat completions")
                last.__cause__ = exc
                if attempt + 1 < self.TRIES:
                    time.sleep(2 ** attempt)
                continue
            except httpx.TransportError as exc:
                last = ProviderOfflineError("Nightshift is unreachable")
                last.__cause__ = exc
                if attempt + 1 < self.TRIES:
                    time.sleep(2 ** attempt)
                continue
            if r.status_code >= 500:
                last = _error_from_http(r)
                if attempt + 1 < self.TRIES:
                    time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise _error_from_http(r)
            try:
                data = r.json()
            except ValueError as exc:
                raise ProviderError("Nightshift chat returned non-JSON", code="bad_response") from exc
            raw = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            content, reasoning = strip_think(raw)
            meterless = data.get("meterless") or {}
            provider_model = meterless.get("provider_model") or data.get("model")
            usage = data.get("usage") or {}
            return ChatResult(
                content=content, model_requested=model, provider_model=provider_model,
                reasoning=reasoning, usage=usage,
                latency_ms=int((time.perf_counter() - t0) * 1000), provider=self.name,
            )
        raise last or ProviderError(f"Nightshift request failed after {self.TRIES} tries", code="server")

    def grade(self) -> dict:
        """Self-assessment against the IMPLEMENT 'Grade my provider integration' prompt."""
        return {
            "level": self.INTEGRATION_LEVEL,
            "provider": self.name,
            "app": self.app,
            "notes": (
                "L0 text chat through Nightshift with a real token and typed auth/offline/"
                "rate_limited/empty_response/timeout errors. Explicit Nemotron ids instead of "
                "meterless/* modes (settled 23 Oct 2026). L1 classes discovery, L2 attachments, "
                "and L3 3D are not implemented."
            ),
        }
# --- end provider contract (Cursor) -------------------------------------------------------------


def open_chat(provider: str | None = None, sample_root: Path | None = None) -> ChatClient:
    """Pick the provider from the argument, else SAMPLE_MODE, else Nightshift."""
    from dotenv import load_dotenv
    load_dotenv()
    root = Path(sample_root or os.environ.get("SAMPLE_ROOT", "samples/llm"))
    sample = os.environ.get("SAMPLE_MODE", "false").lower() in ("1", "true", "yes")
    provider = provider or ("sample" if sample else "nightshift")
    if provider == "sample":
        return SampleChat(root)
    if provider == "nightshift":
        live = NightshiftChat(os.environ.get("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1"),
                              os.environ["METERLESS_RUNTIME_TOKEN"], os.environ.get("METERLESS_APP", "vog-desk"))
        return SampleChat(root, live=live) if os.environ.get("RECORD_SAMPLES") else live
    raise ValueError(f"unknown provider {provider!r}")


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, int((time.perf_counter() - t0) * 1000)
