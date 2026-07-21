-- Overrides dbt's default schema name generation, which concatenates the
-- profile's schema with the model's +schema (e.g. "silver_silver").
-- This makes dbt use only the +schema defined in dbt_project.yml (silver or gold),
-- ignoring the profile's schema for this purpose — expected result:
-- dev_gabriel_freitas_lh_nautical.silver.stg_customers, without duplication.

{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}