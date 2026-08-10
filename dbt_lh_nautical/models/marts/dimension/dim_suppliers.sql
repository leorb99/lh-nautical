with
    suppliers as (
        select *
        from {{ ref('int_suppliers') }}
    )

    , lead_time_metrics as (
        select *
        from {{ ref('int_supplier_lead_time_metrics') }}
    )

    , selected_suppliers as (
        select
            {{ dbt_utils.generate_surrogate_key(['suppliers.supplier_id']) }}
                as supplier_sk

            , suppliers.supplier_id
            , suppliers.supplier_legal_name
            , suppliers.supplier_trade_name

            , coalesce(
                suppliers.supplier_trade_name
                , suppliers.supplier_legal_name
            ) as supplier_display_name

            , suppliers.supplier_country
            , suppliers.supplier_tax_id
            , suppliers.supplier_tax_id_type
            , suppliers.supplier_email
            , suppliers.supplier_phone
            , suppliers.supplier_contact_name
            , suppliers.is_active

            , case
                when suppliers.is_active then 'active'
                else 'inactive'
            end as supplier_status

            -- Combined lead time for the "supplier ranking" dashboard
            -- card: real receiving history when it exists, the declared
            -- catalog quote otherwise. See int_supplier_lead_time_metrics
            -- for how the two sources are validated and combined.
            , lead_time_metrics.observed_median_lead_time_days
            , lead_time_metrics.observed_average_lead_time_days
            , lead_time_metrics.observed_lead_time_observation_count
            , lead_time_metrics.declared_average_lead_time_days
            , lead_time_metrics.selected_lead_time_days
            , lead_time_metrics.lead_time_source

            , suppliers.created_at
            , suppliers.updated_at
        from suppliers

        left join lead_time_metrics
            on
                suppliers.supplier_id
                = lead_time_metrics.supplier_id
    )

select *
from selected_suppliers
