import pandas as pd

from scripts.datasets.shared.sampling import pair_keys, sample_pairs
from scripts.datasets.shared.features import query_features


def test_shared_sampler_uses_sqlite_and_separates_dialects():
    row = dict(index="1", dbid="db", schema_ddl="DDL", ground_truth="NEQ",
               q1="SELECT [a] FROM [t]", q2="SELECT [a] + 1 FROM [t]", dialect="sqlite")
    duplicate, _ = pair_keys(row, feature_extractor=query_features)
    assert duplicate[0] == "sqlite"
    assert 'SELECT "a" FROM "t"' in duplicate
    # SQLite brackets must parse, rather than being retained as an analysis failure.
    reduced, audit = sample_pairs(pd.DataFrame([row]), feature_extractor=query_features)
    assert audit.reason.tolist() == ["representative"]
    assert reduced.q1.tolist() == [row["q1"]]
    plain = {**row, "q1": "SELECT a FROM t", "q2": "SELECT a + 1 FROM t"}
    assert pair_keys(plain, feature_extractor=query_features)[0] != pair_keys(
        {**plain, "dialect": "mysql"}, feature_extractor=query_features,
    )[0]
