-- One-time setup that enables automatic masking of PII columns via Unity Catalog
-- governed tags + an ABAC (Attribute-Based Access Control) column mask policy.
--
-- Column-level classification (data_classification: "pii" / "sensitive") is set in
-- dbt via `databricks_tags` in each model's .yml (see dim_customers.yml, dim_employees.yml,
-- dim_suppliers.yml). This script is what actually turns that classification into masking.
--
-- Run this once per catalog (each personal dev catalog, and prod) in the Databricks
-- SQL editor. It uses SQL editor widgets (":catalog_name", ":pipeline_identity") so
-- you'll be prompted to fill them in before running.
--
-- Before you run this:
--   1. Run `dbt build` at least once against your catalog first. Step 2 below creates
--      a function inside the `gold` schema, which only exists after dbt has created it.
--   2. Run each numbered step below SEPARATELY (select the block, run just that one),
--      not as a single "Run All". Step 1 is account-wide and only needs to succeed
--      once, ever, across the whole team -- if you get an "already exists" error on
--      it, that's expected and fine, just move on to step 2. If you "Run All" instead,
--      that error can stop the script before steps 2-3 ever execute.
--   3. In the `:pipeline_identity` widget, put YOUR OWN user email (the identity you
--      run dbt/tests with) if this is a personal dev catalog, or the CI/CD service
--      principal if this is prod. See the note above step 3 for why this matters.
--
-- Requires:
--   - Account-level CREATE privilege for step 1 (governed tags are account-wide).
--   - CREATE FUNCTION / CREATE POLICY privilege on the target catalog for steps 2-3.

-- 1. Register the governed tag key and its allowed values (account-wide, run once
-- ever -- expect "already exists" on every run after the first, that's fine).
CREATE GOVERNED TAG data_classification VALUES ('pii', 'sensitive');

-- 2. Masking function: returns a fixed placeholder for any column it's applied to.
-- Requires the `gold` schema to already exist (run `dbt build` first).
CREATE FUNCTION :catalog_name.gold.mask_pii(val STRING)
RETURNS STRING
RETURN '***MASKED***';

-- 3. Policy: any column tagged data_classification = 'pii' OR 'sensitive', on any
-- table in this catalog, gets masked for every user, except :pipeline_identity.
--
-- :pipeline_identity must be the identity that runs dbt/tests against this catalog
-- (your own user email in personal dev catalogs, or the CI/CD service principal in
-- prod). Without this exception, the policy also masks that identity's own queries,
-- which makes any `unique`/`accepted_values` test on a masked column always see the
-- same masked literal for every row and fail with a false-positive duplicate.
CREATE POLICY mask_pii_columns
ON CATALOG :catalog_name
COLUMN MASK :catalog_name.gold.mask_pii
TO `account users` EXCEPT `:pipeline_identity`
FOR TABLES
MATCH COLUMNS (
    has_tag_value('data_classification', 'pii')
    OR has_tag_value('data_classification', 'sensitive')
) AS sensitive_col
ON COLUMN sensitive_col;

-- Verify it worked:
-- select customer_id, customer_legal_name, customer_email from :catalog_name.gold.dim_customers limit 5;
-- select supplier_id, supplier_legal_name, supplier_tax_id from :catalog_name.gold.dim_suppliers limit 5;
-- customer_legal_name, customer_email, supplier_legal_name and supplier_tax_id should come back as '***MASKED***'
-- for anyone except :pipeline_identity, who should see the real values.

-- To update the policy later (e.g. this exact change), drop and recreate it:
-- DROP POLICY mask_pii_columns ON CATALOG :catalog_name;
