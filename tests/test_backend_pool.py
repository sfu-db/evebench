from __future__ import annotations

import operator
import os
import time

import pytest

from evebench.execution import (
    BackendProcessExpired,
    BackendProcessPool,
    BackendTaskTimeout,
)


@pytest.mark.asyncio
async def test_global_pool_executes_picklable_backend_task() -> None:
    pool = BackendProcessPool(max_workers=1, max_tasks=2)
    try:
        result = await pool.run(
            operator.add,
            args=(20, 22),
            timeout_ms=5_000,
        )
    finally:
        pool.stop()

    assert result == 42


@pytest.mark.asyncio
async def test_global_pool_replaces_timed_out_worker() -> None:
    pool = BackendProcessPool(max_workers=1, max_tasks=2)
    try:
        with pytest.raises(BackendTaskTimeout):
            await pool.run(time.sleep, args=(10,), timeout_ms=100)

        result = await pool.run(
            operator.add,
            args=(1, 2),
            timeout_ms=5_000,
        )
    finally:
        pool.stop()

    assert result == 3


@pytest.mark.asyncio
async def test_global_pool_reports_crash_and_replaces_worker() -> None:
    pool = BackendProcessPool(max_workers=1, max_tasks=2)
    try:
        with pytest.raises(BackendProcessExpired) as error:
            await pool.run(os._exit, args=(7,), timeout_ms=5_000)

        result = await pool.run(
            operator.add,
            args=(2, 3),
            timeout_ms=5_000,
        )
    finally:
        pool.stop()

    assert error.value.exitcode == 7
    assert result == 5
