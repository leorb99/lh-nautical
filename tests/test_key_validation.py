CATALOG_SCHEMA = "dev_natalia_correia_lh_nautical.bronze"

"""
CP2 - Pipeline Validation

Bronze Layer Data Quality Tests

Validates:
- Not Null constraints
- Uniqueness constraints

These tests are designed to run through the project's automated
testing framework using Pytest and Databricks Connect.
"""

def test_orders_id_not_null(spark):
    df = spark.table(f"{CATALOG_SCHEMA}.orders")

    null_count = df.filter("id IS NULL").count()

    assert null_count == 0, f"orders.id has {null_count} null values"


def test_orders_id_unique(spark):
    df = spark.table(f"{CATALOG_SCHEMA}.orders")

    duplicate_count = (
        df.groupBy("id")
        .count()
        .filter("count > 1")
        .count()
    )

    assert duplicate_count == 0, f"orders.id has {duplicate_count} duplicated values"

