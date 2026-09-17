"""PySpark ``StructType`` materialization of the ``schema_contract`` specs.

Databricks/Spark-runtime-only module. Importing this module requires
PySpark to be installed and available — local pure-Python tests must NEVER
import this module. See ``schema_contract.py`` for the underlying
Python-native contract, which this module materializes but never redefines:
``build_chunk_source_schema``/``build_document_chunks_schema`` both go
through a single internal mapper (``_struct_type_from_fields``) driven by
``CHUNK_SOURCE_FIELDS``/``DOCUMENT_CHUNKS_FIELDS`` — there is exactly one
list of fields per contract, never a second one re-declared here.

Not executed as part of this implementation task — no SparkSession is
created or used anywhere in this module at import time.
"""

from __future__ import annotations

from pyspark.sql.types import (
    ArrayType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

from .schema_contract import CHUNK_SOURCE_FIELDS, DOCUMENT_CHUNKS_FIELDS, FieldSpec

__all__ = ["build_chunk_source_schema", "build_document_chunks_schema"]


def _spark_type_for(field: FieldSpec):
    if field.logical_type == "string":
        return StringType()
    if field.logical_type == "int":
        return IntegerType()
    if field.logical_type == "timestamp":
        return TimestampType()
    if field.logical_type == "array<string>":
        return ArrayType(StringType(), containsNull=bool(field.contains_null))
    # schema_contract.FieldSpec.__post_init__ already restricts logical_type
    # to the four known values, so this path indicates the two modules have
    # drifted apart — fail fast rather than guess a type.
    raise ValueError(
        f"Field {field.name!r}: unsupported logical_type {field.logical_type!r} "
        "has no known PySpark type mapping."
    )


def _struct_type_from_fields(fields: tuple[FieldSpec, ...]) -> StructType:
    return StructType(
        [StructField(f.name, _spark_type_for(f), nullable=f.nullable) for f in fields]
    )


def build_chunk_source_schema() -> StructType:
    """The 16-column source schema — see schema_contract.CHUNK_SOURCE_FIELDS."""
    return _struct_type_from_fields(CHUNK_SOURCE_FIELDS)


def build_document_chunks_schema() -> StructType:
    """The 17-column target schema — see schema_contract.DOCUMENT_CHUNKS_FIELDS."""
    return _struct_type_from_fields(DOCUMENT_CHUNKS_FIELDS)
