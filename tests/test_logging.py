from __future__ import annotations

import json

from evebench.observability import (
    LoggingConfig,
    configure_logging,
    get_logger,
    log_context,
)


def test_logging_writes_contextual_json_and_readable_console(
    tmp_path, capsys
) -> None:
    log_path = tmp_path / "evebench.jsonl"
    configure_logging(
        LoggingConfig(
            file_path=log_path,
            file_level="DEBUG",
            console_level="INFO",
            console_enabled=True,
            max_bytes=10_000,
            backup_count=1,
        )
    )
    logger = get_logger("test")

    with log_context(run_id=7, case_id="db:1", checker_id="stub"):
        logger.debug(
            "backend detail",
            extra={
                "event": "test.detail",
                "duration_ms": 12,
                "connection_string": "sqlite:///secret.db",
            },
        )
        logger.info("case complete", extra={"event": "test.complete"})
    logger.info(
        "explicit context",
        extra={"event": "test.explicit", "run_id": 8},
    )

    console = capsys.readouterr().err
    records = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]

    assert "case complete" in console
    assert "backend detail" not in console
    assert "run_id=7" in console
    assert records[0]["event"] == "test.detail"
    assert records[0]["run_id"] == "7"
    assert records[0]["case_id"] == "db:1"
    assert records[0]["duration_ms"] == 12
    assert records[0]["connection_string"] == "[REDACTED]"
    assert records[2]["run_id"] == 8

    configure_logging(
        LoggingConfig(file_path=None, console_enabled=False)
    )
