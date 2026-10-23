"""llm.py: think-stripping, sample replay and the provider wrapper, without network."""
import json

import httpx
import pytest

from vogdesk.llm import (LIGHTNING, MIN_TOKENS_LIGHTNING, ULTRA, ChatResult, Message, NightshiftChat,
                         ProviderError, SampleChat, request_key, strip_think)


def test_strip_think_keeps_only_the_answer():
    answer, reasoning = strip_think("<think>step one\nstep two</think>\n\nThe answer is 7.")
    assert answer == "The answer is 7."
    assert reasoning == "step one\nstep two"


def test_strip_think_uses_the_last_close_tag_and_passes_plain_text():
    answer, reasoning = strip_think("a</think>b</think>final")
    assert answer == "final" and reasoning == "a</think>b"
    assert strip_think("plain") == ("plain", None)


def test_request_key_is_stable_and_model_specific():
    msgs = [Message(role="user", content="hi")]
    assert request_key(msgs, LIGHTNING) == request_key(msgs, LIGHTNING)
    assert request_key(msgs, LIGHTNING) != request_key(msgs, ULTRA)


def test_sample_chat_replays_and_marks_fallback(tmp_path):
    msgs = [Message(role="user", content="regime?")]
    key = request_key(msgs, LIGHTNING)
    (tmp_path / f"{key}.json").write_text(ChatResult(content="trade", model_requested=LIGHTNING,
                                                     provider_model=LIGHTNING, provider="nightshift").model_dump_json())
    s = SampleChat(tmp_path)
    r = s.complete(msgs, LIGHTNING)
    assert r.content == "trade" and r.cached and r.provider_model == LIGHTNING
    miss = s.complete([Message(role="user", content="other")], LIGHTNING)
    assert miss.provider == "sample-fallback" and not miss.cached


def _fake_client(handler, like: NightshiftChat | None = None):
    headers = like._client.headers if like is not None else None
    return httpx.Client(base_url="http://nightshift.test/v1", headers=headers, transport=httpx.MockTransport(handler))


def test_nightshift_strips_think_reads_provider_model_and_floors_tokens():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "meterless/auto", "meterless": {"provider_model": ULTRA},
                                         "choices": [{"message": {"content": "<think>hmm</think>0.31"}}],
                                         "usage": {"total_tokens": 42}})

    c = NightshiftChat("http://nightshift.test/v1", "secret-token", app="vog-desk")
    c._client = _fake_client(handler, like=c)
    r = c.complete([Message(role="user", content="p?")], LIGHTNING, max_tokens=50)
    assert r.content == "0.31" and r.reasoning == "hmm" and r.provider_model == ULTRA
    assert seen["body"]["max_tokens"] == MIN_TOKENS_LIGHTNING and seen["body"]["model"] == LIGHTNING
    assert seen["headers"]["meterless-app"] == "vog-desk" and seen["headers"]["meterless-scope"] == "user"
    assert seen["headers"]["authorization"] == "Bearer secret-token"


def test_nightshift_does_not_retry_4xx_but_retries_5xx(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502, json={"error": "empty_response"})
        return httpx.Response(200, json={"model": ULTRA, "choices": [{"message": {"content": "ok"}}]})

    c = NightshiftChat("http://nightshift.test/v1", "t")
    c._client = _fake_client(flaky)
    assert c.complete([Message(role="user", content="x")], ULTRA).content == "ok" and calls["n"] == 3

    c2 = NightshiftChat("http://nightshift.test/v1", "t")
    c2._client = _fake_client(lambda req: httpx.Response(401, json={"error": "bad token"}))
    with pytest.raises(ProviderError):
        c2.complete([Message(role="user", content="x")], ULTRA)
