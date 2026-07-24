with
    stock_movements as (
        select *
        from {{ ref('stg_stock_movements') }}
    )

    , reference_date as (
        select
            max(cast(occurred_at as date)) as inventory_analysis_date
        from stock_movements
    )

    , sales_last_30_days as (
        select
            stock_movements.product_variant_id
            , stock_movements.location_id

            , sum(
                abs(stock_movements.stock_movement_quantity)
            ) as sales_quantity_last_30_days

            , count(distinct cast(stock_movements.occurred_at as date))
                as days_with_sales_last_30_days

            , max(cast(stock_movements.occurred_at as date))
                as last_sale_date

        from stock_movements
        cross join reference_date

        where stock_movements.stock_movement_type = 'sale_out'
          and cast(stock_movements.occurred_at as date)
              between date_sub(reference_date.inventory_analysis_date, 29)
                  and reference_date.inventory_analysis_date

        group by
            stock_movements.product_variant_id
            , stock_movements.location_id
    )

    , calculated_consumption as (
        select
            sales_last_30_days.product_variant_id
            , sales_last_30_days.location_id
            , reference_date.inventory_analysis_date
            , sales_last_30_days.sales_quantity_last_30_days
            , sales_last_30_days.days_with_sales_last_30_days
            , sales_last_30_days.last_sale_date

            -- Average demand per calendar day in the 30-day analysis window
            , cast(
                sales_last_30_days.sales_quantity_last_30_days / 30.0
                as decimal(14, 3)
            ) as average_daily_sales_30d

        from sales_last_30_days
        cross join reference_date
    )

select *
from calculated_consumption
