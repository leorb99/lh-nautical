
"""
CP2 - Bronze Layer Idempotency Validation Tests

Validates that repeated ingestion executions do not generate
duplicate business records in Bronze tables.

This test supports pipeline reliability and ensures ingestion
processes remain idempotent across multiple executions.
"""

CATALOG_SCHEMA = "dev_natalia_correia_lh_nautical.bronze"


def test_orders_pipeline_idempotency_by_primary_key(spark):
    df = spark.table(f"{CATALOG_SCHEMA}.orders")

    duplicate_count = (
        df.groupBy("id")
        .count()
        .filter("count > 1")
        .count()
    )

    assert duplicate_count == 0, (
        f"Idempotency check failed: orders table has "
        f"{duplicate_count} duplicated primary keys"
    )