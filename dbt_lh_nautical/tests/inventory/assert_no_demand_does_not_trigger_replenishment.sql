select
    product_variant_id
    , location_id
    , average_daily_sales_90d
    , is_demand_history_missing
    , suggested_reorder_quantity
    , is_replenishment_required

from {{ ref('fct_inventory_replenishment') }}

where average_daily_sales_90d = 0
  and (
      suggested_reorder_quantity <> 0
      or is_replenishment_required = true
  )
