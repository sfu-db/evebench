import pandas as pd

import scripts.datasets.relabel_postgres_dataset as module


def test_relabel_dataset_applies_matching_spa_labels(tmp_path):
    source = tmp_path / "postgres.csv"
    output = tmp_path / "relabeled.csv"
    label_dir = tmp_path / "labels"
    label_dir.mkdir()

    pd.DataFrame(
        [
            {
                "source": "model.csv",
                "dbid": "tpch",
                "source_index": "1",
                "ground_truth": "NEQ",
            },
            {
                "source": "model.csv",
                "dbid": "tpch",
                "source_index": "2",
                "ground_truth": "EQ",
            },
            {
                "source": "without-labels.csv",
                "dbid": "tpch",
                "source_index": "1",
                "ground_truth": "EQ",
            },
        ]
    ).to_csv(source, index=False)
    pd.DataFrame(
        [
            {
                "db_id": "tpch",
                "val_id": "1",
                "equiv": "False",
                "pg16_equiv_total": "True",
            },
            {
                "db_id": "tpch",
                "val_id": "2",
                "equiv": "False",
                "pg16_equiv_total": "",
            },
            {
                "db_id": "tpch",
                "val_id": "3",
                "equiv": "True",
                "pg16_equiv_total": "",
            },
        ]
    ).to_csv(label_dir / "model_pg16.tsv", sep="\t", index=False)

    assert module.relabel_dataset(source, label_dir, output) == (2, 1, 2, 1)
    assert pd.read_csv(output)["ground_truth"].tolist() == ["EQ", "NEQ", "EQ"]
