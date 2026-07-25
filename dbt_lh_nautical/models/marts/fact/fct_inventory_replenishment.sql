with
    replenishment_inputs as (
        select *
        from {{ ref('int_inventory_replenishment_inputs') }}
    )

    -- Replenishment metrics are calculated at the product-variant
    -- and location grain inherited from the input model.
    , replenishment_calculations as (
        select
            {{ dbt_utils.generate_surrogate_key([
                'product_variant_id',
                'location_id'
            ]) }} as inventory_replenishment_sk

            , product_variant_id
            , location_id
            , current_inventory_snapshot_sk

            , quantity_on_hand
            , open_purchase_order_quantity
            , open_purchase_order_item_count
            , reorder_point as source_reorder_point
            , snapshot_date as inventory_reference_date

            , inventory_analysis_date
            , last_sale_date

            , sales_quantity_last_30_days
            , sales_quantity_last_60_days
            , sales_quantity_last_90_days

            , average_daily_sales_30d
            , average_daily_sales_60d
            , average_daily_sales_90d

            , selected_lead_time_days
            , lead_time_source
            , lead_time_observation_count
            , lead_time_confidence
            , is_lead_time_estimated
            , is_demand_history_missing

            -- Expected demand during replenishment is based on the
            -- 90-day average daily demand and the selected lead time.
            , cast(
                average_daily_sales_90d
                * selected_lead_time_days
                as decimal(14, 3)
            ) as demand_during_lead_time

            -- Safety stock represents seven additional calendar days
            -- of demand based on the 90-day average.
            , cast(
                average_daily_sales_90d
                * 7
                as decimal(14, 3)
            ) as safety_stock_quantity

        from replenishment_inputs
    )

    , replenishment_recommendations as (
        select
            *

            , cast(
                demand_during_lead_time
                + safety_stock_quantity
                as decimal(14, 3)
            ) as recommended_stock_target

            -- Open purchase orders are subtracted to avoid recommending
            -- duplicate purchases for inventory already in transit.
            , cast(
                greatest(
                    demand_during_lead_time
                    + safety_stock_quantity
                    - quantity_on_hand
                    - open_purchase_order_quantity
                    , 0
                )
                as decimal(14, 3)
            ) as suggested_reorder_quantity

            , case
                when average_daily_sales_90d > 0
                    then cast(
                        quantity_on_hand
                        / average_daily_sales_90d
                        as decimal(14, 2)
                    )
            end as estimated_days_until_stockout

            , case
                when
                    average_daily_sales_90d > 0
                    and quantity_on_hand
                    < demand_during_lead_time
                    then true
                else false
            end as will_stockout_before_replenishment

            -- Replenishment is not recommended when no valid demand
            -- exists in the 90-day demand window.
            , case
                when
                    average_daily_sales_90d > 0
                    and (
                        quantity_on_hand
                        <= coalesce(source_reorder_point, 0)

                        or (
                            quantity_on_hand
                            + open_purchase_order_quantity
                        ) < (
                            demand_during_lead_time
                            + safety_stock_quantity
                        )
                    )
                    then true
                else false
            end as is_replenishment_required

        from replenishment_calculations
    )

select *
from replenishment_recommendations
