with
    current_stock as (
        select *
        from {{ ref('int_current_inventory') }}
    )

    , inventory_consumption as (
        select *
        from {{ ref('int_inventory_consumption_30d') }}
    )

    , current_inventory as (
        select
            {{ dbt_utils.generate_surrogate_key([
                'current_stock.product_variant_id',
                'current_stock.location_id'
            ]) }} as current_inventory_sk

            , current_stock.product_variant_id
            , current_stock.location_id
            , current_stock.quantity_on_hand
            , current_stock.reorder_point as source_reorder_point
            , current_stock.snapshot_date as inventory_reference_date

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
                    current_stock.quantity_on_hand
                    / inventory_consumption.average_daily_sales_30d
                    as decimal(14, 2)
                )
                else null
            end as inventory_coverage_days

            , case
                when current_stock.quantity_on_hand = 0
                    then 'out_of_stock'

                when coalesce(
                    inventory_consumption.average_daily_sales_30d,
                    0
                ) = 0
                    then 'no_recent_sales'

                when (
                    current_stock.quantity_on_hand
                    / inventory_consumption.average_daily_sales_30d
                ) <= 7
                    then 'critical'

                when (
                    current_stock.quantity_on_hand
                    / inventory_consumption.average_daily_sales_30d
                ) <= 15
                    then 'attention'

                else 'healthy'
            end as inventory_health_status

            , case
                when current_stock.quantity_on_hand = 0
                    then true

                when inventory_consumption.average_daily_sales_30d > 0
                    and (
                        current_stock.quantity_on_hand
                        / inventory_consumption.average_daily_sales_30d
                    ) <= 7
                    then true

                else false
            end as is_stockout_risk

        from current_stock

        left join inventory_consumption
            on current_stock.product_variant_id
                = inventory_consumption.product_variant_id
            and current_stock.location_id
                = inventory_consumption.location_id
    )

select *
from current_inventory
