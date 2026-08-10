with
    current_stock as (
        select *
        from {{ ref('int_current_inventory') }}
    )

    , inventory_demand as (
        select *
        from {{ ref('int_inventory_demand_metrics') }}
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
                inventory_demand.sales_quantity_last_30_days
                , cast(0 as decimal(14, 3))
            ) as sales_quantity_last_30_days

            , coalesce(
                inventory_demand.sales_quantity_last_60_days
                , cast(0 as decimal(14, 3))
            ) as sales_quantity_last_60_days

            , coalesce(
                inventory_demand.sales_quantity_last_90_days
                , cast(0 as decimal(14, 3))
            ) as sales_quantity_last_90_days

            , coalesce(
                inventory_demand.average_daily_sales_30d
                , cast(0 as decimal(14, 3))
            ) as average_daily_sales_30d

            , coalesce(
                inventory_demand.average_daily_sales_60d
                , cast(0 as decimal(14, 3))
            ) as average_daily_sales_60d

            , coalesce(
                inventory_demand.average_daily_sales_90d
                , cast(0 as decimal(14, 3))
            ) as average_daily_sales_90d

            , coalesce(
                inventory_demand.days_with_sales_last_30_days
                , 0
            ) as days_with_sales_last_30_days

            , coalesce(
                inventory_demand.days_with_sales_last_60_days
                , 0
            ) as days_with_sales_last_60_days

            , coalesce(
                inventory_demand.days_with_sales_last_90_days
                , 0
            ) as days_with_sales_last_90_days

            , inventory_demand.last_sale_date
            , inventory_demand.inventory_analysis_date

            , case
                when inventory_demand.average_daily_sales_30d > 0
                    then cast(
                        current_stock.quantity_on_hand
                        / inventory_demand.average_daily_sales_30d
                        as decimal(14, 2)
                    )
            end as inventory_coverage_days_30d

            , case
                when inventory_demand.average_daily_sales_60d > 0
                    then cast(
                        current_stock.quantity_on_hand
                        / inventory_demand.average_daily_sales_60d
                        as decimal(14, 2)
                    )
            end as inventory_coverage_days_60d

            , case
                when inventory_demand.average_daily_sales_90d > 0
                    then cast(
                        current_stock.quantity_on_hand
                        / inventory_demand.average_daily_sales_90d
                        as decimal(14, 2)
                    )
            end as inventory_coverage_days_90d

            , {{ classify_inventory_health(
                'current_stock.quantity_on_hand',
                'inventory_demand.average_daily_sales_90d'
            ) }} as inventory_health_status

            , case
                when current_stock.quantity_on_hand = 0
                    then true

                when
                    inventory_demand.average_daily_sales_90d > 0
                    and (
                        current_stock.quantity_on_hand
                        / inventory_demand.average_daily_sales_90d
                    ) <= 7
                    then true

                else false
            end as is_stockout_risk

        from current_stock

        left join inventory_demand
            on
                current_stock.product_variant_id
                = inventory_demand.product_variant_id
                and current_stock.location_id
                = inventory_demand.location_id
    )

select *
from current_inventory
