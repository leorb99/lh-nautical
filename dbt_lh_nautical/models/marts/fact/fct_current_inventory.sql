with
    stock_levels as (
        select *
        from {{ ref('stg_stock_levels') }}
    )

    , inventory_consumption as (
        select *
        from {{ ref('int_inventory_consumption_30d') }}
    )

    , current_inventory as (
        select
            {{ dbt_utils.generate_surrogate_key([
                'stock_levels.product_variant_id',
                'stock_levels.location_id'
            ]) }} as current_inventory_sk

            , stock_levels.product_variant_id
            , stock_levels.location_id
            , stock_levels.stock_level_quantity_on_hand as quantity_on_hand
            , stock_levels.stock_level_reorder_point as source_reorder_point
            , cast(stock_levels.updated_at as date) as inventory_reference_date

            , coalesce(
                inventory_consumption.sales_quantity_last_30_days,
                cast(0 as decimal(14, 3))
            ) as sales_quantity_last_30_days

            , coalesce(
                inventory_consumption.average_daily_sales_30d,
                cast(0 as decimal(14, 3))
            ) as average_daily_sales_30d

            , inventory_consumption.days_with_sales_last_30_days
            , inventory_consumption.last_sale_date
            , inventory_consumption.inventory_analysis_date

            , case
                when inventory_consumption.average_daily_sales_30d > 0
                then cast(
                    stock_levels.stock_level_quantity_on_hand
                    / inventory_consumption.average_daily_sales_30d
                    as decimal(14, 2)
                )
                else null
            end as inventory_coverage_days

            , case
                when stock_levels.stock_level_quantity_on_hand = 0
                    then 'out_of_stock'

                when coalesce(
                    inventory_consumption.average_daily_sales_30d,
                    0
                ) = 0
                    then 'no_recent_sales'

                when (
                    stock_levels.stock_level_quantity_on_hand
                    / inventory_consumption.average_daily_sales_30d
                ) <= 7
                    then 'critical'

                when (
                    stock_levels.stock_level_quantity_on_hand
                    / inventory_consumption.average_daily_sales_30d
                ) <= 15
                    then 'attention'

                else 'healthy'
            end as inventory_health_status

            , case
                when stock_levels.stock_level_quantity_on_hand = 0
                    then true

                when inventory_consumption.average_daily_sales_30d > 0
                    and (
                        stock_levels.stock_level_quantity_on_hand
                        / inventory_consumption.average_daily_sales_30d
                    ) <= 7
                    then true

                else false
            end as is_stockout_risk

        from stock_levels
        left join inventory_consumption
            on stock_levels.product_variant_id
                = inventory_consumption.product_variant_id
            and stock_levels.location_id
                = inventory_consumption.location_id
    )

select *
from current_inventory
