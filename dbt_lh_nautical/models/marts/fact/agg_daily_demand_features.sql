-- Grain: one row per product variant, location, and calendar day.
--
-- Business purpose:
-- Feature table for the demand-forecasting models (Checkpoint 5). Combines a
-- gap-free daily sales history per product+location with lag/rolling-window
-- features, calendar attributes, and a revenue-based ranking flag used to
-- scope the classic time-series analysis to the highest-revenue products.
--
-- Important:
-- Days with no sale for an active product+location pair are explicitly
-- represented with quantity_sold = 0 (not simply absent), so the model sees
-- "no demand" as a real signal instead of a gap in the data.

with
    sales as (
        select *
        from {{ ref('fct_sales') }}
        where is_effective_sale = true
    )

    , daily_sales as (
        select
            sales.product_variant_id
            , sales.location_id
            , sales.order_date
            , sum(sales.order_item_quantity) as quantity_sold
        from sales
        group by sales.product_variant_id, sales.location_id, sales.order_date
    )

    -- Only product+location pairs that have sold at least once are spined
    -- across the calendar. Crossing every product with every location
    -- (including pairs that never transacted) would explode row count for
    -- no analytical benefit.
    , active_combinations as (
        select distinct
            product_variant_id
            , location_id
        from daily_sales
    )

    , date_bounds as (
        select
            min(order_date) as min_date
            , max(order_date) as max_date
        from daily_sales
    )

    , date_spine as (
        select
            active_combinations.product_variant_id
            , active_combinations.location_id
            , dim_dates.date_day
        from active_combinations
        cross join {{ ref('dim_dates') }} as dim_dates
        cross join date_bounds
        where dim_dates.date_day between date_bounds.min_date and date_bounds.max_date
    )

    -- Every product+location+day now has a row, with quantity_sold defaulting
    -- to 0 wherever no sale happened that day.
    , filled_grid as (
        select
            date_spine.product_variant_id
            , date_spine.location_id
            , date_spine.date_day
            , coalesce(daily_sales.quantity_sold, 0) as quantity_sold
        from date_spine
        left join daily_sales
            on
                date_spine.product_variant_id = daily_sales.product_variant_id
                and date_spine.location_id = daily_sales.location_id
                and date_spine.date_day = daily_sales.order_date
    )

    -- Lag and rolling-window features are computed on the gap-free grid, so
    -- zero-sale days are correctly reflected in both lags and moving
    -- averages, not silently skipped.
    --
    -- Moving averages deliberately exclude the current day (ROWS BETWEEN n
    -- PRECEDING AND 1 PRECEDING) to avoid leaking same-day information into a
    -- feature meant to represent "recent trend up to, but not including,
    -- today."
    , with_lag_and_rolling_features as (
        select
            filled_grid.*

            , lag(filled_grid.quantity_sold, 1) over (
                partition by filled_grid.product_variant_id, filled_grid.location_id order by filled_grid.date_day
            ) as lag_1d

            , lag(filled_grid.quantity_sold, 7) over (
                partition by filled_grid.product_variant_id, filled_grid.location_id order by filled_grid.date_day
            ) as lag_7d

            , lag(filled_grid.quantity_sold, 30) over (
                partition by filled_grid.product_variant_id, filled_grid.location_id order by filled_grid.date_day
            ) as lag_30d

            , avg(filled_grid.quantity_sold) over (
                partition by filled_grid.product_variant_id, filled_grid.location_id order by filled_grid.date_day
                rows between 7 preceding and 1 preceding
            ) as moving_avg_7d

            , avg(filled_grid.quantity_sold) over (
                partition by filled_grid.product_variant_id, filled_grid.location_id order by filled_grid.date_day
                rows between 30 preceding and 1 preceding
            ) as moving_avg_30d

            , avg(filled_grid.quantity_sold) over (
                partition by filled_grid.product_variant_id, filled_grid.location_id order by filled_grid.date_day
                rows between 90 preceding and 1 preceding
            ) as moving_avg_90d

        from filled_grid
    )

    , product_revenue_ranking as (
        select
            product_variant_id
            , dense_rank() over (
                order by sum(order_item_net_revenue_after_returns) desc
            ) as product_revenue_rank
        from sales
        group by product_variant_id
    )

    , final as (
        select
            with_lag_and_rolling_features.product_variant_id
            , with_lag_and_rolling_features.location_id
            , with_lag_and_rolling_features.date_day
            , with_lag_and_rolling_features.quantity_sold

            , dim_locations.location_type
            , dim_products.category_name

            , dim_dates.day_week_num
            , dim_dates.month_num
            , dim_dates.is_holiday
            , dim_dates.is_business_day

            , with_lag_and_rolling_features.lag_1d
            , with_lag_and_rolling_features.lag_7d
            , with_lag_and_rolling_features.lag_30d
            , with_lag_and_rolling_features.moving_avg_7d
            , with_lag_and_rolling_features.moving_avg_30d
            , with_lag_and_rolling_features.moving_avg_90d

            , coalesce(product_revenue_ranking.product_revenue_rank, 999) <= 10 as is_top_10_product

        from with_lag_and_rolling_features

        left join {{ ref('dim_locations') }} as dim_locations
            on with_lag_and_rolling_features.location_id = dim_locations.location_id

        left join {{ ref('dim_products') }} as dim_products
            on with_lag_and_rolling_features.product_variant_id = dim_products.product_variant_id

        left join {{ ref('dim_dates') }} as dim_dates
            on with_lag_and_rolling_features.date_day = dim_dates.date_day

        left join product_revenue_ranking
            on with_lag_and_rolling_features.product_variant_id = product_revenue_ranking.product_variant_id
    )

select *
from final
