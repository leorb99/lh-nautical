select
    product_variant_id
    , location_id
    , recommended_stock_target
    , quantity_on_hand
    , open_purchase_order_quantity
    , suggested_reorder_quantity

from {{ ref('fct_inventory_replenishment') }}

where abs(
    suggested_reorder_quantity
    - greatest(
        recommended_stock_target
        - quantity_on_hand
        - open_purchase_order_quantity,
        0
    )
) > 0.001
