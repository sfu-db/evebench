"""Backend-specific schema translators used during adapter preprocessing."""

from .calcite import preprocess_calcite_case, preprocess_calcite_ddl
from .verieql import VeriEQLSchema, parse_verieql_ddl

__all__ = [
    "VeriEQLSchema",
    "parse_verieql_ddl",
    "preprocess_calcite_case",
    "preprocess_calcite_ddl",
]
