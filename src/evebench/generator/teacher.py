from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from evebench.adapters.llm import extract_completion_text

from .models import GeneratedPair

DEFAULT_TEACHER_PROMPT = """You are a SQL benchmark teacher.
Generate SQL query pairs that are intended to be semantically equivalent and suitable for benchmarking equivalence checkers.

Dialect: {dialect}
Complexity: {complexity}
Requested pairs: {num_pairs}
Seed: {seed}

Schema DDL:
{schema}

Return strict JSON with this structure:
{
  "pairs": [
    {
      "query_left": "<SQL query>",
      "query_right": "<SQL query>",
      "explanation": "<why these should be equivalent>",
      "metadata": {"shape": "join|aggregate|subquery|predicate"}
    }
  ]
}

Rules:
- Generate exactly {num_pairs} pairs.
- Each query must be a SELECT query.
- Do not include markdown, only JSON.
"""


class TeacherInputError(ValueError):
    pass


class TeacherRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LLMTeacherConfig:
    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = field(default=None, repr=False)
    prompt_template: str = DEFAULT_TEACHER_PROMPT
    max_completion_tokens: int = 4_096
    reasoning_effort: str | None = None
    temperature: float | None = None


class LLMTeacher:
    def __init__(self, config: LLMTeacherConfig) -> None:
        self._config = config

    @property
    def model(self) -> str:
        return self._config.model

    def is_available(self) -> bool:
        return bool(self._config.model.strip() and self._config.base_url.strip())

    def generate_pairs(
        self,
        *,
        schema_ddl: str,
        dialect: str,
        num_pairs: int,
        complexity: str,
        seed: int | None,
        timeout_ms: int,
        options: dict[str, Any] | None = None,
    ) -> list[GeneratedPair]:
        if not self.is_available():
            raise TeacherRuntimeError(
                "LLM teacher is not configured; set EVEBENCH_LLMGEN_MODEL"
            )
        backend_options = dict(options or {})
        prompt_template = backend_options.pop("prompt", self._config.prompt_template)
        if not isinstance(prompt_template, str) or not prompt_template.strip():
            raise TeacherInputError("Teacher prompt must be a non-empty string")
        max_tokens = backend_options.pop(
            "max_completion_tokens", self._config.max_completion_tokens
        )
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise TeacherInputError("max_completion_tokens must be a positive integer")
        retries = backend_options.pop("retries", 0)
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0 or retries > 3:
            raise TeacherInputError("retries must be an integer between 0 and 3")
        reasoning_effort = backend_options.pop(
            "reasoning_effort", self._config.reasoning_effort
        )
        if reasoning_effort is not None and (
            not isinstance(reasoning_effort, str) or not reasoning_effort.strip()
        ):
            raise TeacherInputError("reasoning_effort must be a non-empty string or null")
        temperature = backend_options.pop("temperature", self._config.temperature)
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not 0 <= temperature <= 2
        ):
            raise TeacherInputError("temperature must be a number between 0 and 2")
        if backend_options:
            unknown = ", ".join(sorted(str(key) for key in backend_options))
            raise TeacherInputError(f"Unknown teacher option(s): {unknown}")

        prompt = _render_prompt(
            prompt_template,
            schema=schema_ddl,
            dialect=dialect,
            complexity=complexity,
            num_pairs=num_pairs,
            seed=seed,
        )
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        if reasoning_effort is None:
            payload["max_tokens"] = max_tokens
            if temperature is not None:
                payload["temperature"] = temperature
        else:
            payload["max_completion_tokens"] = max_tokens
            payload["reasoning_effort"] = reasoning_effort.strip()

        timeout_seconds = max(1.0, timeout_ms / 1000)
        errors: list[str] = []
        for _ in range(retries + 1):
            try:
                response = self._request(payload, timeout_seconds)
                content = extract_completion_text(response)
                return _parse_pairs(content, num_pairs)
            except TeacherInputError:
                raise
            except Exception as exc:  # pragma: no cover - defensive path
                errors.append(str(exc))
        raise TeacherRuntimeError(
            "Teacher generation failed after retries: " + " | ".join(errors)
        )

    def _request(self, payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._config.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise TeacherRuntimeError(f"Teacher endpoint HTTP {exc.code}: {raw}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise TeacherRuntimeError("Teacher request timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise TeacherRuntimeError("Teacher request timed out") from exc
            raise TeacherRuntimeError(f"Could not reach teacher endpoint: {exc.reason}") from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TeacherRuntimeError("Teacher endpoint did not return JSON") from exc
        if not isinstance(data, dict):
            raise TeacherRuntimeError("Teacher endpoint response must be a JSON object")
        return data

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers


def _render_prompt(
    template: str,
    *,
    schema: str,
    dialect: str,
    complexity: str,
    num_pairs: int,
    seed: int | None,
) -> str:
    required = ("{schema}", "{dialect}", "{complexity}", "{num_pairs}", "{seed}")
    missing = [value for value in required if value not in template]
    if missing:
        raise TeacherInputError("Teacher prompt missing placeholder(s): " + ", ".join(missing))
    return (
        template.replace("{schema}", schema)
        .replace("{dialect}", dialect)
        .replace("{complexity}", complexity)
        .replace("{num_pairs}", str(num_pairs))
        .replace("{seed}", "none" if seed is None else str(seed))
    )


def _parse_pairs(content: str, requested_pairs: int) -> list[GeneratedPair]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise TeacherRuntimeError("Teacher completion is not valid JSON") from exc
    if not isinstance(data, dict):
        raise TeacherRuntimeError("Teacher completion must be a JSON object")
    pairs_value = data.get("pairs")
    if not isinstance(pairs_value, list):
        raise TeacherRuntimeError("Teacher completion must include list field 'pairs'")
    if not pairs_value:
        raise TeacherRuntimeError("Teacher returned zero pairs")
    pairs: list[GeneratedPair] = []
    for item in pairs_value:
        if not isinstance(item, dict):
            raise TeacherRuntimeError("Each teacher pair must be a JSON object")
        left = item.get("query_left")
        right = item.get("query_right")
        explanation = item.get("explanation")
        metadata = item.get("metadata")
        if not isinstance(left, str) or not left.strip():
            raise TeacherRuntimeError("Teacher pair query_left must be a non-empty string")
        if not isinstance(right, str) or not right.strip():
            raise TeacherRuntimeError("Teacher pair query_right must be a non-empty string")
        if explanation is not None and not isinstance(explanation, str):
            raise TeacherRuntimeError("Teacher pair explanation must be a string when provided")
        if metadata is not None and not isinstance(metadata, dict):
            raise TeacherRuntimeError("Teacher pair metadata must be an object when provided")
        pairs.append(
            GeneratedPair(
                query_left=left,
                query_right=right,
                explanation=explanation,
                metadata=metadata if isinstance(metadata, dict) else {},
            )
        )
    return pairs[:requested_pairs]
