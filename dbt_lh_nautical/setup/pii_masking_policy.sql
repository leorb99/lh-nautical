-- One-time setup that enables automatic masking of PII columns via Unity Catalog
-- governed tags + an ABAC (Attribute-Based Access Control) column mask policy.
--
-- Column-level classification (data_classification: "pii" / "sensitive") is set in
-- dbt via `databricks_tags` in each model's .yml (see dim_customers.yml, dim_employees.yml,
-- dim_suppliers.yml). This script is what actually turns that classification into masking.
--
-- Run this once per catalog (each personal dev catalog, and prod) in the Databricks
-- SQL editor. It uses SQL editor widgets (":catalog_name") so you'll be prompted to
-- fill in the target catalog before running.
--
-- Requires:
--   - Account-level CREATE privilege for step 1 (governed tags are account-wide, so
--     this step only needs to run successfully once total, not once per catalog --
--     skip it if you get an "already exists" error on a later run).
--   - CREATE FUNCTION / CREATE POLICY privilege on the target catalog for steps 2-3.

-- 1. Register the governed tag key and its allowed values (account-wide, run once).
CREATE GOVERNED TAG data_classification VALUES ('pii', 'sensitive');

-- 2. Masking function: returns a fixed placeholder for any column it's applied to.
CREATE FUNCTION :catalog_name.gold.mask_pii(val STRING)
RETURNS STRING
RETURN '***MASKED***';

-- 3. Policy: any column tagged data_classification = 'pii', on any table in this
-- catalog, gets masked for every user -- no exceptions.
CREATE POLICY mask_pii_columns
ON CATALOG :catalog_name
COLUMN MASK :catalog_name.gold.mask_pii
TO `account users`
FOR TABLES
MATCH COLUMNS has_tag_value('data_classification', 'pii') AS sensitive_col
ON COLUMN sensitive_col;

-- Verify it worked:
-- select customer_id, customer_legal_name, customer_email from :catalog_name.gold.dim_customers limit 5;
-- customer_legal_name and customer_email should come back as '***MASKED***'.
