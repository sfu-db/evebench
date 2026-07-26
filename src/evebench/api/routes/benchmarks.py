from typing import Annotated

from fastapi import APIRouter, Depends, Query

from evebench.api.dependencies import get_result_store
from evebench.benchmark import ResultStore

router = APIRouter(prefix="/v1/benchmarks", tags=["benchmarks"])


@router.get("/summary")
async def benchmark_summary(
    store: Annotated[ResultStore, Depends(get_result_store)],
    dataset: Annotated[str | None, Query()] = None,
    run_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[dict[str, object]]:
    """Return four-way outcome ratios for the latest run of each dataset."""
    return store.summary_dicts(dataset=dataset, run_id=run_id)
