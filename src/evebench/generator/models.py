from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evebench.domain.models import CheckResult


@dataclass(frozen=True, slots=True)
class GeneratedPair:
    query_left: str
    query_right: str
    explanation: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    accepted: bool
    reason_code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PairEvaluation:
    index: int
    pair: GeneratedPair
    quality_gate: QualityGateResult
    results: list[CheckResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class GenerateAndEvaluateResult:
    model: str
    requested_pairs: int
    generated_pairs: int
    accepted_pairs: int
    rejected_pairs: int
    evaluations: list[PairEvaluation]
