with
    valid_purchase_receipts as (
        select *
        from {{ ref('int_purchase_order_receipts') }}
        where lead_time_days is not null
    )

    , product_lead_time_metrics as (
        select
            product_variant_id

            , percentile_approx(
                lead_time_days,
                0.5
            ) as median_product_lead_time_days

            , cast(
                avg(lead_time_days) as decimal(14, 2)
            ) as average_product_lead_time_days

            , min(lead_time_days) as minimum_product_lead_time_days
            , max(lead_time_days) as maximum_product_lead_time_days

            , count(*) as product_lead_time_observation_count

            , count(distinct location_id)
                as product_lead_time_location_count

            , count(distinct supplier_id)
                as product_lead_time_supplier_count

        from valid_purchase_receipts

        group by
            product_variant_id
    )

select *
from product_lead_time_metrics
