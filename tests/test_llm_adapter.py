from __future__ import annotations

import json

import pytest

import evebench.adapters.llm as llm_module
from evebench.adapters.core import execute_lifecycle
from evebench.adapters.llm import (
    LLMAdapter,
    LLMConfig,
    extract_completion_text,
    parse_counterexample_response,
)
from evebench.domain import CheckOptions, EquivalenceCase
from evebench.domain.models import ExecutionStatus, Verdict


def _case() -> EquivalenceCase:
    return EquivalenceCase(
        query_left="SELECT a FROM t",
        query_right="SELECT a FROM t WHERE a > 0",
        schema_ddl="CREATE TABLE t (a INT);",
        dialect="sqlite",
        semantics="bag",
    )


def _completion(counterexample: str = "INSERT INTO t(a) VALUES (0);") -> str:
    return json.dumps(
        {
            "explanation": "Q2 filters the zero row.",
            "counterexample_dsp": "Insert one row with a = 0.",
            "counterexample": counterexample,
        }
    )


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def test_default_completion_request_and_result(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _FakeHTTPResponse(
            {
                "choices": [{"message": {"content": _completion()}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            }
        )

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    adapter = LLMAdapter(
        LLMConfig(
            model="compatible-model",
            base_url="http://llm.test/v1/",
            api_key="secret",
            temperature=0.2,
        )
    )

    result = execute_lifecycle(
        adapter, _case(), CheckOptions(timeout_ms=2_500)
    )

    payload = json.loads(captured["request"].data)
    assert captured["request"].full_url == "http://llm.test/v1/chat/completions"
    assert captured["request"].headers["Authorization"] == "Bearer secret"
    assert payload["max_tokens"] == 4_096
    assert payload["temperature"] == 0.2
    assert "max_completion_tokens" not in payload
    assert "Q1:SELECT a FROM t" in payload["messages"][0]["content"]
    assert captured["timeout"] == 2.5
    assert result.execution_status == ExecutionStatus.COMPLETED
    assert result.verdict == Verdict.NOT_EQUIVALENT
    assert result.witness == json.loads(_completion())
    assert result.metrics == {"prompt_tokens": 12, "completion_tokens": 8}


def test_reasoning_completion_uses_reasoning_parameters(monkeypatch) -> None:
    captured = {}

    def fake_urlopen(request, *, timeout):
        captured["payload"] = json.loads(request.data)
        return _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning_content": f"```json\n{_completion()}\n```",
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr(llm_module.urllib.request, "urlopen", fake_urlopen)
    adapter = LLMAdapter(
        LLMConfig(model="reasoning-model", reasoning_effort="high")
    )

    result = execute_lifecycle(adapter, _case(), CheckOptions())

    assert captured["payload"]["reasoning_effort"] == "high"
    assert captured["payload"]["max_completion_tokens"] == 4_096
    assert "max_tokens" not in captured["payload"]
    assert result.verdict == Verdict.NOT_EQUIVALENT


def test_extracts_content_blocks_and_responses_output() -> None:
    assert (
        extract_completion_text(
            {
                "choices": [
                    {
                        "message": {
                            "content": [{"type": "text", "text": _completion()}]
                        }
                    }
                ]
            }
        )
        == _completion()
    )
    assert (
        extract_completion_text(
            {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "reasoning": _completion(),
                        }
                    }
                ]
            }
        )
        == _completion()
    )
    assert (
        extract_completion_text(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": _completion()}
                        ],
                    }
                ]
            }
        )
        == _completion()
    )


def test_rejects_non_insert_counterexample() -> None:
    with pytest.raises(ValueError, match="only INSERT"):
        parse_counterexample_response(_completion("DELETE FROM t;"))


def test_rejects_unknown_llm_options() -> None:
    adapter = LLMAdapter(LLMConfig(model="compatible-model"))

    result = execute_lifecycle(
        adapter,
        _case(),
        CheckOptions(backend_options={"llm": {"unknown": True}}),
    )

    assert result.execution_status == ExecutionStatus.INVALID_INPUT
    assert result.reason_code == "invalid_backend_options"
