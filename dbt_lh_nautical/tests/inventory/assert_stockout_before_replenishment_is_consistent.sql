select
    product_variant_id
    , location_id
    , quantity_on_hand
    , demand_during_lead_time
    , average_daily_sales_90d
    , will_stockout_before_replenishment

from {{ ref('fct_inventory_replenishment') }}

where
    (
        average_daily_sales_90d > 0
        and quantity_on_hand < demand_during_lead_time
        and will_stockout_before_replenishment = false
    )

    or (
        (
            average_daily_sales_90d <= 0
            or quantity_on_hand >= demand_during_lead_time
        )
        and will_stockout_before_replenishment = true
    )
