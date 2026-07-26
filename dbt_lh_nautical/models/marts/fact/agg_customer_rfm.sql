-- Grain: one row per customer.
--
-- Business purpose:
-- RFM (Recency, Frequency, Monetary) segmentation of customers based on effective sales, 
-- powering customer segmentation and retention-targeting analysis in BI
--
-- Reference date rule:
-- Uses the latest placed_at among effective sales as "today" instead of
-- current_date(), consistent with int_inventory_demand_metrics -- avoids
-- skewed recency if the most recently ingested data isn't from today.
--
-- Scoring:
-- Each metric is scored 1-5 by quintile (ntile) across the customer base.
-- Recency is ordered so the most recent customers score 5, not 1.

with
    sales as (
        select *
        from {{ ref('fct_sales') }}
        where is_effective_sale = true
    )

    -- Order-level measures, reduced from item grain to prevent
    -- order_total_amount from being summed once per item.
    , customer_orders as (
        select
            customer_id
            , order_id
            , placed_at
            , order_total_amount
        from sales
        group by customer_id, order_id, placed_at, order_total_amount
    )

    , reference_date as (
        select max(placed_at) as analysis_date
        from customer_orders
    )

    , customer_metrics as (
        select
            customer_orders.customer_id
            , max(customer_orders.placed_at) as last_order_at
            , datediff(
                any_value(reference_date.analysis_date)
                , max(customer_orders.placed_at)
            ) as recency_days
            , count(distinct customer_orders.order_id) as frequency_orders
            , sum(customer_orders.order_total_amount) as monetary_amount
        from customer_orders
        cross join reference_date
        group by customer_orders.customer_id
    )

    , scored as (
        select
            *
            , ntile(5) over (order by recency_days desc) as recency_score
            , ntile(5) over (order by frequency_orders asc) as frequency_score
            , ntile(5) over (order by monetary_amount asc) as monetary_score
        from customer_metrics
    )

    -- Segments are evaluated in priority order: the first matching rule wins.
    , segmented as (
        select
            *
            , case
                when recency_score >= 4
                 and frequency_score >= 4
                 and monetary_score >= 4
                    then 'champions'

                when recency_score <= 2
                 and frequency_score >= 4
                 and monetary_score >= 4
                    then 'at_risk'

                when frequency_score >= 3
                 and monetary_score >= 3
                    then 'loyal'

                when recency_score >= 4
                 and frequency_score <= 2
                    then 'new'

                when recency_score <= 2
                 and frequency_score <= 2
                 and monetary_score <= 2
                    then 'hibernating'

                else 'needs_attention'
            end as customer_segment
        from scored
    )

select
    customer_id
    , last_order_at
    , recency_days
    , frequency_orders
    , monetary_amount
    , recency_score
    , frequency_score
    , monetary_score
    , customer_segment
from segmented
