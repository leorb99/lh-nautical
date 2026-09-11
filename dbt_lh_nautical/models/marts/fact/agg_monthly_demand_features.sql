-- Grain: one row per product and calendar month (national total, all
-- locations combined).
--
-- Business purpose:
-- Feature table for the demand-forecasting models (Checkpoint 5), at the
-- monthly/product grain Adriano's models use.
--
-- Important:
-- Products are deliberately grouped by product_name, not product_id.
-- dim_products has 500 distinct product_id but only 496 distinct
-- product_name: a handful of catalog entries are near-duplicates of the
-- same real-world product. Merging them on purpose pools their sales
-- history together, which increases volume density on an already sparse
-- signal instead of splitting it further across near-empty rows.
--
-- Months with no sale for an active product are explicitly represented
-- with quantity_sold = 0 (not simply absent), so the model sees "no
-- demand" as a real signal instead of a gap in the data.

with
    sales as (
        select *
        from {{ ref('fct_sales') }}
        where is_effective_sale = true
    )

    , products as (
        select
            product_variant_id
            , product_name
        from {{ ref('dim_products') }}
    )

    , sales_enriched as (
        select
            products.product_name
            , date_trunc('month', sales.order_date) as month_start
            , sales.order_item_quantity
        from sales
        left join products
            on sales.product_variant_id = products.product_variant_id
    )

    , monthly_sales as (
        select
            sales_enriched.product_name
            , sales_enriched.month_start
            , sum(sales_enriched.order_item_quantity) as quantity_sold
        from sales_enriched
        group by sales_enriched.product_name, sales_enriched.month_start
    )

    -- Each product gets its own month range (its first sale to its last),
    -- instead of one range shared by every product. A single global range
    -- would pad newer or discontinued products with zero-demand months
    -- outside their real active window, inflating zero-inflation for no
    -- analytical reason.
    , month_bounds as (
        select
            product_name
            , min(month_start) as min_month
            , max(month_start) as max_month
        from monthly_sales
        group by product_name
    )

    , all_months as (
        select distinct date_trunc('month', dim_dates.date_day) as month_start
        from {{ ref('dim_dates') }} as dim_dates
    )

    , month_spine as (
        select
            month_bounds.product_name
            , all_months.month_start
        from month_bounds
        cross join all_months
        where all_months.month_start between month_bounds.min_month and month_bounds.max_month
    )

    -- Every product+month now has a row, with quantity_sold defaulting to
    -- 0 wherever no sale happened that month.
    , filled_grid as (
        select
            month_spine.product_name
            , month_spine.month_start
            , coalesce(monthly_sales.quantity_sold, 0) as quantity_sold
        from month_spine
        left join monthly_sales
            on
                month_spine.product_name = monthly_sales.product_name
                and month_spine.month_start = monthly_sales.month_start
    )

    -- Lag and rolling-window features are computed on the gap-free grid, so
    -- zero-sale months are correctly reflected, not silently skipped.
    --
    -- Rolling averages/max/stddev deliberately exclude the current month
    -- (ROWS BETWEEN n PRECEDING AND 1 PRECEDING) to avoid leaking
    -- same-month information into a feature meant to represent "recent
    -- trend/spread up to, but not including, this month."
    , with_lag_and_rolling_features as (
        select
            filled_grid.*

            , lag(filled_grid.quantity_sold, 1) over (
                partition by filled_grid.product_name order by filled_grid.month_start
            ) as lag_1m

            , lag(filled_grid.quantity_sold, 2) over (
                partition by filled_grid.product_name order by filled_grid.month_start
            ) as lag_2m

            , lag(filled_grid.quantity_sold, 3) over (
                partition by filled_grid.product_name order by filled_grid.month_start
            ) as lag_3m

            , lag(filled_grid.quantity_sold, 6) over (
                partition by filled_grid.product_name order by filled_grid.month_start
            ) as lag_6m

            , lag(filled_grid.quantity_sold, 12) over (
                partition by filled_grid.product_name order by filled_grid.month_start
            ) as lag_12m

            , avg(filled_grid.quantity_sold) over (
                partition by filled_grid.product_name order by filled_grid.month_start
                rows between 3 preceding and 1 preceding
            ) as rolling_avg_3m

            , avg(filled_grid.quantity_sold) over (
                partition by filled_grid.product_name order by filled_grid.month_start
                rows between 6 preceding and 1 preceding
            ) as rolling_avg_6m

            -- Recent volatility: separates "stable" products from ones
            -- that "spike", an important signal for telling P50 and P85
            -- apart in the quantile models.
            , stddev(filled_grid.quantity_sold) over (
                partition by filled_grid.product_name order by filled_grid.month_start
                rows between 6 preceding and 1 preceding
            ) as rolling_std_6m

            -- Recent peak: rolling_avg smooths out exactly the spike that
            -- high-quantile models need to learn to anticipate.
            , max(filled_grid.quantity_sold) over (
                partition by filled_grid.product_name order by filled_grid.month_start
                rows between 6 preceding and 1 preceding
            ) as rolling_max_6m

            , max(filled_grid.quantity_sold) over (
                partition by filled_grid.product_name order by filled_grid.month_start
                rows between 12 preceding and 1 preceding
            ) as rolling_max_12m

        from filled_grid
    )

    -- Momentum: gap between the short and long rolling average shows
    -- whether demand is accelerating (positive) or slowing down (negative).
    , with_momentum as (
        select
            with_lag_and_rolling_features.*
            , with_lag_and_rolling_features.rolling_avg_3m - with_lag_and_rolling_features.rolling_avg_6m as momentum
        from with_lag_and_rolling_features
    )

    , final as (
        select
            with_momentum.product_name
            , with_momentum.month_start
            , month(with_momentum.month_start) as month_num
            , quarter(with_momentum.month_start) as quarter_num
            , with_momentum.quantity_sold

            , with_momentum.lag_1m
            , with_momentum.lag_2m
            , with_momentum.lag_3m
            , with_momentum.lag_6m
            , with_momentum.lag_12m

            , with_momentum.rolling_avg_3m
            , with_momentum.rolling_avg_6m
            , with_momentum.rolling_std_6m
            , with_momentum.rolling_max_6m
            , with_momentum.rolling_max_12m
            , with_momentum.momentum

        from with_momentum
    )

select *
from final
