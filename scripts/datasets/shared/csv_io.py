"""Canonical dataset columns and atomic CSV output."""
from pathlib import Path
import tempfile

import pandas as pd

COLUMNS = ("dbid", "index", "source_index", "schema_ddl", "q1", "q2", "dialect", "ground_truth", "source")


def atomic_csv(frame: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            frame.to_csv(stream, index=False, lineterminator="\n")
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


