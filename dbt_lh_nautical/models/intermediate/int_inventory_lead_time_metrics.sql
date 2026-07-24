with
    purchase_order_receipts as (
        select *
        from {{ ref('int_purchase_order_receipts') }}
        where lead_time_days is not null
    )

    , aggregated_lead_times as (
        select
            product_variant_id
            , location_id

            , percentile_approx(lead_time_days, 0.5)
                as median_lead_time_days

            , cast(avg(lead_time_days) as decimal(14, 2))
                as average_lead_time_days

            , min(lead_time_days)
                as minimum_lead_time_days

            , max(lead_time_days)
                as maximum_lead_time_days

            , count(*) as lead_time_observation_count

            , count(distinct supplier_id) as supplier_count

        from purchase_order_receipts

        group by
            product_variant_id
            , location_id
    )

select *
from aggregated_lead_times
