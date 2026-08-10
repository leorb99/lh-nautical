with
    valid_purchase_receipts as (
        select *
        from {{ ref('int_purchase_order_receipts') }}
        where lead_time_days is not null
    )

    -- Real replenishment history: how long deliveries actually took,
    -- aggregated across every product bought from the supplier.
    , observed_lead_time as (
        select
            supplier_id

            , percentile_approx(
                lead_time_days
                , 0.5
            ) as observed_median_lead_time_days

            , cast(
                avg(lead_time_days) as decimal(14, 2)
            ) as observed_average_lead_time_days

            , count(*) as observed_lead_time_observation_count

        from valid_purchase_receipts

        group by
            supplier_id
    )

    , product_suppliers as (
        select *
        from {{ ref('bridge_product_suppliers') }}
    )

    -- Quoted lead time from the supplier catalog, averaged across every
    -- product the supplier provides. This is the only fallback needed:
    -- declared lead_time_days has no nulls in this business (validated:
    -- 1520/1520 rows populated, range 3-45 days).
    , declared_lead_time as (
        select
            supplier_id

            , cast(
                avg(lead_time_days) as decimal(14, 2)
            ) as declared_average_lead_time_days

        from product_suppliers

        group by
            supplier_id
    )

    -- Full outer join because a supplier could in principle have one
    -- side of the data without the other (purchase history without a
    -- declared catalog relationship, or vice versa for a new supplier).
    , supplier_lead_time_metrics as (
        select
            coalesce(
                observed_lead_time.supplier_id
                , declared_lead_time.supplier_id
            ) as supplier_id

            , observed_lead_time.observed_median_lead_time_days
            , observed_lead_time.observed_average_lead_time_days
            , observed_lead_time.observed_lead_time_observation_count

            , declared_lead_time.declared_average_lead_time_days

            -- Observed history reflects what actually happens and is
            -- preferred whenever it exists. The declared quote is only
            -- used as a fallback for a supplier with no receipt history
            -- yet (e.g. a newly onboarded supplier).
            , coalesce(
                observed_lead_time.observed_median_lead_time_days
                , declared_lead_time.declared_average_lead_time_days
            ) as selected_lead_time_days

            , case
                when
                    observed_lead_time.observed_median_lead_time_days
                    is not null
                    then 'observed_history'
                else 'declared_only'
            end as lead_time_source

        from observed_lead_time

        full outer join declared_lead_time
            on
                observed_lead_time.supplier_id
                = declared_lead_time.supplier_id
    )

select *
from supplier_lead_time_metrics
