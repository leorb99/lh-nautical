with
    valid_purchase_receipts as (
        select lead_time_days
        from {{ ref('int_purchase_order_receipts') }}
        where lead_time_days is not null
    )

    , global_lead_time_metrics as (
        select
            percentile_approx(
                lead_time_days
                , 0.5
            ) as median_global_lead_time_days

            , cast(
                avg(lead_time_days) as decimal(14, 2)
            ) as average_global_lead_time_days

            , min(lead_time_days) as minimum_global_lead_time_days
            , max(lead_time_days) as maximum_global_lead_time_days
            , count(*) as global_lead_time_observation_count

        from valid_purchase_receipts
    )

select *
from global_lead_time_metrics
