with
    goods_receipt_items as (
        select *
        from {{ ref('int_goods_receipt_items') }}
    )

    , goods_receipts as (
        select *
        from {{ ref('int_goods_receipts') }}
    )

    , purchase_order_items as (
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
            {{ dbt_utils.generate_surrogate_key(['goods_receipt_items.goods_receipt_item_id']) }} as goods_receipt_item_sk
            , goods_receipt_items.goods_receipt_item_id
            , goods_receipt_items.goods_receipt_id
            , goods_receipts.purchase_order_id
            , goods_receipt_items.purchase_order_item_id
            , goods_receipts.received_by_employee_id

            -- Descriptive attributes 'borrowed' from purchase_order_items/purchase_orders
            -- (product, supplier, destination, order number/status, and dates):
            -- thats because int_goods_receipt_items alone only has IDs, nothing to analyze by.
            , purchase_order_items.product_variant_id
            , purchase_orders.supplier_id
            , purchase_orders.destination_location_id
            , purchase_orders.purchase_order_number
            , purchase_orders.purchase_order_status
            , purchase_orders.placed_at
            , purchase_orders.purchase_order_expected_delivery_at

            -- degenerate dimensions, denormalized (safe to repeat per item: categorical/reference)
            , goods_receipts.received_at
            , goods_receipts.goods_receipt_notes

            -- Audit timestamp, excluded: no analytical value here, received_at already covers the "when" need
            -- , goods_receipts.created_at

            -- measures
            , goods_receipt_items.goods_receipt_item_quantity_received

        from goods_receipt_items
        left join goods_receipts
            on goods_receipt_items.goods_receipt_id = goods_receipts.goods_receipt_id
        left join purchase_order_items
            on goods_receipt_items.purchase_order_item_id = purchase_order_items.purchase_order_item_id
        left join purchase_orders
            on goods_receipts.purchase_order_id = purchase_orders.purchase_order_id
    )

select *
from joined
