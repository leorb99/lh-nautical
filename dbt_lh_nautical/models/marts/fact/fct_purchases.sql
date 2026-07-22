with
    purchase_order_items as (
        select *
        from {{ ref('int_purchase_order_items') }}
    )

    , purchase_orders as (
        select *
        from {{ ref('int_purchase_orders') }}
    )

    , joined as (
        select
            -- keys
            purchase_order_items.purchase_order_item_id
            , purchase_order_items.purchase_order_id
            , purchase_order_items.product_variant_id
            , purchase_orders.supplier_id
            , purchase_orders.buyer_id
            , purchase_orders.destination_location_id

            -- degenerate dimensions, denormalized (safe to repeat per item: categorical/reference)
            , purchase_orders.purchase_order_number
            , purchase_orders.purchase_order_status
            , purchase_orders.purchase_order_currency
            , purchase_orders.placed_at
            , purchase_orders.purchase_order_expected_delivery_at

            -- Purchase order-level monetary totals, excluded: not additive at item grain, would be double-counted if summed across an order's items
            -- , purchase_orders.purchase_order_subtotal
            -- , purchase_orders.purchase_order_total

            -- Purchase order audit timestamps, excluded: no analytical value here, placed_at already covers the "when did this happen" need
            -- , purchase_orders.created_at
            -- , purchase_orders.updated_at

            -- measures (only item-level)
            , purchase_order_items.purchase_order_item_quantity_ordered
            , purchase_order_items.purchase_order_item_unit_cost
            , purchase_order_items.purchase_order_item_line_total

        from purchase_order_items
        left join purchase_orders
            on purchase_order_items.purchase_order_id = purchase_orders.purchase_order_id
    )

select *
from joined
