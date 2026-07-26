from __future__ import annotations

import argparse
from pathlib import Path

from evebench.benchmark.dataset_builder import build_bird_dataset


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Join BIRD query pairs with full per-database SQLite DDL"
    )
    parser.add_argument(
        "--baseline",
        default="datasets/sqlite/bird_baseline.csv",
    )
    parser.add_argument(
        "--schemas",
        default="datasets/sqlite/bird_dev_schema.json",
    )
    parser.add_argument(
        "--output",
        default="datasets/sqlite/bird_benchmark.csv",
    )
    args = parser.parse_args()

    count = build_bird_dataset(
        args.baseline,
        args.schemas,
        args.output,
    )
    print(f"Wrote {count} cases to {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
