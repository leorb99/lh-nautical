"""
CP2 - Bronze Layer Row Count Validation Tests

Validates that ingested Bronze tables contain records after the
ingestion process completes successfully.

This test provides an initial sanity check for data availability in
the Bronze layer. A full source-versus-Bronze reconciliation should
be implemented when automated access to source systems is available.
"""

CATALOG_SCHEMA = "dev_natalia_correia_lh_nautical.bronze"


def test_orders_bronze_has_records(spark):
    df = spark.table(f"{CATALOG_SCHEMA}.orders")

    row_count = df.count()

    assert row_count > 0, "orders Bronze table has no records"