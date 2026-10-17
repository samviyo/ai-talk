"""Offline tests for the bots: junk parsing, retries, env wiring, behaviour."""

from __future__ import annotations

import json

import pytest

from alibi.bots import naive


class _FakeResponse:
    def __init__(self, text):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class _FlakyClient:
    """chat.completions.create fails N times with a retryable error, then returns text."""

    def __init__(self, failures, text):
        self.failures = failures
        self.remaining = failures
        self.calls = 0
        self.text = text

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kw):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise naive.APIConnectionError(message="boom", request=None)
        return _FakeResponse(self.text)


def test_naive_returns_raw_text_on_junk(monkeypatch):
    monkeypatch.setattr(naive, "_get_client", lambda: _FlakyClient(0, "no json here"))
    out = naive.audit({"task": "t", "transcript": [], "claims": []})
    assert out == "no json here"


def test_naive_parses_lenient_json(monkeypatch):
    payload = {"verdicts": {"c1": "achieved"}, "receipts": {"c1": ["R-1"]}}
    for text in (
        json.dumps(payload),
        f"Sure! Here is the audit:\n```json\n{json.dumps(payload)}\n```",
        "prefix junk " + json.dumps(payload),
    ):
        client = _FlakyClient(0, text)
        monkeypatch.setattr(naive, "_get_client", lambda c=client: c)
        assert naive.audit({"task": "t", "transcript": [], "claims": []}) == payload


def test_naive_never_raises_on_partial_or_non_dict_json(monkeypatch):
    client = _FlakyClient(0, json.dumps([1, 2, 3]))
    monkeypatch.setattr(naive, "_get_client", lambda c=client: c)
    out = naive.audit({"task": "t", "transcript": [], "claims": []})
    assert out == json.dumps([1, 2, 3])  # non-dict JSON falls back to raw text


def test_naive_retries_transient_errors_three_times(monkeypatch):
    payload = {"verdicts": {"c1": "unknown"}, "receipts": {"c1": []}}
    client = _FlakyClient(3, json.dumps(payload))
    monkeypatch.setattr(naive, "_get_client", lambda c=client: c)
    monkeypatch.setattr(naive.time, "sleep", lambda s: None)
    assert naive.audit({"task": "t", "transcript": [], "claims": []}) == payload
    assert client.calls == 4  # 3 failures + 1 success


def test_naive_raises_after_fourth_transient_error(monkeypatch):
    client = _FlakyClient(9, "unused")
    monkeypatch.setattr(naive, "_get_client", lambda c=client: c)
    monkeypatch.setattr(naive.time, "sleep", lambda s: None)
    with pytest.raises(naive.APIConnectionError):
        naive.audit({"task": "t", "transcript": [], "claims": []})
    assert client.calls == 4  # 1 + 3 retries


def test_naive_env_wiring(monkeypatch):
    seen = {}

    class _ProbeOpenAI:
        def __init__(self, **kw):
            seen.update(kw)
            self.chat = self
            self.completions = self

        def create(self, *, model, messages, **kw):
            seen["model"] = model
            seen["messages"] = messages
            return _FakeResponse(json.dumps({"verdicts": {}, "receipts": {}}))

    monkeypatch.setenv("BIFROST_BASE_URL", "https://gw.example.com/")
    monkeypatch.setenv("BIFROST_API_KEY", "k")
    monkeypatch.delenv("ALIBI_MODEL", raising=False)
    monkeypatch.setattr(naive, "OpenAI", _ProbeOpenAI)
    naive._client = None
    try:
        naive.audit(
            {
                "task": "t",
                "transcript": [{"receipt": "R-1", "kind": "say", "role": "agent", "text": "done"}],
                "claims": [{"id": "c1", "text": "It was done."}],
            }
        )
    finally:
        naive._client = None
    assert seen["base_url"] == "https://gw.example.com/v1"
    assert seen["api_key"] == "k"
    assert seen["model"] == "gpt-4o-mini"
    assert seen["messages"][0]["role"] == "system"


def test_naive_prompt_is_deliberately_naive():
    source = (naive.USER_TEMPLATE, naive.SYSTEM)
    text = " ".join(source).lower()
    for forbidden in ("trap", "retry", "injection", "untrusted", "minimal", "undo"):
        assert forbidden not in text, forbidden


def test_all_achieved_cites_every_say():
    from alibi.bots import all_achieved

    inp = {
        "claims": [{"id": "c1", "text": "a"}, {"id": "c2", "text": "b"}],
        "transcript": [
            {"receipt": "R-1", "kind": "say", "role": "agent", "text": "all done"},
            {"receipt": "R-2", "kind": "result", "tool": "t", "status": "ok", "text": "ok", "call": "R-0"},
            {"receipt": "R-3", "kind": "say", "role": "agent", "text": "really"},
        ],
    }
    out = all_achieved.audit(inp)
    assert out["verdicts"] == {"c1": "achieved", "c2": "achieved"}
    assert out["receipts"]["c1"] == ["R-1", "R-3"]


def test_all_not_achieved_marks_everything():
    from alibi.bots import all_not_achieved

    inp = {
        "claims": [{"id": "c1", "text": "a"}],
        "transcript": [{"receipt": "R-1", "kind": "say", "role": "agent", "text": "done"}],
    }
    out = all_not_achieved.audit(inp)
    assert out == {"verdicts": {"c1": "not_achieved"}, "receipts": {"c1": []}}
