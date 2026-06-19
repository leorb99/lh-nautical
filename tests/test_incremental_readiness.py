"""
CP2 - Incremental Processing Readiness Tests

Validates that Bronze tables contain timestamp fields that can support
future incremental ingestion strategies.

These checks verify readiness for incremental processing but do not
validate incremental behavior itself.
"""

CATALOG_SCHEMA = "dev_natalia_correia_lh_nautical.bronze"


def test_orders_has_incremental_tracking_column(spark):
    df = spark.table(f"{CATALOG_SCHEMA}.orders")

    columns = [c.lower() for c in df.columns]

    incremental_columns = [
        "updated_at",
        "created_at",
        "placed_at"
    ]

    found_columns = [
        col for col in incremental_columns
        if col in columns
    ]

    assert len(found_columns) > 0, (
        "Expected at least one incremental tracking column: updated_at, created_at or placed_at"
    )

