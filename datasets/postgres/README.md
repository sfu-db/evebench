Convert the source CSVs from the repository root:

```sh
python scripts/convert_postgres_datasets.py --skip-missing-queries
```

By default, schemas are loaded from `datasets/schema/<dbid>.sql`, followed
by `<dbid>_ri.sql` if present. `tpcds.sql` and `tpcds_ri.sql` are combined
for `tpcds`; `dss.sql` maps to `tpch` (with optional `dss_ri.sql`).
The supplied `dss.sql` contains table definitions but no primary or foreign
keys. Constraints are preserved as supplied, not inferred.

The supplied SQL files cover all database IDs in the current exports:
`dsb`, `imdb_job`, `so`, `tpcds`, and `tpch`. IMDb rows are skipped. Missing
schemas for the remaining IDs raise an error with the source filename and
record number, including for incomplete query pairs skipped with
`--skip-missing-queries`.

The script combines all source CSVs into `datasets/postgres/evebench/postgres.csv`,
with columns `dbid,index,source_index,schema_ddl,q1,q2,dialect,ground_truth,source`.
The `source` column contains the original CSV filename. It maps `db_id` to `dbid`,
`val_id` to `source_index`, `original_sql`/`rewritten_sql` to `q1`/`q2`, and
`equiv=True/False` to `EQ/NEQ`. SQL query text is preserved.
The `index` column numbers the accepted output rows consecutively from 1,
after skipped and duplicate rows are removed.

`--skip-missing-queries` omits incomplete query pairs and reports their counts.
Without that flag, empty queries are errors. Missing schemas, unknown labels,
are errors. Duplicate query pairs within a source are omitted; matching pairs
from different sources remain separate. Source CSVs are preserved; reruns
replace the combined file. Labels come directly from the source exports
and are not independently verified.

Pairs whose SQL strings match after SQLGlot parses and normalizes all statements
are also skipped, regardless of their source label. Pairs that SQLGlot cannot
parse remain eligible for PostgreSQL validation.

Both queries in each remaining pair are executed against PostgreSQL. A pair is
skipped when either query raises a PostgreSQL error. The converter connects to
the server's `postgres` maintenance database and loads each dataset's DDL in a
temporary transaction. Query changes and the DDL are rolled back. The default
server address comes from
`postgresql://postgres:postgres@14-postgres-1:5432/evebench_parseval`; the
database component is replaced with `postgres` for validation, so
`evebench_parseval` need not exist. Use `--connection-string DSN` to override
the server address. Connection or DDL failures stop conversion without replacing
the output file.

Use `--schema-dir PATH`, `--input-dir PATH`, `--output-dir PATH`, or
`--output-file PATH` to override the defaults. Run benchmarks on the generated
file with the PostgreSQL dialect.
