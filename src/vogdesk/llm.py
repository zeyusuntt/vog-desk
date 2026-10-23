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
                 temperature: float = 0.2) -> ChatResult: ...


def strip_think(text: str) -> tuple[str, str | None]:
    """Ultra puts its reasoning before `</think>` in the content. Return (answer, reasoning)."""
    if THINK_END not in text:
        return text.strip(), None
    head, _, tail = text.rpartition(THINK_END)
    head = re.sub(r"^\s*<think>\s*", "", head)
    return tail.strip(), head.strip() or None


def request_key(messages: list[Message], model: str) -> str:
    blob = json.dumps({"model": model, "messages": [m.model_dump() for m in messages]}, sort_keys=True)
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
                 temperature: float = 0.2) -> ChatResult:
        key = request_key(messages, model)
        path = self.root / f"{key}.json"
        if path.exists():
            r = ChatResult.model_validate_json(path.read_text())
            r.cached = True
            return r
        if self.live is not None:
            r = self.live.complete(messages, model, max_tokens, temperature)
            self.root.mkdir(parents=True, exist_ok=True)
            path.write_text(r.model_dump_json(indent=1))
            return r
        return ChatResult(content="[no recorded response for this request]", model_requested=model,
                          provider="sample-fallback")


# --- provider contract (Cursor) -----------------------------------------------------------------
# Piece B: the Nightshift provider block, built in Cursor.
# "Add Meterless as a provider" recipe. The implementation
# below is the minimal working version so the desk runs end to end in the meantime. Contract:
#   base URL METERLESS_BASE_URL (default http://127.0.0.1:8088/v1), bearer METERLESS_RUNTIME_TOKEN,
#   headers `meterless-app: <METERLESS_APP>` and `meterless-scope: user`;
#   GET /v1/models decides online(); POST /v1/chat/completions with an explicit model id;
#   max_tokens floor MIN_TOKENS_LIGHTNING for LIGHTNING; strip_think on every reply;
#   provider_model read from response["meterless"]["provider_model"] (fallback response["model"]);
#   180 s timeout, 3 tries with backoff on 5xx and connection errors, never on 4xx; never log the token.
class ProviderError(RuntimeError):
    pass


class NightshiftChat:
    name = "nightshift"
    TIMEOUT_S = 180.0
    TRIES = 3

    def __init__(self, base_url: str, token: str, app: str = "vog-desk"):
        import httpx
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url, timeout=self.TIMEOUT_S,
            headers={"Authorization": f"Bearer {token}", "meterless-app": app, "meterless-scope": "user",
                     "Content-Type": "application/json"})

    def models(self) -> list[str]:
        r = self._client.get("/models")
        r.raise_for_status()
        return [m.get("id", "") for m in r.json().get("data", [])]

    def online(self) -> bool:
        try:
            return len(self.models()) > 0
        except Exception:
            return False

    def complete(self, messages: list[Message], model: str, max_tokens: int = 1024,
                 temperature: float = 0.2) -> ChatResult:
        import httpx
        if model == LIGHTNING:
            max_tokens = max(max_tokens, MIN_TOKENS_LIGHTNING)
        body = {"model": model, "messages": [m.model_dump() for m in messages],
                "max_tokens": max_tokens, "temperature": temperature}
        last: Exception | None = None
        for attempt in range(self.TRIES):
            t0 = time.perf_counter()
            try:
                r = self._client.post("/chat/completions", json=body)
            except httpx.TransportError as e:
                last = e
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 500:
                last = ProviderError(f"{r.status_code} from provider: {r.text[:200]}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise ProviderError(f"{r.status_code} from provider: {r.text[:200]}")
            data = r.json()
            raw = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            content, reasoning = strip_think(raw)
            provider_model = (data.get("meterless") or {}).get("provider_model") or data.get("model")
            return ChatResult(content=content, model_requested=model, provider_model=provider_model,
                              reasoning=reasoning, usage=data.get("usage") or {},
                              latency_ms=int((time.perf_counter() - t0) * 1000), provider=self.name)
        raise ProviderError(f"Nightshift request failed after {self.TRIES} tries: {last}")
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
