with
    current_inventory as (
        select *
        from {{ ref('int_current_inventory') }}
    )

    , demand_metrics as (
        select *
        from {{ ref('int_inventory_demand_metrics') }}
    )

    , inventory_lead_time_metrics as (
        select *
        from {{ ref('int_inventory_lead_time_metrics') }}
    )

    , product_lead_time_metrics as (
        select *
        from {{ ref('int_product_lead_time_metrics') }}
    )

    , global_lead_time_metrics as (
        select *
        from {{ ref('int_global_lead_time_metrics') }}
    )

    , open_purchase_order_quantities as (
        select *
        from {{ ref('int_open_purchase_order_quantities') }}
    )

    -- Current inventory is the driving dataset so every existing
    -- product-variant and location record is preserved.
    , replenishment_inputs as (
        select
            current_inventory.current_inventory_snapshot_sk
            , current_inventory.product_variant_id
            , current_inventory.location_id

            , current_inventory.quantity_on_hand
            , current_inventory.reorder_point
            , current_inventory.is_out_of_stock
            , current_inventory.is_below_reorder_point
            , current_inventory.snapshot_date

            , coalesce(
                open_purchase_order_quantities.open_purchase_order_quantity,
                0
            ) as open_purchase_order_quantity

            , coalesce(
                open_purchase_order_quantities.open_purchase_order_item_count,
                0
            ) as open_purchase_order_item_count

            , demand_metrics.inventory_analysis_date
            , demand_metrics.last_sale_date

            , coalesce(
                demand_metrics.sales_quantity_last_30_days,
                0
            ) as sales_quantity_last_30_days

            , coalesce(
                demand_metrics.sales_quantity_last_60_days,
                0
            ) as sales_quantity_last_60_days

            , coalesce(
                demand_metrics.sales_quantity_last_90_days,
                0
            ) as sales_quantity_last_90_days

            , coalesce(
                demand_metrics.average_daily_sales_30d,
                0
            ) as average_daily_sales_30d

            , coalesce(
                demand_metrics.average_daily_sales_60d,
                0
            ) as average_daily_sales_60d

            , coalesce(
                demand_metrics.average_daily_sales_90d,
                0
            ) as average_daily_sales_90d

            -- Lead time falls back from product-location history to product
            -- history, global history and finally a fixed seven-day default.
            , cast(
                coalesce(
                    inventory_lead_time_metrics.median_lead_time_days,
                    product_lead_time_metrics.median_product_lead_time_days,
                    global_lead_time_metrics.median_global_lead_time_days,
                    7
                )
                as decimal(14, 2)
            ) as selected_lead_time_days

            , case
                when inventory_lead_time_metrics.median_lead_time_days
                    is not null
                    then 'product_location_history'

                when product_lead_time_metrics.median_product_lead_time_days
                    is not null
                    then 'product_history'

                when global_lead_time_metrics.median_global_lead_time_days
                    is not null
                    then 'global_history'

                else 'default_7_days'
            end as lead_time_source

            , case
                when inventory_lead_time_metrics.median_lead_time_days
                    is not null
                    then inventory_lead_time_metrics.lead_time_observation_count

                when product_lead_time_metrics.median_product_lead_time_days
                    is not null
                    then product_lead_time_metrics.product_lead_time_observation_count

                when global_lead_time_metrics.median_global_lead_time_days
                    is not null
                    then global_lead_time_metrics.global_lead_time_observation_count

                else 0
            end as lead_time_observation_count

            -- Only product-location history can receive high or medium
            -- confidence because it is the most specific replenishment history.
            , case
                when inventory_lead_time_metrics.median_lead_time_days
                    is not null
                 and inventory_lead_time_metrics.lead_time_observation_count >= 10
                    then 'high'

                when inventory_lead_time_metrics.median_lead_time_days
                    is not null
                 and inventory_lead_time_metrics.lead_time_observation_count >= 3
                    then 'medium'

                when inventory_lead_time_metrics.median_lead_time_days
                    is not null
                    then 'low'

                when product_lead_time_metrics.median_product_lead_time_days
                    is not null
                    then 'low'

                when global_lead_time_metrics.median_global_lead_time_days
                    is not null
                    then 'low'

                else 'assumed'
            end as lead_time_confidence

            , case
                when inventory_lead_time_metrics.median_lead_time_days
                    is not null
                    then false
                else true
            end as is_lead_time_estimated

            , case
                when demand_metrics.product_variant_id is null
                    then true
                else false
            end as is_demand_history_missing

        from current_inventory

        left join demand_metrics
            on current_inventory.product_variant_id
            = demand_metrics.product_variant_id
            and current_inventory.location_id
            = demand_metrics.location_id

        left join inventory_lead_time_metrics
            on current_inventory.product_variant_id
            = inventory_lead_time_metrics.product_variant_id
            and current_inventory.location_id
            = inventory_lead_time_metrics.location_id

        left join product_lead_time_metrics
            on current_inventory.product_variant_id
            = product_lead_time_metrics.product_variant_id

        -- Open purchase orders are joined at the same product-location grain
        -- and default to zero when no committed inbound quantity exists.
        left join open_purchase_order_quantities
            on current_inventory.product_variant_id
            = open_purchase_order_quantities.product_variant_id
            and current_inventory.location_id
            = open_purchase_order_quantities.location_id

        cross join global_lead_time_metrics
    )

select *
from replenishment_inputs
