-- Grain: one row per product, location, customer state, and calendar month.
--
-- Business purpose:
-- Feature table for the demand-forecasting models (Checkpoint 5), built to
-- the monthly/regional grain requested for the regression models: it splits
-- demand not just by product and selling location, but also by the state
-- the customer shipped/bought to, to support regional analysis (e.g.
-- distribution-center placement) in addition to demand forecasting.
--
-- Important:
-- Months with no sale for an active product+location+customer_state
-- combination are explicitly represented with quantity_sold = 0 (not simply
-- absent), so the model sees "no demand" as a real signal instead of a gap
-- in the data.
--
-- product_id/location_id are exposed alongside product_name/location_name
-- so any downstream grouping can key off the id instead of the name:
-- dim_products has 500 distinct product_id but only 496 distinct
-- product_name (a handful of different products share the same name), so
-- grouping by name alone would silently merge unrelated products.

with
    sales as (
        select *
        from {{ ref('fct_sales') }}
        where is_effective_sale = true
    )

    , product_variants as (
        select
            product_variant_id
            , product_id
        from {{ ref('dim_products') }}
    )

    -- product_name is identical for every variant of the same product_id
    -- (it comes from the parent product, not the variant), so this collapses
    -- cleanly to one row per product_id.
    , product_attributes as (
        select distinct
            product_id
            , product_name
        from {{ ref('dim_products') }}
    )

    , customers as (
        select
            customer_id
            , primary_address_state as customer_state
        from {{ ref('dim_customers') }}
    )

    -- Rolls sales up from variant (SKU) to product, and attaches the
    -- customer's state, before aggregating to the monthly grain.
    , sales_enriched as (
        select
            product_variants.product_id
            , sales.location_id
            , customers.customer_state
            , date_trunc('month', sales.order_date) as month_start
            , sales.order_item_quantity
        from sales
        left join product_variants
            on sales.product_variant_id = product_variants.product_variant_id
        left join customers
            on sales.customer_id = customers.customer_id
    )

    , monthly_sales as (
        select
            sales_enriched.product_id
            , sales_enriched.location_id
            , sales_enriched.customer_state
            , sales_enriched.month_start
            , sum(sales_enriched.order_item_quantity) as quantity_sold
        from sales_enriched
        group by
            sales_enriched.product_id
            , sales_enriched.location_id
            , sales_enriched.customer_state
            , sales_enriched.month_start
    )

    -- Each product+location+customer_state combination gets its own month
    -- range (its first sale to its last), instead of one range shared by
    -- every combination. A single global range would pad newer or
    -- discontinued combinations with zero-demand months outside their real
    -- active window, inflating zero-inflation for no analytical reason.
    , month_bounds as (
        select
            product_id
            , location_id
            , customer_state
            , min(month_start) as min_month
            , max(month_start) as max_month
        from monthly_sales
        group by product_id, location_id, customer_state
    )

    , all_months as (
        select distinct date_trunc('month', dim_dates.date_day) as month_start
        from {{ ref('dim_dates') }} as dim_dates
    )

    , month_spine as (
        select
            month_bounds.product_id
            , month_bounds.location_id
            , month_bounds.customer_state
            , all_months.month_start
        from month_bounds
        cross join all_months
        where all_months.month_start between month_bounds.min_month and month_bounds.max_month
    )

    -- Every product+location+customer_state+month now has a row, with
    -- quantity_sold defaulting to 0 wherever no sale happened that month.
    , filled_grid as (
        select
            month_spine.product_id
            , month_spine.location_id
            , month_spine.customer_state
            , month_spine.month_start
            , coalesce(monthly_sales.quantity_sold, 0) as quantity_sold
        from month_spine
        left join monthly_sales
            on
                month_spine.product_id = monthly_sales.product_id
                and month_spine.location_id = monthly_sales.location_id
                and month_spine.customer_state = monthly_sales.customer_state
                and month_spine.month_start = monthly_sales.month_start
    )

    -- Lag and rolling-window features are computed on the gap-free grid, so
    -- zero-sale months are correctly reflected in both lags and moving
    -- averages, not silently skipped.
    --
    -- Moving averages deliberately exclude the current month (ROWS BETWEEN
    -- n PRECEDING AND 1 PRECEDING) to avoid leaking same-month information
    -- into a feature meant to represent "recent trend up to, but not
    -- including, this month."
    , with_lag_and_rolling_features as (
        select
            filled_grid.*

            , lag(filled_grid.quantity_sold, 1) over (
                partition by filled_grid.product_id, filled_grid.location_id, filled_grid.customer_state
                order by filled_grid.month_start
            ) as lag_1m

            , lag(filled_grid.quantity_sold, 2) over (
                partition by filled_grid.product_id, filled_grid.location_id, filled_grid.customer_state
                order by filled_grid.month_start
            ) as lag_2m

            , lag(filled_grid.quantity_sold, 3) over (
                partition by filled_grid.product_id, filled_grid.location_id, filled_grid.customer_state
                order by filled_grid.month_start
            ) as lag_3m

            , lag(filled_grid.quantity_sold, 6) over (
                partition by filled_grid.product_id, filled_grid.location_id, filled_grid.customer_state
                order by filled_grid.month_start
            ) as lag_6m

            , lag(filled_grid.quantity_sold, 12) over (
                partition by filled_grid.product_id, filled_grid.location_id, filled_grid.customer_state
                order by filled_grid.month_start
            ) as lag_12m

            , avg(filled_grid.quantity_sold) over (
                partition by filled_grid.product_id, filled_grid.location_id, filled_grid.customer_state
                order by filled_grid.month_start
                rows between 3 preceding and 1 preceding
            ) as moving_avg_3m

            , avg(filled_grid.quantity_sold) over (
                partition by filled_grid.product_id, filled_grid.location_id, filled_grid.customer_state
                order by filled_grid.month_start
                rows between 6 preceding and 1 preceding
            ) as moving_avg_6m

        from filled_grid
    )

    , final as (
        select
            with_lag_and_rolling_features.product_id
            , product_attributes.product_name
            , with_lag_and_rolling_features.location_id
            , dim_locations.location_name
            , dim_locations.location_state
            , with_lag_and_rolling_features.customer_state
            , with_lag_and_rolling_features.month_start
            , with_lag_and_rolling_features.quantity_sold

            , with_lag_and_rolling_features.lag_1m
            , with_lag_and_rolling_features.lag_2m
            , with_lag_and_rolling_features.lag_3m
            , with_lag_and_rolling_features.lag_6m
            , with_lag_and_rolling_features.lag_12m

            , with_lag_and_rolling_features.moving_avg_3m
            , with_lag_and_rolling_features.moving_avg_6m

        from with_lag_and_rolling_features

        left join product_attributes
            on with_lag_and_rolling_features.product_id = product_attributes.product_id

        left join {{ ref('dim_locations') }} as dim_locations
            on with_lag_and_rolling_features.location_id = dim_locations.location_id
    )

select *
from final
