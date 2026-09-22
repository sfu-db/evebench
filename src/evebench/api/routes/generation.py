from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from evebench.api.dependencies import get_generation_service
from evebench.api.schemas import (
    CheckResultResponse,
    GenerateEvaluateRequest,
    GenerateEvaluateResponse,
    GeneratedPairResponse,
    PairEvaluationResponse,
    QualityGateResponse,
)
from evebench.application.check_service import UnknownCheckerError
from evebench.generator import (
    GenerateRequest,
    GenerationService,
    GenerationUnavailableError,
    GenerationValidationError,
)
from evebench.observability import log_context

router = APIRouter(prefix="/v1/generation", tags=["generation"])


@router.post("/generate-evaluate", response_model=GenerateEvaluateResponse)
async def generate_and_evaluate(
    request: GenerateEvaluateRequest,
    service: Annotated[GenerationService, Depends(get_generation_service)],
) -> GenerateEvaluateResponse:
    with log_context(case_id="generated"):
        try:
            result = await service.generate_and_evaluate(
                GenerateRequest(
                    schema_ddl=request.schema_ddl,
                    dialect=request.dialect,
                    semantics=request.semantics,
                    num_pairs=request.num_pairs,
                    complexity=request.complexity,
                    checker_ids=request.checker_ids,
                    options=request.options.to_domain(),
                    teacher_options=request.teacher_options,
                    include_rejected=request.include_rejected,
                )
            )
        except UnknownCheckerError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "unknown_checker",
                    "checker_ids": exc.checker_ids,
                },
            ) from exc
        except GenerationValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_generation_request", "message": str(exc)},
            ) from exc
        except GenerationUnavailableError as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "generation_unavailable", "message": str(exc)},
            ) from exc

    return GenerateEvaluateResponse(
        model=result.model,
        requested_pairs=result.requested_pairs,
        generated_pairs=result.generated_pairs,
        accepted_pairs=result.accepted_pairs,
        rejected_pairs=result.rejected_pairs,
        evaluations=[
            PairEvaluationResponse(
                index=item.index,
                pair=GeneratedPairResponse(
                    query_left=item.pair.query_left,
                    query_right=item.pair.query_right,
                    explanation=item.pair.explanation,
                    metadata=item.pair.metadata,
                ),
                quality_gate=QualityGateResponse(
                    accepted=item.quality_gate.accepted,
                    reason_code=item.quality_gate.reason_code,
                    message=item.quality_gate.message,
                ),
                results=[
                    CheckResultResponse.from_domain(result)
                    for result in item.results
                ],
            )
            for item in result.evaluations
        ],
    )
